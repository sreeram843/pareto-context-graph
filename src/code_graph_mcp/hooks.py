from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(api|secret)_?key\s*=\s*['\"][^'\"]+['\"]"),
]


def load_hooks(repo_root: Path) -> list[ModuleType]:
    hook_dir = repo_root / ".code-graph" / "hooks"
    if not hook_dir.exists():
        return []

    modules: list[ModuleType] = []
    for py_file in sorted(hook_dir.glob("*.py")):
        spec = importlib.util.spec_from_file_location(f"code_graph_hook_{py_file.stem}", py_file)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    return modules


def run_pre_context_hooks(repo_root: Path, payload: dict) -> dict:
    for module in load_hooks(repo_root):
        func = getattr(module, "pre_context", None)
        if callable(func):
            payload = func(payload) or payload
    return payload


def run_post_context_hooks(repo_root: Path, response: dict) -> dict:
    for module in load_hooks(repo_root):
        func = getattr(module, "post_context", None)
        if callable(func):
            response = func(response) or response
    return response


def run_post_build_hooks(repo_root: Path, result: dict) -> dict:
    for module in load_hooks(repo_root):
        func = getattr(module, "post_build", None)
        if callable(func):
            result = func(result) or result
    return result


def run_post_update_hooks(repo_root: Path, result: dict) -> dict:
    for module in load_hooks(repo_root):
        func = getattr(module, "post_update", None)
        if callable(func):
            result = func(result) or result
    return result


def redact_text(text: str) -> str:
    output = text
    for pattern in SECRET_PATTERNS:
        output = pattern.sub("***REDACTED***", output)
    return output


def redact_context_payload(response: dict) -> dict:
    for entry in response.get("context_files", []):
        if "content" in entry and isinstance(entry["content"], str):
            entry["content"] = redact_text(entry["content"])
        for chunk in entry.get("chunks", []):
            body = chunk.get("body")
            if isinstance(body, str):
                chunk["body"] = redact_text(body)
    return response
