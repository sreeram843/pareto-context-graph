"""Build-phase timing helpers (Phase 10.1)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

META_KEY = "build_profile"


@dataclass
class BuildTimings:
    """Wall-clock seconds per build phase."""

    phases: dict[str, float] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    def start(self, name: str) -> float:
        return time.perf_counter()

    def stop(self, name: str, started: float) -> None:
        self.phases[name] = self.phases.get(name, 0.0) + (time.perf_counter() - started)

    def total(self) -> float:
        return sum(self.phases.values())

    def to_dict(self) -> dict[str, Any]:
        total = self.total()
        pct = {
            name: round(100.0 * seconds / total, 1)
            for name, seconds in self.phases.items()
            if total > 0
        }
        return {
            "phases_sec": {name: round(seconds, 3) for name, seconds in self.phases.items()},
            "total_sec": round(total, 3),
            "pct": pct,
            **self.meta,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str | None) -> BuildTimings | None:
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        phases = payload.get("phases_sec", {})
        meta = {
            key: payload[key] for key in payload if key not in {"phases_sec", "total_sec", "pct"}
        }
        return cls(phases={str(k): float(v) for k, v in phases.items()}, meta=meta)


def read_build_profile(store) -> dict[str, Any] | None:
    raw = store.get_meta(META_KEY)
    timings = BuildTimings.from_json(raw)
    return timings.to_dict() if timings else None
