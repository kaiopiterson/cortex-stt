"""Streaming state tests without model initialization."""

import pytest
from app.core.config import Settings
from app.api.websocket import StreamingSession
from app.services.inference import InvalidAudioError, AudioDurationError, TranscriptionWord


@pytest.fixture
def session():
    return StreamingSession(
        Settings(api_key="x" * 40, device="cpu", compute="int8", stream_max_duration_seconds=10)
    )


def test_append_discard(session):
    session.append_audio(b"\x00\x00" * 100)
    session.discard_samples(40)
    assert session.buffered_samples == 60
    assert session.buffer_offset_samples == 40


def test_alignment(session):
    with pytest.raises(InvalidAudioError):
        session.append_audio(b"x")


def test_chunk_limit(session):
    with pytest.raises(InvalidAudioError):
        session.append_audio(b"x" * (session.settings.stream_max_chunk_bytes + 2))


def test_duration(session):
    session.total_received_samples = 160000
    with pytest.raises(AudioDurationError):
        session.append_audio(b"\x00\x00")


def test_duplicate_and_context(session):
    word = TranscriptionWord(0.1, 0.4, " Olá", 0.9)
    session.committed_words.append(word)
    session.buffer_offset_samples = 16000
    assert session.previous_text == " Olá"
    assert session._is_duplicate(TranscriptionWord(0.12, 0.42, " olá", 0.8))
