from app.core.telemetry import Telemetry


def test_histogram_and_counter():
    metrics = Telemetry()
    metrics.observe("stt_duration_seconds", 0.2)
    metrics.observe("stt_duration_seconds", 2)
    metrics.increment("stt_success_total")
    output = metrics.render({"stt_active": 3})
    assert 'stt_duration_seconds_bucket{le="0.25"} 1.0' in output
    assert 'stt_duration_seconds_bucket{le="+Inf"} 2.0' in output
    assert "stt_duration_seconds_sum 2.2" in output
    assert "stt_success_total 1" in output
    assert "stt_active 3" in output
