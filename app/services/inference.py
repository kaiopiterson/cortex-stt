"""Isolated speech inference service for CortexLab STT v0.2.0."""

import asyncio
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from io import BytesIO
from time import perf_counter
from typing import Any, Callable

import av
import ctranslate2
import numpy as np
from faster_whisper import WhisperModel
from app.core import telemetry
from app.core.config import Settings
from app.services.gpu_info import log_vram
from app.services.priority import PrioritySlots

logger = logging.getLogger(__name__)


def normalize_text(text: str) -> str:
    """Casefold, keep letters and digits, collapse spaces (for blocklist matching)."""
    cleaned = "".join(char if char.isalnum() else " " for char in text.casefold())
    return " ".join(cleaned.split())


class InferenceError(RuntimeError):
    """Base inference service error."""


class ModelNotReadyError(InferenceError):
    """Model has not been initialized."""


class ModelLoadError(InferenceError):
    """Model initialization failed."""


class ModelExecutionError(InferenceError):
    """Inference backend execution failed."""


class InferenceBusyError(InferenceError):
    """Inference capacity is currently exhausted."""


class InvalidAudioError(InferenceError):
    """Audio is empty, invalid or unsupported."""


class AudioDurationError(InvalidAudioError):
    """Decoded audio exceeds the configured duration."""


@dataclass(frozen=True, slots=True)
class TranscriptionWord:
    start: float
    end: float
    text: str
    probability: float


@dataclass(frozen=True, slots=True)
class TranscriptionSegment:
    start: float
    end: float
    text: str
    avg_logprob: float
    no_speech_prob: float
    words: tuple[TranscriptionWord, ...] = ()


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    text: str
    segments: tuple[TranscriptionSegment, ...]
    language: str
    language_probability: float
    model: str
    device: str
    audio_duration_seconds: float
    processing_ms: float
    queue_wait_ms: float
    audio_offset_seconds: float
    filtered_segments: int = 0

    @property
    def rtf(self) -> float:
        if self.audio_duration_seconds <= 0:
            return 0.0
        return (self.processing_ms / 1000.0) / self.audio_duration_seconds

    @property
    def confidence(self) -> float:
        scores = [
            math.exp(min(0.0, s.avg_logprob)) * (1 - max(0.0, min(1.0, s.no_speech_prob)))
            for s in self.segments
            if s.text.strip() and math.isfinite(s.avg_logprob) and math.isfinite(s.no_speech_prob)
        ]
        return round(sum(scores) / len(scores), 4) if scores else 0.0

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["confidence"] = self.confidence
        result["rtf"] = round(self.rtf, 4)
        return result


