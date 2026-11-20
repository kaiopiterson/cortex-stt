"""WebSocket streaming and per-session state."""
import asyncio
import logging
from dataclasses import dataclass, field
from time import monotonic
import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.core.config import Settings
from app.core.limiter import stream_client_key
from app.api.common import acquire_session, processing_metadata, validate_api_key, valid_language
from app.services.inference import (AudioDurationError, InferenceBusyError, InferenceService,
    InvalidAudioError, ModelExecutionError, ModelNotReadyError, TranscriptionResult, TranscriptionWord)

logger = logging.getLogger(__name__)
router = APIRouter()

@dataclass(slots=True)
class StreamingSession:
    settings: Settings
    language: str = "pt"
    mode: str = "stream"
    buffer: bytearray = field(default_factory=bytearray)
    committed_words: list[TranscriptionWord] = field(default_factory=list)
    provisional_words: list[TranscriptionWord] = field(default_factory=list)
    buffer_offset_samples: int = 0
    total_received_samples: int = 0
    last_inference_samples: int = 0
    committed_until_seconds: float = 0.0
    revision: int = 0

    @property
    def duration_limit(self) -> int:
        if self.mode == "utterance":
            return min(self.settings.utterance_max_seconds,
                       self.settings.max_audio_duration_seconds)
        return self.settings.stream_max_duration_seconds

    @property
    def buffered_samples(self) -> int:
        return len(self.buffer) // self.settings.sample_width_bytes

    @property
    def audio_duration_seconds(self) -> float:
        return self.total_received_samples / self.settings.sample_rate

    @property
    def confirmed_text(self) -> str:
        return "".join(w.text for w in self.committed_words).strip()

    @property
    def provisional_text(self) -> str:
        return "".join(w.text for w in self.provisional_words).strip()

    @property
    def full_text(self) -> str:
        return "".join(w.text for w in self.committed_words + self.provisional_words).strip()

    @property
    def previous_text(self) -> str | None:
        window_start = self.buffer_offset_samples / self.settings.sample_rate
        max_chars = self.settings.max_prompt_chars
        if not max_chars:
            return None
        context = "".join(w.text for w in self.committed_words if w.end <= window_start)
        return context[-max_chars:] or None

    def append_audio(self, chunk: bytes) -> None:
        if not chunk:
            return
        if len(chunk) > self.settings.stream_max_chunk_bytes:
            raise InvalidAudioError("Maximum audio chunk size exceeded")
        if len(chunk) % self.settings.sample_width_bytes:
            raise InvalidAudioError("Invalid PCM sample alignment")
        count = len(chunk) // self.settings.sample_width_bytes
        if self.total_received_samples + count > self.duration_limit * self.settings.sample_rate:
            raise AudioDurationError("Maximum streaming duration exceeded")
        self.buffer.extend(chunk)
        self.total_received_samples += count

    def discard_samples(self, samples: int) -> None:
        if samples <= 0:
            return
        samples = min(samples, self.buffered_samples)
        del self.buffer[:samples * self.settings.sample_width_bytes]
        self.buffer_offset_samples += samples

    def get_pcm(self, sample_count: int | None = None) -> np.ndarray:
        if sample_count is None:
            sample_count = self.buffered_samples
        snapshot = bytes(self.buffer[:sample_count * self.settings.sample_width_bytes])
        return np.frombuffer(snapshot, dtype="<i2").astype(np.float32) / 32768.0

    def should_infer(self) -> bool:
        return self.total_received_samples - self.last_inference_samples >= self.settings.stream_step_samples

    def _extract_words(self, result: TranscriptionResult) -> list[TranscriptionWord]:
        words = []
        for segment in result.segments:
            if segment.words:
                words.extend(segment.words)
            elif segment.text.strip():
                words.append(TranscriptionWord(start=segment.start, end=segment.end,
                                               text=segment.text, probability=0.0))
        return sorted(words, key=lambda w: (w.start, w.end))

    @staticmethod
    def _normalize_word(text: str) -> str:
        return "".join(c for c in text.casefold() if c.isalnum())

    def _is_duplicate(self, word: TranscriptionWord) -> bool:
        normalized = self._normalize_word(word.text)
        if not normalized:
            return False
        for committed in reversed(self.committed_words[-32:]):
            if committed.end < word.start - 0.5:
                break
            same_text = self._normalize_word(committed.text) == normalized
            temporal_overlap = min(committed.end, word.end) - max(committed.start, word.start)
            close_boundary = abs(committed.start - word.start) <= 0.15 and abs(committed.end - word.end) <= 0.20
            if same_text and (temporal_overlap > 0 or close_boundary):
                return True
        return False

    def apply_result(self, result: TranscriptionResult, window_end_samples: int,
                     confirmation_limit_samples: int, final: bool = False) -> None:
        window_end = window_end_samples / self.settings.sample_rate
        boundary = window_end if final else confirmation_limit_samples / self.settings.sample_rate
        provisional = []
        for word in self._extract_words(result):
            if (not np.isfinite(word.start) or not np.isfinite(word.end)
                    or word.end <= word.start or word.end > window_end + 0.1):
                continue
            if word.end <= self.committed_until_seconds or self._is_duplicate(word):
                continue
            if word.end <= boundary:
                self.committed_words.append(word)
                self.committed_until_seconds = max(self.committed_until_seconds, word.end)
            elif not final:
                provisional.append(word)
        self.provisional_words = [] if final else provisional
        self.revision += 1

    def event_payload(self, event_type: str, result: TranscriptionResult | None = None) -> dict:
        payload = {
            "type": event_type, "text": self.full_text,
            "confirmed_text": self.confirmed_text, "partial_text": self.provisional_text,
            "language": self.language, "revision": self.revision,
            "audio_duration_seconds": round(self.audio_duration_seconds, 3),
            "confirmed_until_seconds": round(self.committed_until_seconds, 3),
            "segments": [{"start": w.start, "end": w.end, "text": w.text}
                         for w in self.committed_words + self.provisional_words],
            "processing": processing_metadata(self.settings),
        }
        if result is not None:
            payload.update(processing_ms=result.processing_ms, queue_wait_ms=result.queue_wait_ms,
                           rtf=round(result.rtf, 4))
        return payload

