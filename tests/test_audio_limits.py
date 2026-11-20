"""Bounded decoding tests with fake PyAV containers."""

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import asyncio

import pytest
from app.core.config import Settings
from app.services.inference import AudioDurationError, InferenceService


@pytest.fixture
def service():
    instance = InferenceService(
        Settings(api_key="x" * 40, device="cpu", compute="int8", max_audio_duration_seconds=1)
    )
    yield instance
    asyncio.run(instance.close())


def test_container_duration_rejected_before_decode(service):
    container = MagicMock()
    container.duration = 2_000_000
    container.streams.audio = [SimpleNamespace(duration=None, time_base=None)]
    with patch("app.services.inference.av.open") as opened:
        opened.return_value.__enter__.return_value = container
        with pytest.raises(AudioDurationError):
            service._prepare_audio(BytesIO(b"data"))
    container.decode.assert_not_called()


def test_unknown_duration_aborts_before_accumulating_frame(service):
    container = MagicMock()
    container.duration = None
    container.streams.audio = [SimpleNamespace(duration=None, time_base=None)]
    frame = MagicMock()
    frame.samples = 16001
    container.decode.return_value = iter([frame])
    with (
        patch("app.services.inference.av.open") as opened,
        patch("app.services.inference.av.AudioResampler") as resampler,
    ):
        opened.return_value.__enter__.return_value = container
        resampler.return_value.resample.return_value = [frame]
        with pytest.raises(AudioDurationError):
            service._prepare_audio(BytesIO(b"data"))
    frame.to_ndarray.assert_not_called()
