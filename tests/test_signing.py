"""Tests for signing verification behavior."""

from pathlib import Path

import pytest

from pareto_context_graph.signing import sign_file, verify_file


def test_hmac_roundtrip(tmp_path: Path, monkeypatch):
    archive = tmp_path / "snap.tar.gz"
    archive.write_bytes(b"payload")
    monkeypatch.setenv("PCG_SNAPSHOT_KEY", "test-secret")
    sign_file(archive)
    assert verify_file(archive) is True
    archive.write_bytes(b"tampered")
    assert verify_file(archive) is False


def test_ed25519_invalid_hex_raises(monkeypatch, tmp_path: Path):
    archive = tmp_path / "snap.tar.gz"
    archive.write_bytes(b"payload")
    sig = tmp_path / "snap.tar.gz.sig.json"
    sig.write_text(
        '{"algorithm": "ed25519", "digest": "not-hex", "file": "snap.tar.gz"}\n',
        encoding="utf-8",
    )
    pub = tmp_path / "pub.key"
    pub.write_bytes(b"\x00" * 32)
    monkeypatch.setenv("PCG_ED25519_PUBLIC_KEY_PATH", str(pub))
    pytest.importorskip("cryptography")
    assert verify_file(archive) is False