async def process_window(session: StreamingSession, inference: InferenceService,
                         sample_count: int, confirmation_limit_samples: int,
                         final: bool = False) -> TranscriptionResult | None:
    if sample_count <= 0:
        return None
    pcm = session.get_pcm(sample_count)
    if pcm.size == 0:
        return None
    window_end = session.buffer_offset_samples + pcm.size
    result = await inference.transcribe_window(
        pcm=pcm, offset_samples=session.buffer_offset_samples,
        language=session.language, previous_text=session.previous_text)
    if result.language:
        session.language = result.language
    session.apply_result(result, window_end, confirmation_limit_samples, final)
    session.last_inference_samples = max(session.last_inference_samples, window_end)
    return result


def trim_backlog(session: StreamingSession) -> int:
    settings = session.settings
    lag = session.total_received_samples - session.last_inference_samples
    if lag <= settings.stream_max_lag_seconds * settings.sample_rate:
        return 0
    skipped = max(0, session.buffered_samples - settings.stream_window_samples)
    if skipped:
        session.discard_samples(skipped)
        session.provisional_words.clear()
    return skipped


async def advance_stream(session: StreamingSession, inference: InferenceService) -> TranscriptionResult | None:
    settings = session.settings
    window = settings.stream_window_samples
    step = settings.stream_step_samples
    margin = settings.stream_overlap_samples
    latest = None
    trim_backlog(session)
    while session.buffered_samples > window:
        end = session.buffer_offset_samples + window
        if end > session.last_inference_samples:
            latest = await process_window(session, inference, window, end - margin)
        session.discard_samples(step)
    if session.buffered_samples > 0 and session.should_infer():
        end = session.buffer_offset_samples + session.buffered_samples
        latest = await process_window(session, inference, session.buffered_samples,
                                      max(session.buffer_offset_samples, end - margin))
    return latest


