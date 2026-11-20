from app.api.websocket import StreamingSession, trim_backlog
from app.core.config import Settings
from app.services.inference import TranscriptionWord


def test_backlog_preserves_confirmed_words():
    settings = Settings(api_key="x" * 40, device="cpu", compute="int8")
    session = StreamingSession(settings)
    session.committed_words.append(TranscriptionWord(0.0, 0.5, " Olá", 0.9))
    session.committed_until_seconds = 0.5
    session.append_audio(b"\x00\x00" * 96000)
    assert trim_backlog(session) == 32000
    assert session.buffered_samples == 64000
    assert session.buffer_offset_samples == 32000
    assert session.confirmed_text == "Olá"
    assert session.committed_until_seconds == 0.5


def test_normal_backlog_is_not_trimmed():
    settings = Settings(api_key="x" * 40, device="cpu", compute="int8")
    session = StreamingSession(settings)
    session.append_audio(b"\x00\x00" * 16000)
    assert trim_backlog(session) == 0
