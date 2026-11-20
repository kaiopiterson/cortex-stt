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
        lines = []
        with self.lock:
            for name, value in sorted(self.counters.items()):
                lines.extend((f"# TYPE {name} counter", f"{name} {value}"))
            for name, values in sorted(self.histograms.items()):
                lines.append(f"# TYPE {name} histogram")
                for bound, value in zip(self.bounds, values):
                    lines.append(f'{name}_bucket{{le="{bound}"}} {value}')
                lines.extend((
                    f'{name}_bucket{{le="+Inf"}} {values[-2]}',
                    f"{name}_count {values[-2]}",
                    f"{name}_sum {values[-1]}",
                ))
        for name, value in sorted(gauges.items()):
            lines.extend((f"# TYPE {name} gauge", f"{name} {value}"))
        return "\n".join(lines) + "\n"


telemetry = Telemetry()
