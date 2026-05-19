from __future__ import annotations

import hashlib
import json
import math
import struct
from pathlib import Path
from typing import Protocol

from .store import DB_DIR


class EmbeddingsBackend(Protocol):
    def encode(self, texts: list[str]) -> list[list[float]]: ...


class DeterministicNoopBackend:
    """Dependency-free embedding backend used as safe default."""

    def __init__(self, dims: int = 32) -> None:
        self.dims = dims

    def encode(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).digest()
            vals = []
            for i in range(self.dims):
                byte = digest[i % len(digest)]
                vals.append((byte / 255.0) - 0.5)
            norm = math.sqrt(sum(v * v for v in vals)) or 1.0
            vectors.append([v / norm for v in vals])
        return vectors


class OpenAIBackend:
    """Adapter for OpenAI embeddings API. Requires OPENAI_API_KEY env var."""

    def __init__(self, model: str = "text-embedding-3-small") -> None:
        self.model = model

    def encode(self, texts: list[str]) -> list[list[float]]:
        import json
        import os
        import urllib.request

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not set")

        url = "https://api.openai.com/v1/embeddings"
        payload = json.dumps({"input": texts, "model": self.model}).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
        return [item["embedding"] for item in data["data"]]


class OllamaBackend:
    """Adapter for local Ollama embeddings. Requires running Ollama server."""

    def __init__(
        self,
        model: str = "nomic-embed-text",
        base_url: str = "http://localhost:11434",
    ) -> None:
        self.model = model
        self.base_url = base_url

    def encode(self, texts: list[str]) -> list[list[float]]:
        import json
        import urllib.request

        vectors: list[list[float]] = []
        for text in texts:
            payload = json.dumps({"model": self.model, "prompt": text}).encode()
            req = urllib.request.Request(
                f"{self.base_url}/api/embeddings",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read())
            vectors.append(data["embedding"])
        return vectors


def _paths(repo_root: Path) -> list[str]:
    paths: list[str] = []
    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(repo_root).as_posix()
        if rel.startswith(".git/") or rel.startswith(f"{DB_DIR}/"):
            continue
        paths.append(rel)
    return sorted(paths)


def build_embeddings(repo_root: Path, backend: EmbeddingsBackend | None = None) -> dict:
    backend = backend or DeterministicNoopBackend()
    paths = _paths(repo_root)

    texts: list[str] = []
    for rel in paths:
        fp = repo_root / rel
        try:
            content = fp.read_text(errors="ignore")[:2000]
        except OSError:
            content = ""
        texts.append(f"{rel}\n{content}")

    vectors = backend.encode(texts)
    out_dir = repo_root / DB_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    vec_path = out_dir / "embeddings.bin"
    idx_path = out_dir / "embeddings.index.json"

    dims = len(vectors[0]) if vectors else 0
    with vec_path.open("wb") as handle:
        for vec in vectors:
            handle.write(struct.pack(f"<{len(vec)}f", *vec))

    idx_path.write_text(
        json.dumps(
            {
                "dims": dims,
                "count": len(paths),
                "paths": paths,
            },
            indent=2,
        )
        + "\n"
    )

    return {"count": len(paths), "dims": dims, "vector_file": str(vec_path), "index_file": str(idx_path)}


def load_embedding_index(repo_root: Path) -> dict | None:
    idx_path = repo_root / DB_DIR / "embeddings.index.json"
    if not idx_path.exists():
        return None
    return json.loads(idx_path.read_text())


def _read_vector_at(vec_path: Path, dims: int, index: int) -> list[float]:
    if dims <= 0:
        return []
    stride = dims * 4
    with vec_path.open("rb") as handle:
        handle.seek(index * stride)
        buf = handle.read(stride)
    if len(buf) != stride:
        return []
    return list(struct.unpack(f"<{dims}f", buf))


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def query_embedding_scores(repo_root: Path, query: str, paths: list[str]) -> dict[str, float]:
    index = load_embedding_index(repo_root)
    if not index:
        return {}
    dims = int(index.get("dims", 0))
    all_paths = index.get("paths", [])
    if not isinstance(all_paths, list):
        return {}

    vec_path = repo_root / DB_DIR / "embeddings.bin"
    if not vec_path.exists():
        return {}

    backend = DeterministicNoopBackend(dims=max(1, dims or 32))
    qvec = backend.encode([query])[0]

    pos_by_path = {p: i for i, p in enumerate(all_paths)}
    scores: dict[str, float] = {}
    for path in paths:
        idx = pos_by_path.get(path)
        if idx is None:
            continue
        vec = _read_vector_at(vec_path, dims, idx)
        if vec:
            scores[path] = _cosine(qvec, vec)
    return scores