class InferenceService:
    def __init__(
        self, settings: Settings, model_factory: Callable[..., Any] = WhisperModel
    ) -> None:
        self.settings = settings
        self._model_factory = model_factory
        self._model: Any | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._semaphore = PrioritySlots(settings.workers)
        self._executor = ThreadPoolExecutor(
            max_workers=settings.workers, thread_name_prefix="stt-inference"
        )
        self._pending: set[asyncio.Future] = set()
        self._ready = False
        self._closed = False
        self.active_device: str = settings.device
        self.active_compute: str = settings.compute
        self.degraded = False
        self._blocklist = {
            normalize_text(entry) for entry in settings.hallucination_blocklist if entry.strip()
        }

    def compute_candidates(self) -> list[tuple[str, str]]:
        """Ordered (device, compute) attempts: configured, int8_float32 on CUDA, then CPU int8."""
        candidates: list[tuple[str, str]] = [(self.settings.device, self.settings.compute)]
        if self.settings.device == "cuda":
            if self.settings.compute != "int8_float32":
                candidates.append(("cuda", "int8_float32"))
            if self.settings.cpu_fallback:
                candidates.append(("cpu", "int8"))
        return candidates

    @property
    def is_ready(self) -> bool:
        return self._ready and not self._closed and self._model is not None

    async def initialize(self) -> None:
        async with self._lifecycle_lock:
            if self.is_ready:
                return
            if self._closed:
                raise ModelNotReadyError("Inference service is closed")
            logger.info(
                "Loading STT model: model=%s device=%s compute=%s workers=%s",
                self.settings.model,
                self.settings.device,
                self.settings.compute,
                self.settings.workers,
            )
            started = perf_counter()
            loop = asyncio.get_running_loop()
            load_job = loop.run_in_executor(self._executor, self._load_model)
            try:
                self._model = await asyncio.shield(load_job)
            except asyncio.CancelledError:
                try:
                    self._model = await asyncio.shield(load_job)
                finally:
                    raise
            except ModelLoadError:
                logger.error("STT model initialization failed")
                raise
            if self.active_device == "cuda":
                await asyncio.to_thread(log_vram, self.settings.device_index)
            self._ready = True
            logger.info(
                "STT model loaded in %.2f seconds on %s/%s",
                perf_counter() - started,
                self.active_device,
                self.active_compute,
            )

    def _load_model(self) -> Any:
        """Load the model, falling back through compute_candidates() (AUD-16)."""
        errors: list[str] = []
        for index, (device, compute) in enumerate(self.compute_candidates()):
            device_index = self.settings.device_index if device == "cuda" else 0
            try:
                supported = ctranslate2.get_supported_compute_types(device, device_index)
                if compute not in supported:
                    errors.append(f"{device}/{compute}: unsupported")
                    continue
                model = self._model_factory(
                    self.settings.model,
                    device=device,
                    device_index=device_index,
                    compute_type=compute,
                    cpu_threads=self.settings.cpu_threads,
                    num_workers=self.settings.workers,
                    download_root=self.settings.model_cache_dir,
                )
            except (RuntimeError, ValueError, OSError, ImportError) as exc:
                errors.append(f"{device}/{compute}: {type(exc).__name__}")
                continue
            self.active_device, self.active_compute = device, compute
            self.degraded = index > 0
            if self.degraded:
                logger.warning(
                    "STT running degraded on %s/%s; attempts: %s", device, compute, errors
                )
            return model
        raise ModelLoadError("Unable to initialize inference backend: " + "; ".join(errors))

    async def close(self) -> None:
        async with self._lifecycle_lock:
            if self._closed:
                return
            self._ready = False
            self._closed = True
            if self._pending:
                await asyncio.gather(*tuple(self._pending), return_exceptions=True)
            await asyncio.to_thread(self._executor.shutdown, wait=True, cancel_futures=True)
            self._model = None
            logger.info("STT inference service stopped")

    def _decode_bounded(self, audio: BytesIO) -> np.ndarray:
        rate = self.settings.sample_rate
        seconds = self.settings.max_audio_duration_seconds
        limit = rate * seconds
        audio.seek(0)
        with av.open(audio, mode="r") as container:
            if not container.streams.audio:
                raise InvalidAudioError("Audio stream unavailable")
            stream = container.streams.audio[0]
            if container.duration is not None and container.duration / av.time_base > seconds:
                raise AudioDurationError("Maximum audio duration exceeded")
            if stream.duration is not None and stream.time_base is not None:
                if float(stream.duration * stream.time_base) > seconds:
                    raise AudioDurationError("Maximum audio duration exceeded")
            resampler = av.AudioResampler(format="fltp", layout="mono", rate=rate)
            chunks: list[np.ndarray] = []
            count = 0

            def consume(frames):
                nonlocal count
                for frame in frames:
                    count += frame.samples
                    if count > limit:
                        raise AudioDurationError("Maximum audio duration exceeded")
                    chunks.append(frame.to_ndarray().reshape(-1).astype(np.float32, copy=False))

            for frame in container.decode(stream):
                consume(resampler.resample(frame))
            consume(resampler.resample(None))
        return np.concatenate(chunks) if chunks else np.empty(0, dtype=np.float32)

    def prepare_audio(self, audio: np.ndarray | BytesIO) -> np.ndarray:
        """Decode and validate audio into mono float32 samples at 16 kHz."""
        return self._prepare_audio(audio)

    def _prepare_audio(self, audio: np.ndarray | BytesIO) -> np.ndarray:
        if isinstance(audio, BytesIO):
            try:
                audio.seek(0)
                samples = self._decode_bounded(audio)
            except (av.error.FFmpegError, ValueError, OSError) as exc:
                raise InvalidAudioError("Unable to decode audio") from exc
        elif isinstance(audio, np.ndarray):
            if audio.ndim != 1:
                raise InvalidAudioError("PCM audio must be mono")
            if not np.issubdtype(audio.dtype, np.floating):
                raise InvalidAudioError("PCM samples must be normalized floats")
            samples = np.ascontiguousarray(audio, dtype=np.float32)
        else:
            raise InvalidAudioError("Unsupported audio input")
        if samples.size == 0:
            raise InvalidAudioError("Audio input is empty")
        if samples.size / self.settings.sample_rate > self.settings.max_audio_duration_seconds:
            raise AudioDurationError("Maximum audio duration exceeded")
        if not np.isfinite(samples).all():
            raise InvalidAudioError("Audio contains invalid numeric samples")
        return np.clip(samples, -1.0, 1.0)

    def _transcribe_sync(
        self,
        audio: np.ndarray | BytesIO,
        language: str | None,
        initial_prompt: str | None,
        audio_offset_seconds: float,
        queue_wait_ms: float,
        word_timestamps: bool,
        model: Any,
        hotwords: str | None = None,
    ) -> TranscriptionResult:
        started = perf_counter()
        samples = self._prepare_audio(audio)
        audio_duration = samples.size / self.settings.sample_rate
        try:
            segments, info = model.transcribe(
                samples,
                language=language,
                initial_prompt=initial_prompt,
                hotwords=hotwords,
                beam_size=self.settings.beam_size,
                condition_on_previous_text=self.settings.condition_on_previous_text,
                word_timestamps=word_timestamps,
                vad_filter=self.settings.vad_enabled,
                vad_parameters={
                    "threshold": self.settings.vad_threshold,
                    "min_silence_duration_ms": self.settings.vad_min_silence_ms,
                    "speech_pad_ms": self.settings.vad_speech_pad_ms,
                },
            )
            result_segments = []
            # Consume the lazy generator inside the dedicated executor.
            for segment in segments:
                words = tuple(
                    TranscriptionWord(
                        start=round(word.start + audio_offset_seconds, 3),
                        end=round(word.end + audio_offset_seconds, 3),
                        text=word.word,
                        probability=float(word.probability),
                    )
                    for word in (segment.words or [])
                )
                result_segments.append(
                    TranscriptionSegment(
                        start=round(segment.start + audio_offset_seconds, 3),
                        end=round(segment.end + audio_offset_seconds, 3),
                        text=segment.text,
                        avg_logprob=float(segment.avg_logprob),
                        no_speech_prob=float(segment.no_speech_prob),
                        words=words,
                    )
                )
        except (RuntimeError, ValueError, OSError, av.error.FFmpegError) as exc:
            raise ModelExecutionError("Speech inference execution failed") from exc
        kept = [segment for segment in result_segments if not self.is_hallucination(segment)]
        return TranscriptionResult(
            text="".join(segment.text for segment in kept).strip(),
            segments=tuple(kept),
            language=info.language,
            language_probability=float(info.language_probability),
            model=self.settings.model,
            device=self.active_device,
            audio_duration_seconds=round(audio_duration, 4),
            processing_ms=round((perf_counter() - started) * 1000, 2),
            queue_wait_ms=round(queue_wait_ms, 2),
            audio_offset_seconds=round(audio_offset_seconds, 3),
            filtered_segments=len(result_segments) - len(kept),
        )

    def is_hallucination(self, segment: "TranscriptionSegment") -> bool:
        """AUD-17: drop silence-born text and known Whisper PT-BR hallucinations."""
        if not self.settings.hallucination_filter:
            return False
        if (
            segment.no_speech_prob >= self.settings.hallucination_no_speech_threshold
            and segment.avg_logprob <= self.settings.hallucination_logprob_threshold
        ):
            return True
        text = normalize_text(segment.text)
        return bool(text) and any(
            text == entry or text.startswith(entry + " ") for entry in self._blocklist
        )

    def metrics_snapshot(self) -> dict[str, float]:
        return {
            "stt_degraded": 1 if self.degraded else 0,
            "stt_inference_active": len(self._pending),
            "stt_inference_queue_depth": len(self._semaphore.waiters),
            "stt_inference_workers_available": self._semaphore.available,
        }

    def _release_job(self, job: asyncio.Future) -> None:
        self._pending.discard(job)
        self._semaphore.release()
        if not job.cancelled():
            if job.exception() is None:
                result = job.result()
                telemetry.inference_success.inc()
                telemetry.inference_duration.observe(result.processing_ms / 1000)
                if result.audio_duration_seconds > 0:
                    telemetry.inference_rtf.observe(result.rtf)
                if result.filtered_segments:
                    telemetry.hallucinations_filtered.inc(result.filtered_segments)
            else:
                telemetry.inference_failure.inc()

    async def transcribe(
        self,
        audio: np.ndarray | BytesIO,
        language: str | None = None,
        initial_prompt: str | None = None,
        audio_offset_seconds: float = 0.0,
        word_timestamps: bool | None = None,
        priority: int = 1,
        hotwords: str | None = None,
    ) -> TranscriptionResult:
        if not self.is_ready:
            raise ModelNotReadyError("Speech model is not ready")
        if not math.isfinite(audio_offset_seconds) or audio_offset_seconds < 0:
            raise ValueError("Invalid audio offset")
        if language is None:
            language = self.settings.language
        if language == "" or language == "auto":
            language = None
        elif language:
            language = language.lower().replace("_", "-").split("-")[0]
        if initial_prompt:
            initial_prompt = (
                initial_prompt[-self.settings.max_prompt_chars :]
                if self.settings.max_prompt_chars
                else None
            )
        if word_timestamps is None:
            word_timestamps = self.settings.word_timestamps
        queue_started = perf_counter()
        try:
            await asyncio.wait_for(
                self._semaphore.acquire(priority),
                timeout=self.settings.inference_queue_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            telemetry.inference_busy.inc()
            raise InferenceBusyError("Inference capacity unavailable") from exc
        queue_wait_ms = (perf_counter() - queue_started) * 1000
        telemetry.queue_wait.observe(queue_wait_ms / 1000)
        if not self.is_ready:
            self._semaphore.release()
            raise ModelNotReadyError("Inference service is stopping")
        loop = asyncio.get_running_loop()
        try:
            job = loop.run_in_executor(
                self._executor,
                self._transcribe_sync,
                audio,
                language,
                initial_prompt,
                audio_offset_seconds,
                queue_wait_ms,
                word_timestamps,
                self._model,
                hotwords,
            )
        except RuntimeError as exc:
            self._semaphore.release()
            raise ModelExecutionError("Inference executor is unavailable") from exc
        self._pending.add(job)
        job.add_done_callback(self._release_job)
        # Keep the GPU slot until native computation actually finishes.
        return await asyncio.shield(job)

    async def transcribe_window(
        self,
        pcm: np.ndarray,
        offset_samples: int,
        language: str | None = None,
        previous_text: str | None = None,
        hotwords: str | None = None,
        priority: int = 1,
    ) -> TranscriptionResult:
        if offset_samples < 0:
            raise ValueError("Audio offset cannot be negative")
        if pcm.size > self.settings.stream_window_samples:
            raise InvalidAudioError("Audio exceeds maximum streaming window")
        return await self.transcribe(
            audio=pcm,
            language=language,
            initial_prompt=previous_text,
            audio_offset_seconds=offset_samples / self.settings.sample_rate,
            word_timestamps=True,
            hotwords=hotwords,
            priority=priority,
        )
