"""Bounded-cardinality, process-local Prometheus instrumentation."""

from threading import Lock


class Telemetry:
    bounds = (0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 60)

    def __init__(self):
        self.lock = Lock()
        self.histograms: dict[str, list[float]] = {}
        self.counters: dict[str, int] = {}

    def increment(self, name: str) -> None:
        with self.lock:
            self.counters[name] = self.counters.get(name, 0) + 1

    def observe(self, name: str, seconds: float) -> None:
        with self.lock:
            values = self.histograms.setdefault(name, [0.0] * (len(self.bounds) + 2))
            for index, bound in enumerate(self.bounds):
                if seconds <= bound:
                    values[index] += 1
            values[-2] += 1
            values[-1] += seconds

    def render(self, gauges: dict[str, float]) -> str:
        lines: list[str] = []
        with self.lock:
            for name, count in sorted(self.counters.items()):
                lines.extend((f"# TYPE {name} counter", f"{name} {count}"))
            for name, values in sorted(self.histograms.items()):
                lines.append(f"# TYPE {name} histogram")
                for bound, bucket in zip(self.bounds, values):
                    lines.append(f'{name}_bucket{{le="{bound}"}} {bucket}')
                lines.extend(
                    (
                        f'{name}_bucket{{le="+Inf"}} {values[-2]}',
                        f"{name}_count {values[-2]}",
                        f"{name}_sum {values[-1]}",
                    )
                )
        for name, gauge in sorted(gauges.items()):
            lines.extend((f"# TYPE {name} gauge", f"{name} {gauge}"))
        return "\n".join(lines) + "\n"


telemetry = Telemetry()
