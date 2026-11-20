from unittest.mock import AsyncMock

import pytest

from app.api.websocket import StreamingSession, finalize_stream
from app.core.config import Settings
from app.services.inference import AudioDurationError, TranscriptionResult


@pytest.mark.asyncio
async def test_utterance_final_uses_single_inference():
    settings = Settings(api_key="x" * 40, device="cpu", compute="int8")
    session = StreamingSession(settings, mode="utterance")
    session.append_audio(b"\x00\x00" * 16000)
    inference = AsyncMock()
    inference.transcribe.return_value = TranscriptionResult(
        "hello", (), "en", 1.0, "small", "cpu", 1.0, 10.0, 0.0, 0.0
    )
    await finalize_stream(session, inference)
    inference.transcribe.assert_awaited_once()
    inference.transcribe_window.assert_not_called()


def test_utterance_duration_limit():
    settings = Settings(api_key="x" * 40, device="cpu", compute="int8",
                        utterance_max_seconds=1)
    session = StreamingSession(settings, mode="utterance")
    with pytest.raises(AudioDurationError):
        session.append_audio(b"\x00\x00" * 16001)
