"""CortexLab STT v0.2.0 — REST and bounded sliding-window streaming."""
import asyncio
import logging
import secrets
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from time import monotonic
from typing import Annotated

import numpy as np
from fastapi import (APIRouter, Depends, File, Form, Header, HTTPException,
                     Request, UploadFile, WebSocket, WebSocketDisconnect)
from fastapi.responses import HTMLResponse
from app.core.config import Settings
from app.services.inference import (
    AudioDurationError, InferenceBusyError, InferenceService, InvalidAudioError,
    ModelExecutionError, ModelNotReadyError, TranscriptionResult, TranscriptionWord,
)

logger = logging.getLogger(__name__)
router = APIRouter()
INDEX_PATH = Path(__file__).resolve().parents[1] / "index.html"
SUPPORTED_AUDIO_TYPES = ("audio/", "video/")


def get_settings_from_state(request: Request) -> Settings:
    return request.app.state.settings


def get_inference_service(request: Request) -> InferenceService:
    return request.app.state.inference_service


SettingsDependency = Annotated[Settings, Depends(get_settings_from_state)]
InferenceDependency = Annotated[InferenceService, Depends(get_inference_service)]


def validate_api_key(supplied_key: str | None, settings: Settings) -> bool:
    return bool(supplied_key and secrets.compare_digest(
        supplied_key.encode("utf-8"), settings.api_key.get_secret_value().encode("utf-8")))


def require_api_key(settings: SettingsDependency,
                    authorization: Annotated[str | None, Header()] = None) -> None:
    scheme, separator, token = (authorization or "").partition(" ")
    if not separator or scheme.lower() != "bearer" or not validate_api_key(token, settings):
        raise HTTPException(401, "Invalid API credentials" if authorization else "Missing API credentials",
                            headers={"WWW-Authenticate": "Bearer"})


Authenticated = Annotated[None, Depends(require_api_key)]


@dataclass(slots=True)
class StreamingSession:
    settings: Settings
    language: str = "pt"
    buffer: bytearray = field(default_factory=bytearray)
    committed_words: list[TranscriptionWord] = field(default_factory=list)
    provisional_words: list[TranscriptionWord] = field(default_factory=list)
    buffer_offset_samples: int = 0
    total_received_samples: int = 0
    last_inference_samples: int = 0
    committed_until_seconds: float = 0.0
    revision: int = 0

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
        if self.total_received_samples + count > self.settings.stream_max_duration_seconds * self.settings.sample_rate:
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


def processing_metadata(settings: Settings) -> dict:
    return {"mode": "server", "location": "server", "server_used": True,
            "engine": "faster-whisper", "model": settings.model, "device": settings.device}


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


async def advance_stream(session: StreamingSession, inference: InferenceService) -> TranscriptionResult | None:
    settings = session.settings
    window = settings.stream_window_samples
    step = settings.stream_step_samples
    margin = settings.stream_overlap_samples
    latest = None
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
    await advance_stream(session, inference)
    if session.buffered_samples == 0:
        session.provisional_words.clear()
        return None
    end = session.buffer_offset_samples + session.buffered_samples
    return await process_window(session, inference, session.buffered_samples, end, final=True)


