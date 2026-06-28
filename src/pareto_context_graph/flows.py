"""Loader + validator for flow/architecture ground truth (Phase 1.1).

The flow ground truth (``tests/eval/flows/ground-truth.json``) drives the agent A/B +
LLM judge scorecard: each flow is a verified call path through a repo. This module
loads and structurally validates the file, and offers :func:`verify_call_path` to
confirm the cited ``file:line`` symbols still exist in a checked-out repo (used by the
self-test so the ground truth can't silently rot).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_FLOWS_PATH = Path("tests/eval/flows/ground-truth.json")
# How far from the cited line the symbol may appear before we call it stale.
LINE_TOLERANCE = 5


@dataclass
class CallStep:
    symbol: str
    file: str
    line: int
    note: str = ""


@dataclass
class Flow:
    flow_id: str
    repo_key: str
    repo_sha: str
    question: str
    call_path: list[CallStep]
    must_hit_symbols: list[str]
    dynamic_boundaries: list[str]
    size: str = ""
    memory_probe_required: bool = True

    @classmethod
    def from_dict(cls, d: dict) -> Flow:
        return cls(
            flow_id=d["flow_id"],
            repo_key=d["repo_key"],
            repo_sha=d.get("repo_sha", ""),
            question=d["question"],
            call_path=[CallStep(**{k: s[k] for k in ("symbol", "file", "line") if k in s},
                                note=s.get("note", "")) for s in d["call_path"]],
            must_hit_symbols=list(d.get("must_hit_symbols", [])),
            dynamic_boundaries=list(d.get("dynamic_boundaries", [])),
            size=d.get("size", ""),
            memory_probe_required=bool(d.get("memory_probe_required", True)),
        )


def load_flows(path: Path | None = None) -> list[Flow]:
    path = path or DEFAULT_FLOWS_PATH
    blob = json.loads(Path(path).read_text())
    return [Flow.from_dict(f) for f in blob.get("flows", [])]


def _short_symbol(symbol: str) -> str:
    """Last dotted component, e.g. ``Client.send`` -> ``send``."""
    return symbol.split(".")[-1]


def verify_call_path(flow: Flow, repo_root: Path) -> list[str]:
    """Return a list of human-readable problems with this flow's call path.

    An empty list means every cited file exists and each symbol's defining name
    appears within ``LINE_TOLERANCE`` lines of the cited line. Steps for files that
    do not exist (wrong checkout) are reported rather than silently skipped.
    """
    problems: list[str] = []
    for step in flow.call_path:
        fp = repo_root / step.file
        if not fp.is_file():
            problems.append(f"{flow.flow_id}: missing file {step.file}")
            continue
        lines = fp.read_text(errors="ignore").splitlines()
        if not (1 <= step.line <= len(lines)):
            problems.append(
                f"{flow.flow_id}: {step.file}:{step.line} out of range (1..{len(lines)})"
            )
            continue
        name = _short_symbol(step.symbol)
        window = lines[max(0, step.line - 1 - LINE_TOLERANCE) : step.line + LINE_TOLERANCE]
        if not any(name in line for line in window):
            problems.append(
                f"{flow.flow_id}: symbol '{name}' not found near {step.file}:{step.line}"
            )
    return problems