async def finalize_stream(session: StreamingSession, inference: InferenceService) -> TranscriptionResult | None:
    if session.mode == "utterance":
        if not session.buffered_samples:
            return None
        result = await inference.transcribe(
            audio=session.get_pcm(), language=session.language, word_timestamps=True, priority=0
        )
        session.language = result.language
        session.apply_result(result, session.total_received_samples,
                             session.total_received_samples, final=True)
        return result
    await advance_stream(session, inference)
    if session.buffered_samples == 0:
        session.provisional_words.clear()
        return None
    end = session.buffer_offset_samples + session.buffered_samples
    return await process_window(session, inference, session.buffered_samples, end, final=True)

async def next_frame(queue, receiver, timeout):
    pending = asyncio.create_task(queue.get())
    try:
        done, _ = await asyncio.wait(
            {pending, receiver}, timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if receiver in done:
            receiver.result()
        if pending in done:
            return pending.result()
        if receiver in done:
            return await asyncio.wait_for(pending, timeout=timeout)
        raise asyncio.TimeoutError
    finally:
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


async def websocket_error(websocket: WebSocket, code: str, message: str, close_code: int) -> None:
    try:
        await websocket.send_json({"type": "error", "code": code, "message": message})
        await websocket.close(code=close_code)
    except (WebSocketDisconnect, RuntimeError, OSError):
        return

@router.websocket("/v1/audio/stream")
async def stream_audio(websocket: WebSocket) -> None:
    settings: Settings = websocket.app.state.settings
    inference: InferenceService = websocket.app.state.inference_service
    semaphore: asyncio.Semaphore = websocket.app.state.stream_slots
    acquired = False
    key_slot = None
    session = None
    receiver = None
    await websocket.accept()
    try:
        try:
            config = await asyncio.wait_for(websocket.receive_json(), timeout=settings.stream_auth_timeout_seconds)
        except asyncio.TimeoutError:
            await websocket_error(websocket, "authentication_timeout", "Authentication timeout", 1008)
            return
        except (ValueError, KeyError, TypeError):
            await websocket_error(websocket, "invalid_handshake", "Expected JSON configuration", 1003)
            return
        if not isinstance(config, dict):
            await websocket_error(websocket, "invalid_handshake", "Expected JSON object", 1003)
            return
        api_key = config.get("api_key")
        if not isinstance(api_key, str) or not validate_api_key(api_key, settings):
            await websocket_error(websocket, "unauthorized", "Invalid API credentials", 1008)
            return
        if not inference.is_ready:
            await websocket_error(websocket, "model_not_ready", "Inference model is not ready", 1013)
            return
        language = config.get("language", settings.language)
        if not isinstance(language, str) or not valid_language(language):
            await websocket_error(websocket, "invalid_language", "Invalid language configuration", 1008)
            return
        if (config.get("format", "pcm_s16le") != "pcm_s16le"
                or config.get("sample_rate", settings.sample_rate) != settings.sample_rate
                or config.get("channels", settings.channels) != settings.channels):
            await websocket_error(websocket, "unsupported_audio_format", "Expected PCM signed 16-bit little-endian, 16 kHz, mono", 1003)
            return
        mode = config.get("mode", "stream")
        if mode not in ("stream", "utterance"):
            await websocket_error(websocket, "invalid_mode", "Invalid transcription mode", 1008)
            return
        key = stream_client_key(websocket, api_key)
        counts = getattr(websocket.app.state, "stream_key_counts", None)
        if counts is None:
            counts = {}
            websocket.app.state.stream_key_counts = counts
        if counts.get(key, 0) >= settings.stream_connections_per_key:
            await websocket_error(websocket, "server_busy", "Connection capacity unavailable", 1013)
            return
        counts[key] = counts.get(key, 0) + 1
        key_slot = key
        await acquire_session(semaphore, 0.2)
        acquired = True
        session = StreamingSession(settings=settings, language=language.lower(), mode=mode)
        started = monotonic()
        await websocket.send_json({
            "type": "ready", "version": settings.app_version, "protocol_version": "1.1",
            "sample_rate": settings.sample_rate, "channels": settings.channels, "format": "pcm_s16le",
            "window_seconds": settings.stream_window_seconds, "step_seconds": settings.stream_step_seconds,
            "confirmation_margin_seconds": settings.stream_overlap_seconds,
            "max_duration_seconds": session.duration_limit, "mode": mode,
            "processing": processing_metadata(settings),
        })
        queue = asyncio.Queue(maxsize=settings.stream_queue_size)

        async def receive_frames():
            while True:
                message = await websocket.receive()
                await queue.put(message)
                if message["type"] == "websocket.disconnect" or message.get("text") == "finish":
                    return

        receiver = asyncio.create_task(receive_frames())
        busy_failures = 0
        deferred = None
        while True:
            remaining = session.duration_limit - (monotonic() - started)
            if remaining <= 0:
                result = await finalize_stream(session, inference)
                payload = session.event_payload("final", result)
                payload["reason"] = "session_duration_limit"
                await websocket.send_json(payload)
                await websocket.close(code=1000)
                return
            try:
                if deferred is not None:
                    message, deferred = deferred, None
                else:
                    message = await next_frame(queue, receiver, min(settings.stream_idle_timeout_seconds, remaining))
            except asyncio.TimeoutError:
                result = await finalize_stream(session, inference)
                payload = session.event_payload("final", result)
                payload["reason"] = "session_duration_limit" if monotonic() - started >= session.duration_limit else "idle_timeout"
                await websocket.send_json(payload)
                await websocket.close(code=1000)
                return
            if message["type"] == "websocket.disconnect":
                return
            command = message.get("text")
            if command is not None:
                if command == "finish":
                    result = await finalize_stream(session, inference)
                    await websocket.send_json(session.event_payload("final", result))
                    await websocket.close(code=1000)
                    return
                if command == "ping":
                    await websocket.send_json({"type": "pong"})
                    continue
                await websocket_error(websocket, "unsupported_command", "Unsupported control command", 1008)
                return
            chunk = message.get("bytes")
            if not chunk:
                continue
            session.append_audio(chunk)
            if session.mode == "utterance":
                continue
            # Drain already queued audio without reordering control messages.
            for _ in range(settings.stream_queue_size):
                try:
                    queued = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if queued.get("bytes") is None:
                    deferred = queued
                    break
                session.append_audio(queued["bytes"])
            skipped = trim_backlog(session)
            if skipped:
                await websocket.send_json({
                    "type": "warning", "code": "lagging",
                    "skipped_seconds": skipped / settings.sample_rate,
                })
            try:
                latest = await advance_stream(session, inference)
                busy_failures = 0
            except InferenceBusyError:
                busy_failures += 1
                await websocket.send_json({"type": "warning", "code": "server_busy"})
                if busy_failures >= settings.stream_busy_failures:
                    raise
                continue
            if latest is not None:
                await websocket.send_json(session.event_payload("partial", latest))
    except WebSocketDisconnect:
        logger.debug("Streaming client disconnected")
    except AudioDurationError:
        await websocket_error(websocket, "duration_limit", "Maximum audio duration exceeded", 1009)
    except InvalidAudioError:
        await websocket_error(websocket, "invalid_audio", "Invalid PCM audio", 1003)
    except InferenceBusyError:
        await websocket_error(websocket, "server_busy", "Inference capacity unavailable", 1013)
    except ModelNotReadyError:
        await websocket_error(websocket, "model_not_ready", "Inference model is not ready", 1013)
    except ModelExecutionError:
        logger.error("Streaming inference execution failed")
        await websocket_error(websocket, "inference_error", "Transcription failed", 1011)
    except OSError:
        logger.debug("Streaming transport interrupted")
    except Exception:
        logger.exception("Unexpected streaming failure")
        await websocket_error(websocket, "internal_error", "Streaming failed", 1011)
    finally:
        if receiver is not None:
            receiver.cancel()
            await asyncio.gather(receiver, return_exceptions=True)
        if session is not None:
            session.buffer.clear()
            session.committed_words.clear()
            session.provisional_words.clear()
        if key_slot is not None:
            counts[key_slot] -= 1
            if counts[key_slot] == 0:
                del counts[key_slot]
        if acquired:
            semaphore.release()

