import sys
from unittest.mock import Mock

from app.core.config import Settings
from app.services.gpu_info import log_vram


def test_cuda_default(monkeypatch):
    monkeypatch.delenv("STT_COMPUTE", raising=False)
    assert Settings(api_key="x" * 40, device="cuda", _env_file=None).compute == "int8_float16"


def test_cpu_default(monkeypatch):
    monkeypatch.delenv("STT_COMPUTE", raising=False)
    assert Settings(api_key="x" * 40, device="cpu", _env_file=None).compute == "int8"


def test_nvml_optional(monkeypatch):
    monkeypatch.setitem(sys.modules, "pynvml", None)
    log_vram(0)


def test_nvml_shutdown(monkeypatch):
    nvml = Mock()
    nvml.nvmlDeviceGetMemoryInfo.return_value = Mock(free=1, used=2, total=3)
    monkeypatch.setitem(sys.modules, "pynvml", nvml)
    log_vram(0)
    nvml.nvmlShutdown.assert_called_once()
