from __future__ import annotations

import gzip
import io
import tarfile
import urllib.request
from pathlib import Path

from .store import DB_DIR


def export_snapshot(repo_root: Path, out_path: Path) -> Path:
    """Export .code-graph contents as a compressed tarball."""
    src_dir = repo_root / DB_DIR
    if not src_dir.exists():
        raise FileNotFoundError(f"missing snapshot source: {src_dir}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "w:gz"
    with tarfile.open(out_path, mode) as tar:
        tar.add(src_dir, arcname=DB_DIR)
    return out_path


def import_snapshot(repo_root: Path, in_path: Path) -> Path:
    """Import .code-graph contents from a compressed tarball."""
    target_dir = repo_root / DB_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(in_path, "r:gz") as tar:
        tar.extractall(path=repo_root)
    return target_dir


def fetch_snapshot(source: str, dest_path: Path) -> Path:
    """Fetch snapshot from local path or URL and return local file path."""
    if source.startswith("http://") or source.startswith("https://"):
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(source) as response:
            data = response.read()
        dest_path.write_bytes(data)
        return dest_path

    src = Path(source)
    if not src.exists():
        raise FileNotFoundError(f"snapshot source not found: {source}")
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(src.read_bytes())
    return dest_path
