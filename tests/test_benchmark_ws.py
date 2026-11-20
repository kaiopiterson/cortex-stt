import runpy
from pathlib import Path

benchmark = runpy.run_path(str(Path(__file__).with_name("benchmark_ws.py")))


def test_summary():
    rows = [
        {"ttft_ms": None, "final_ms": 10, "rtf": 0.1, "busy": 0, "ok": True},
        {"ttft_ms": None, "final_ms": 20, "rtf": 0.2, "busy": 2, "ok": False},
    ]
    result = benchmark["summarize"](rows)
    assert result["ttft_ms"]["p50"] is None
    assert result["final_ms"]["p50"] == 15
    assert result["server_busy_rate"] == 0.5
