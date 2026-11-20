import pytest

from app.api.common import parse_hotwords
from app.services.inference import TranscriptionResult, TranscriptionSegment


def test_confidence():
    result = TranscriptionResult(
        "Olá", (TranscriptionSegment(0, 1, "Olá", 0, 0.2),),
        "pt", 1, "small", "cpu", 1, 10, 0, 0
    )
    assert result.confidence == 0.8
    assert result.to_dict()["confidence"] == 0.8


def test_hotwords():
    assert parse_hotwords(["Ritmo", "CortexLab"]) == "Ritmo CortexLab"


@pytest.mark.parametrize("value", ["abc", ["x" * 65], ["x"] * 51, [1], [" "]])
def test_invalid_hotwords(value):
    with pytest.raises(ValueError):
        parse_hotwords(value)
