"""In-process Prometheus-style metrics (Phase 7.6)."""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    idx = int((len(values) - 1) * pct)
    return values[idx]


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self._histograms: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = defaultdict(
            list
        )

    def inc(self, name: str, value: float = 1.0, **labels: str) -> None:
        key = (name, tuple(sorted(labels.items())))
        with self._lock:
            self._counters[key] += value

    def observe(self, name: str, value: float, **labels: str) -> None:
        key = (name, tuple(sorted(labels.items())))
        with self._lock:
            bucket = self._histograms[key]
            bucket.append(value)
            if len(bucket) > 2048:
                del bucket[: len(bucket) - 2048]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            counters = {
                self._format_name(name, labels): value
                for (name, labels), value in self._counters.items()
            }
            histograms: dict[str, dict[str, float]] = {}
            for (name, labels), values in self._histograms.items():
                if not values:
                    continue
                ordered = sorted(values)
                histograms[self._format_name(name, labels)] = {
                    "count": float(len(ordered)),
                    "sum": float(sum(ordered)),
                    "p50": _percentile(ordered, 0.50),
                    "p95": _percentile(ordered, 0.95),
                    "p99": _percentile(ordered, 0.99),
                }
        return {"counters": counters, "histograms": histograms, "ts": time.time()}

    def prometheus_text(self) -> str:
        snap = self.snapshot()
        lines: list[str] = []
        for name, value in sorted(snap["counters"].items()):
            base = name.split("{", 1)[0]
            lines.append(f"# TYPE {base} counter")
            lines.append(f"{name} {value}")
        for name, stats in sorted(snap["histograms"].items()):
            base = name.split("{", 1)[0]
            lines.append(f"# TYPE {base} summary")
            for quantile, key in (("0.5", "p50"), ("0.95", "p95"), ("0.99", "p99")):
                if "{" in name:
                    qname = name[:-1] + f',quantile="{quantile}"' + "}"
                else:
                    qname = f'{base}{{quantile="{quantile}"}}'
                lines.append(f"{qname} {stats[key]}")
            if "{" in name:
                prefix, labels = name.split("{", 1)
                labels = labels.rstrip("}")
                count_name = f"{prefix}_count{{{labels}}}"
                sum_name = f"{prefix}_sum{{{labels}}}"
            else:
                count_name = f"{base}_count"
                sum_name = f"{base}_sum"
            lines.append(f"{count_name} {stats['count']}")
            lines.append(f"{sum_name} {stats['sum']}")
        return "\n".join(lines) + ("\n" if lines else "")

    @staticmethod
    def _format_name(name: str, labels: tuple[tuple[str, str], ...]) -> str:
        if not labels:
            return name
        label_text = ",".join(f'{k}="{v}"' for k, v in labels)
        return f"{name}{{{label_text}}}"


METRICS = MetricsRegistry()

CONTEXT_PHASES = frozenset({"retrieve", "hybrid", "semantic", "rank", "pack", "filter"})


class PhaseTimer:
    def __init__(self, phase: str, *, metric: str = "cgmcp_request_latency_seconds") -> None:
        self.phase = phase
        self.metric = metric
        self._start = 0.0

    def __enter__(self) -> PhaseTimer:
        self._start = time.perf_counter()
        return self

    def __exit__(self, *args: object) -> None:
        elapsed = time.perf_counter() - self._start
        METRICS.observe(self.metric, elapsed, phase=self.phase)


class ContextPhaseTracker:
    """Accumulates per-phase wall time for the context pipeline (Phase 14.3)."""

    def __init__(self) -> None:
        self._active: str | None = None
        self._start = 0.0

    def enter(self, phase: str) -> None:
        if phase not in CONTEXT_PHASES:
            raise ValueError(f"unknown context phase: {phase}")
        self.close_active()
        self._active = phase
        self._start = time.perf_counter()
        from .tracing import phase_started

        phase_started(phase)

    def close_active(self) -> None:
        if self._active is None:
            return
        elapsed = time.perf_counter() - self._start
        METRICS.observe("cgmcp_context_phase_latency_seconds", elapsed, phase=self._active)
        from .tracing import phase_finished

        phase_finished(self._active, elapsed)
        self._active = None

    def __enter__(self) -> ContextPhaseTracker:
        return self

    def __exit__(self, *args: object) -> None:
        self.close_active()
