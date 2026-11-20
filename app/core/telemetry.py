"""Prometheus instrumentation for CortexLab STT v0.3.0 (AUD-15).

Labels are bounded: route templates, HTTP methods, status codes, and fixed
enumerations for reasons and surfaces. Application and subject identifiers are
never used as labels; per-app consumption is served by /v1/admin/usage.
"""

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Histogram,
    disable_created_metrics,
    generate_latest,
)

disable_created_metrics()

REGISTRY = CollectorRegistry(auto_describe=True)
LATENCY_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60)
RTF_BUCKETS = (0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5, 1, 2)

REJECTION_REASONS = (
    "unauthorized",
    "forbidden_scope",
    "rate_limited",
    "quota_exceeded",
    "server_busy",
    "lagging",
    "unavailable",
)
AUTH_SURFACES = ("rest", "websocket", "token", "admin", "metrics")

inference_success = Counter("stt_inference_success", "Completed inference jobs", registry=REGISTRY)
inference_failure = Counter("stt_inference_failure", "Failed inference jobs", registry=REGISTRY)
inference_busy = Counter(
    "stt_inference_busy", "Jobs rejected because the inference queue was full", registry=REGISTRY
)
inference_duration = Histogram(
    "stt_inference_duration_seconds",
    "Native inference time per job (window or file)",
    buckets=LATENCY_BUCKETS,
    registry=REGISTRY,
)
queue_wait = Histogram(
    "stt_queue_wait_seconds",
    "Time waiting for an inference worker",
    buckets=LATENCY_BUCKETS,
    registry=REGISTRY,
)
inference_rtf = Histogram(
    "stt_inference_rtf",
    "Real-time factor (processing time / audio duration)",
    buckets=RTF_BUCKETS,
    registry=REGISTRY,
)
http_requests = Counter(
    "stt_http_requests",
    "HTTP requests by route template, method and status",
    ["route", "method", "status"],
    registry=REGISTRY,
)
http_duration = Histogram(
    "stt_http_request_duration_seconds",
    "End-to-end HTTP latency by route template",
    ["route"],
    buckets=LATENCY_BUCKETS,
    registry=REGISTRY,
)
stream_sessions = Counter(
    "stt_stream_sessions", "WebSocket sessions admitted", ["mode"], registry=REGISTRY
)
stream_first_partial = Histogram(
    "stt_stream_first_partial_seconds",
    "Time from the first audio frame to the first partial result",
    buckets=LATENCY_BUCKETS,
    registry=REGISTRY,
)
stream_final_latency = Histogram(
    "stt_stream_final_latency_seconds",
    "Time from the client 'finish' command to the final result",
    ["mode"],
    buckets=LATENCY_BUCKETS,
    registry=REGISTRY,
)
rejections = Counter(
    "stt_rejections", "Rejected or degraded operations by reason", ["reason"], registry=REGISTRY
)
auth_failures = Counter(
    "stt_auth_failures", "Failed authentications by surface", ["surface"], registry=REGISTRY
)
tokens_issued = Counter("stt_tokens_issued", "Access tokens issued", registry=REGISTRY)
hallucinations_filtered = Counter(
    "stt_hallucination_segments_filtered",
    "Segments dropped by the hallucination filter",
    registry=REGISTRY,
)

for _reason in REJECTION_REASONS:
    rejections.labels(_reason)
for _surface in AUTH_SURFACES:
    auth_failures.labels(_surface)


def reject(reason: str) -> None:
    rejections.labels(reason if reason in REJECTION_REASONS else "server_busy").inc()


def auth_failed(surface: str) -> None:
    auth_failures.labels(surface if surface in AUTH_SURFACES else "rest").inc()


def render(gauges: dict[str, float]) -> str:
    """Prometheus text exposition: registry metrics plus point-in-time gauges."""
    lines = [generate_latest(REGISTRY).decode("utf-8").rstrip("\n")]
    for name, value in sorted(gauges.items()):
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {value}")
    return "\n".join(lines) + "\n"