async def acquire_session(semaphore: asyncio.Semaphore, timeout: float) -> None:
    try:
        await asyncio.wait_for(semaphore.acquire(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise InferenceBusyError("Maximum concurrent sessions reached") from exc


async def websocket_error(websocket: WebSocket, code: str, message: str, close_code: int) -> None:
    try:
        await websocket.send_json({"type": "error", "code": code, "message": message})
        await websocket.close(code=close_code)
    except (WebSocketDisconnect, RuntimeError, OSError):
        return


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def web_interface() -> HTMLResponse:
    try:
        content = await asyncio.to_thread(INDEX_PATH.read_text, encoding="utf-8")
    except FileNotFoundError as exc:
        raise HTTPException(404, "Web interface unavailable") from exc
    except OSError as exc:
        raise HTTPException(500, "Unable to load web interface") from exc
    return HTMLResponse(content=content)


@router.get("/health", tags=["Health"])
async def health(settings: SettingsDependency, inference: InferenceDependency) -> dict:
    return {"status": "ok" if inference.is_ready else "initializing",
            "ready": inference.is_ready, "version": settings.app_version,
            "model": settings.model, "device": settings.device}


@router.get("/v1/models", tags=["Models"])
async def list_models(settings: SettingsDependency, authenticated: Authenticated) -> dict:
    return {"object": "list", "data": [{"id": settings.model, "object": "model", "owned_by": "cortexlab"}]}


@router.post("/v1/audio/transcriptions", tags=["Transcriptions"])
async def transcribe_audio(request: Request, settings: SettingsDependency,
                           inference: InferenceDependency, authenticated: Authenticated,
                           file: Annotated[UploadFile, File()],
                           model: Annotated[str | None, Form()] = None,
                           language: Annotated[str | None, Form()] = None,
                           prompt: Annotated[str | None, Form()] = None) -> dict:
    if (model or settings.model) not in {settings.model, "whisper-1"}:
        raise HTTPException(400, "Unsupported model")
    if not inference.is_ready:
        raise HTTPException(503, "Inference model is not ready")
    content_type = file.content_type or ""
    if content_type and content_type != "application/octet-stream" and not content_type.startswith(SUPPORTED_AUDIO_TYPES):
        raise HTTPException(415, "Unsupported audio content type")
    semaphore = request.app.state.session_slots
    acquired = False
    try:
        await acquire_session(semaphore, settings.inference_queue_timeout_seconds)
        acquired = True
        started = monotonic()
        audio = await file.read(settings.max_upload_bytes + 1)
        if not audio:
            raise HTTPException(422, "Empty audio file")
        if len(audio) > settings.max_upload_bytes:
            raise HTTPException(413, "Maximum upload size exceeded")
        result = await inference.transcribe(audio=BytesIO(audio), language=language, initial_prompt=prompt)
        response = result.to_dict()
        response["total_ms"] = round((monotonic() - started) * 1000, 2)
        response["processing"] = processing_metadata(settings)
        return response
    except AudioDurationError as exc:
        raise HTTPException(413, "Maximum audio duration exceeded") from exc
    except InvalidAudioError as exc:
        raise HTTPException(422, "Invalid or unsupported audio") from exc
    except InferenceBusyError as exc:
        raise HTTPException(503, "Inference capacity unavailable", headers={"Retry-After": "2"}) from exc
    except ModelNotReadyError as exc:
        raise HTTPException(503, "Inference model is not ready") from exc
    except ModelExecutionError as exc:
        logger.error("REST inference execution failed")
        raise HTTPException(500, "Transcription failed") from exc
    finally:
        if acquired:
            semaphore.release()
        await file.close()


@router.websocket("/v1/audio/stream")
async def stream_audio(websocket: WebSocket) -> None:
    settings: Settings = websocket.app.state.settings
    inference: InferenceService = websocket.app.state.inference_service
    semaphore: asyncio.Semaphore = websocket.app.state.session_slots
    acquired = False
    session = None
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
        if not isinstance(language, str) or len(language) > 32:
            await websocket_error(websocket, "invalid_language", "Invalid language configuration", 1008)
            return
        if (config.get("format", "pcm_s16le") != "pcm_s16le"
                or config.get("sample_rate", settings.sample_rate) != settings.sample_rate
                or config.get("channels", settings.channels) != settings.channels):
            await websocket_error(websocket, "unsupported_audio_format", "Expected PCM signed 16-bit little-endian, 16 kHz, mono", 1003)
            return
        await acquire_session(semaphore, 0.2)
        acquired = True
        session = StreamingSession(settings=settings, language=language)
        started = monotonic()
        await websocket.send_json({
            "type": "ready", "version": settings.app_version, "protocol_version": "1.1",
            "sample_rate": settings.sample_rate, "channels": settings.channels, "format": "pcm_s16le",
            "window_seconds": settings.stream_window_seconds, "step_seconds": settings.stream_step_seconds,
            "confirmation_margin_seconds": settings.stream_overlap_seconds,
            "max_duration_seconds": settings.stream_max_duration_seconds,
            "processing": processing_metadata(settings),
        })
        while True:
            remaining = settings.stream_max_duration_seconds - (monotonic() - started)
            if remaining <= 0:
                result = await finalize_stream(session, inference)
                payload = session.event_payload("final", result)
                payload["reason"] = "session_duration_limit"
                await websocket.send_json(payload)
                await websocket.close(code=1000)
                return
            try:
                message = await asyncio.wait_for(websocket.receive(), timeout=min(settings.stream_idle_timeout_seconds, remaining))
            except asyncio.TimeoutError:
                result = await finalize_stream(session, inference)
                payload = session.event_payload("final", result)
                payload["reason"] = "session_duration_limit" if monotonic() - started >= settings.stream_max_duration_seconds else "idle_timeout"
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
            if len(chunk) > settings.stream_max_chunk_bytes:
                await websocket_error(websocket, "chunk_too_large", "Maximum audio chunk size exceeded", 1009)
                return
            chunk_view = memoryview(chunk)
            if len(chunk_view) % settings.sample_width_bytes:
                raise InvalidAudioError("Invalid PCM sample alignment")
            incoming = len(chunk_view) // settings.sample_width_bytes
            if session.total_received_samples + incoming > settings.stream_max_duration_seconds * settings.sample_rate:
                raise AudioDurationError("Maximum streaming duration exceeded")
            step_bytes = settings.stream_step_samples * settings.sample_width_bytes
            latest = None
            for offset in range(0, len(chunk_view), step_bytes):
                session.append_audio(bytes(chunk_view[offset:offset + step_bytes]))
                result = await advance_stream(session, inference)
                if result is not None:
                    latest = result
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
    finally:
        if session is not None:
            session.buffer.clear()
            session.committed_words.clear()
            session.provisional_words.clear()
        if acquired:
            semaphore.release()
