"""AUD-16 compute fallback and AUD-17 hallucination filtering."""

from types import SimpleNamespace

import numpy as np
import pytest

from app.core.config import Settings
from app.services import inference as inference_module
from app.services.inference import InferenceService, ModelLoadError, TranscriptionSegment


def settings(**overrides):
    base = {"api_key": "x" * 40, "device": "cuda", "compute": "int8_float16"}
    base.update(overrides)
    return Settings(**base)


def supported(table):
    def lookup(device, _index):
        if table.get(device) is None:
            raise RuntimeError(f"{device} unavailable")
        return table[device]

    return lookup


async def test_cuda_failure_falls_back_to_cpu(monkeypatch):
    monkeypatch.setattr(
        inference_module.ctranslate2,
        "get_supported_compute_types",
        supported({"cuda": None, "cpu": {"int8", "float32"}}),
    )
    calls = []

    def factory(model, **kwargs):
        calls.append((kwargs["device"], kwargs["compute_type"]))
        return object()

    service = InferenceService(settings(), model_factory=factory)
    await service.initialize()
    try:
        assert calls == [("cpu", "int8")]
        assert service.degraded and service.active_device == "cpu"
        assert service.metrics_snapshot()["stt_degraded"] == 1
    finally:
        await service.close()


async def test_unsupported_compute_falls_back_to_int8_float32(monkeypatch):
    monkeypatch.setattr(
        inference_module.ctranslate2,
        "get_supported_compute_types",
        supported({"cuda": {"int8_float32", "float32"}, "cpu": {"int8"}}),
    )
    service = InferenceService(settings(), model_factory=lambda model, **kwargs: object())
    await service.initialize()
    try:
        assert (service.active_device, service.active_compute) == ("cuda", "int8_float32")
        assert service.degraded
    finally:
        await service.close()


async def test_configured_compute_is_not_degraded(monkeypatch):
    monkeypatch.setattr(
        inference_module.ctranslate2,
        "get_supported_compute_types",
        supported({"cuda": {"int8_float16"}, "cpu": {"int8"}}),
    )
    service = InferenceService(settings(), model_factory=lambda model, **kwargs: object())
    await service.initialize()
    try:
        assert not service.degraded
        assert service.metrics_snapshot()["stt_degraded"] == 0
    finally:
        await service.close()


async def test_no_cpu_fallback_when_disabled(monkeypatch):
    monkeypatch.setattr(
        inference_module.ctranslate2,
        "get_supported_compute_types",
        supported({"cuda": None, "cpu": {"int8"}}),
    )
    service = InferenceService(
        settings(cpu_fallback=False), model_factory=lambda model, **kwargs: object()
    )
    with pytest.raises(ModelLoadError):
        await service.initialize()
    await service.close()


def test_default_workers_is_one():
    assert settings().workers == 1


def segment(text, no_speech=0.05, logprob=-0.2):
    return TranscriptionSegment(0.0, 1.0, text, logprob, no_speech)


@pytest.mark.parametrize(
    "text",
    [
        " Legendas pela comunidade Amara.org",
        " Obrigado por assistir!",
        " Inscreva-se no canal.",
        " legendado por Fulano de Tal",
    ],
)
def test_known_hallucinations_are_filtered(text):
    service = InferenceService(settings(device="cpu", compute="int8"))
    assert service.is_hallucination(segment(text))


@pytest.mark.parametrize(
    "text", [" Ligar a luz da sala", " Obrigado pela ajuda com o relatório", " Legenda do mapa"]
)
def test_real_speech_is_kept(text):
    service = InferenceService(settings(device="cpu", compute="int8"))
    assert not service.is_hallucination(segment(text))


def test_silence_born_segments_are_filtered_and_counted():
    service = InferenceService(settings(device="cpu", compute="int8"))
    service.active_device = "cpu"
    words = [
        SimpleNamespace(start=0.0, end=0.5, word=" Abrir", probability=0.9),
    ]
    raw = [
        SimpleNamespace(
            start=0.0,
            end=1.0,
            text=" Abrir agenda",
            avg_logprob=-0.2,
            no_speech_prob=0.02,
            words=words,
        ),
        SimpleNamespace(
            start=1.0, end=3.0, text=" Tchau.", avg_logprob=-1.4, no_speech_prob=0.85, words=[]
        ),
        SimpleNamespace(
            start=3.0,
            end=5.0,
            text=" Obrigado por assistir.",
            avg_logprob=-0.3,
            no_speech_prob=0.1,
            words=[],
        ),
    ]
    model = SimpleNamespace(
        transcribe=lambda *args, **kwargs: (
            iter(raw),
            SimpleNamespace(language="pt", language_probability=0.99),
        )
    )
    result = service._transcribe_sync(
        np.zeros(16000 * 5, dtype=np.float32), "pt", None, 0.0, 0.0, True, model
    )
    assert result.text == "Abrir agenda"
    assert result.filtered_segments == 2
    assert len(result.segments) == 1


def test_filter_can_be_disabled():
    service = InferenceService(settings(device="cpu", compute="int8", hallucination_filter=False))
    assert not service.is_hallucination(segment(" Obrigado por assistir", 0.9, -2.0))
