"""Centralized application configuration for CortexLab STT v0.2.0."""

from functools import lru_cache
from typing import Literal
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="STT_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )
    app_name: str = "CortexLab STT"
    app_version: str = "0.3.0"
    app_requests_per_minute: int = Field(default=60, ge=1)
    app_daily_audio_seconds: int = Field(default=3600, ge=1)
    app_monthly_audio_seconds: int = Field(default=36000, ge=1)
    app_concurrency: int = Field(default=2, ge=1)
    registry_path: str = "/data/registry.sqlite3"
    environment: Literal["development", "staging", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    admin_key: SecretStr | None = None
    legacy_api_key_enabled: bool | None = None
    api_key: SecretStr
    rate_limit: str = "60/minute"
    trust_proxy_headers: bool = False
    stream_connections_per_key: int = Field(default=4, ge=1, le=100)
    model: str = "small"
    device: Literal["cuda", "cpu"] = "cuda"
    device_index: int = Field(default=0, ge=0)
    compute: Literal["float32", "float16", "int8", "int8_float32", "int8_float16"] = "int8_float16"
    language: str = "pt"
    model_cache_dir: str = "/models"
    cpu_threads: int = Field(default=4, ge=1, le=64)
    workers: int = Field(default=2, ge=1, le=16)
    beam_size: int = Field(default=1, ge=1, le=10)
    sessions: int = Field(default=10, ge=1, le=100)
    stream_sessions: int | None = Field(default=None, ge=1, le=100)
    rest_concurrency: int = Field(default=4, ge=1, le=100)
    inference_queue_timeout_seconds: float = Field(default=5.0, gt=0, le=120)
    sample_rate: Literal[16000] = 16000
    channels: Literal[1] = 1
    sample_width_bytes: Literal[2] = 2
    max_upload_mb: int = Field(default=25, ge=1, le=100)
    max_audio_duration_seconds: int = Field(default=600, ge=1, le=3600)
    stream_window_seconds: float = Field(default=4.0, ge=1.0, le=15.0)
    stream_overlap_seconds: float = Field(default=0.5, ge=0.0, le=3.0)
    stream_step_seconds: float = Field(default=1.0, ge=0.25, le=5.0)
    stream_max_duration_seconds: int = Field(default=1800, ge=10, le=7200)
    stream_idle_timeout_seconds: int = Field(default=20, ge=5, le=300)
    stream_max_chunk_bytes: int = Field(default=320000, ge=320, le=1048576)
    stream_auth_timeout_seconds: int = Field(default=5, ge=1, le=30)
    utterance_max_seconds: int = Field(default=15, ge=1, le=60)
    stream_max_lag_seconds: float = Field(default=3.0, gt=0, le=60)
    stream_busy_failures: int = Field(default=3, ge=1, le=20)
    stream_queue_size: int = Field(default=8, ge=1, le=64)
    review_threshold: float = Field(default=0.6, ge=0, le=1)
    vad_enabled: bool = True
    vad_threshold: float = Field(default=0.5, gt=0.0, lt=1.0)
    vad_min_silence_ms: int = Field(default=350, ge=100, le=3000)
    vad_speech_pad_ms: int = Field(default=150, ge=0, le=1000)
    word_timestamps: bool = False
    condition_on_previous_text: bool = False
    max_prompt_chars: int = Field(default=512, ge=0, le=4096)

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if (
            len(secret) < 32
            or secret.lower() in {"changeme", "secret", "default", "replace-with-random-secret"}
            or secret != secret.strip()
        ):
            raise ValueError(
                "STT_API_KEY must contain a non-default secret with at least 32 characters"
            )
        return value

    @field_validator("model", "model_cache_dir")
    @classmethod
    def validate_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Configuration value cannot be empty")
        return value.strip()

    @model_validator(mode="before")
    @classmethod
    def device_compute_default(cls, values):
        if isinstance(values, dict) and values.get("device") == "cpu" and "compute" not in values:
            return {**values, "compute": "int8"}
        return values

    @model_validator(mode="after")
    def validate_configuration(self):
        if self.legacy_api_key_enabled is None:
            object.__setattr__(self, "legacy_api_key_enabled", self.environment != "production")
        if self.admin_key is not None:
            secret = self.admin_key.get_secret_value()
            if (
                len(secret) < 32
                or secret == self.api_key.get_secret_value()
                or secret.startswith(("stt_", "tok_"))
            ):
                raise ValueError("STT_ADMIN_KEY must be independent and at least 32 characters")
        if self.device == "cpu" and self.compute in {"float16", "int8_float16"}:
            raise ValueError("Selected compute type requires a compatible accelerator")
        if self.stream_overlap_seconds >= self.stream_window_seconds:
            raise ValueError("Streaming overlap must be smaller than streaming window")
        if self.stream_step_seconds > self.stream_window_seconds - self.stream_overlap_seconds:
            raise ValueError("Streaming step exceeds the usable window")
        if self.stream_max_chunk_bytes % self.sample_width_bytes:
            raise ValueError("Streaming chunk size must align with PCM sample width")
        return self

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def bytes_per_second(self) -> int:
        return self.sample_rate * self.channels * self.sample_width_bytes

    @property
    def stream_window_samples(self) -> int:
        return round(self.stream_window_seconds * self.sample_rate)

    @property
    def stream_overlap_samples(self) -> int:
        return round(self.stream_overlap_seconds * self.sample_rate)

    @property
    def stream_step_samples(self) -> int:
        return round(self.stream_step_seconds * self.sample_rate)

    @property
    def stream_window_bytes(self) -> int:
        return self.stream_window_samples * self.sample_width_bytes * self.channels


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
