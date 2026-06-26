"""Snapshot signing and verification (Phase 7.4, HMAC-SHA256 stdlib)."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path

SIG_SUFFIX = ".sig.json"
ALGORITHM_HMAC = "hmac-sha256"
ALGORITHM_ED25519 = "ed25519"
ALGORITHM = ALGORITHM_HMAC


class SigningVerificationError(Exception):
    """Raised when signature verification fails for a non-signature reason."""


def _signing_key() -> bytes | None:
    raw = os.environ.get("PCG_SNAPSHOT_KEY", "").strip()
    if not raw:
        return None
    return raw.encode("utf-8")


def _ed25519_private_key():
    path = os.environ.get("PCG_ED25519_KEY_PATH", "").strip()
    if path:
        key_bytes = Path(path).read_bytes()
    else:
        hex_key = os.environ.get("PCG_ED25519_KEY", "").strip()
        if not hex_key:
            return None
        key_bytes = bytes.fromhex(hex_key)
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError:
        return None
    return Ed25519PrivateKey.from_private_bytes(key_bytes[:32])


def _preferred_algorithm() -> str:
    if os.environ.get("PCG_SNAPSHOT_SIGNING", "").lower() == "ed25519":
        return ALGORITHM_ED25519
    if _ed25519_private_key() is not None and not _signing_key():
        return ALGORITHM_ED25519
    return ALGORITHM_HMAC


def signature_path(archive_path: Path) -> Path:
    return Path(str(archive_path) + SIG_SUFFIX)


def sign_file(path: Path) -> Path | None:
    algo = _preferred_algorithm()
    if algo == ALGORITHM_ED25519:
        private_key = _ed25519_private_key()
        if private_key is None:
            return None
        digest = private_key.sign(path.read_bytes()).hex()
        sig_path = signature_path(path)
        payload = {"algorithm": ALGORITHM_ED25519, "digest": digest, "file": path.name}
        sig_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return sig_path

    key = _signing_key()
    if key is None:
        return None
    digest = hmac.new(key, path.read_bytes(), hashlib.sha256).hexdigest()
    sig_path = signature_path(path)
    payload = {"algorithm": ALGORITHM_HMAC, "digest": digest, "file": path.name}
    sig_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return sig_path


def verify_file(path: Path) -> bool:
    sig_path = signature_path(path)
    if not sig_path.exists():
        return os.environ.get("PCG_REQUIRE_SIGNED_SNAPSHOTS", "").lower() not in {
            "1",
            "true",
            "yes",
        }
    try:
        payload = json.loads(sig_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    algorithm = payload.get("algorithm", ALGORITHM_HMAC)
    if algorithm == ALGORITHM_ED25519:
        pub_path = os.environ.get("PCG_ED25519_PUBLIC_KEY_PATH", "").strip()
        if not pub_path:
            return False
        try:
            from cryptography.exceptions import InvalidSignature
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        except ImportError:
            return False
        public_key = Ed25519PublicKey.from_public_bytes(Path(pub_path).read_bytes()[:32])
        try:
            signature = bytes.fromhex(str(payload.get("digest", "")))
        except ValueError:
            return False
        try:
            public_key.verify(signature, path.read_bytes())
        except InvalidSignature:
            return False
        except (TypeError, ValueError) as exc:
            raise SigningVerificationError(f"invalid ed25519 signature payload: {exc}") from exc
        return True

    key = _signing_key()
    if key is None:
        return False
    if algorithm != ALGORITHM_HMAC:
        return False
    expected = hmac.new(key, path.read_bytes(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(str(payload.get("digest", "")), expected)
