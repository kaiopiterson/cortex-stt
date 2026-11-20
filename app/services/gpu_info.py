"""Best-effort NVML startup diagnostics, without a required dependency."""
import logging

logger = logging.getLogger(__name__)


def log_vram(device_index: int) -> None:
    try:
        import pynvml
        pynvml.nvmlInit()
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(device_index)
            memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
            logger.info("GPU VRAM: free=%d MiB used=%d MiB total=%d MiB",
                        memory.free // 1048576, memory.used // 1048576,
                        memory.total // 1048576)
        finally:
            pynvml.nvmlShutdown()
    except Exception:
        logger.debug("Optional NVML diagnostics unavailable")


def gpu_metrics(device_index: int) -> dict[str, float]:
    """Sample NVML on demand; absence is explicit, not zero utilization."""
    try:
        import pynvml

        pynvml.nvmlInit()
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(device_index)
            memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
            utilization = pynvml.nvmlDeviceGetUtilizationRates(handle)
            return {
                "stt_gpu_available": 1,
                "stt_gpu_memory_used_bytes": memory.used,
                "stt_gpu_memory_total_bytes": memory.total,
                "stt_gpu_utilization_ratio": utilization.gpu / 100,
            }
        finally:
            pynvml.nvmlShutdown()
    except Exception:
        return {"stt_gpu_available": 0}
