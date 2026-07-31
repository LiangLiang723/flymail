"""Scrypt password hashing for FlyMail V2 local users."""

from __future__ import annotations

import base64
import hmac
import os

from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


_VERSION = 1
_N = 32768
_R = 8
_P = 1
_SALT_BYTES = 16
_DIGEST_BYTES = 32


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)


def _derive(password: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=_DIGEST_BYTES, n=_N, r=_R, p=_P).derive(
        password.encode("utf-8")
    )


def hash_password(password: str) -> str:
    """Hash any non-empty administrative/import password with random salt."""

    if not isinstance(password, str) or not password:
        raise ValueError("password must be a non-empty string")
    salt = os.urandom(_SALT_BYTES)
    digest = _derive(password, salt)
    return (
        f"scrypt$v={_VERSION}$n={_N}$r={_R}$p={_P}$"
        f"{_encode(salt)}${_encode(digest)}"
    )


def verify_password(password: str, encoded: str) -> bool:
    """Verify a password without propagating malformed stored-hash errors."""

    if not isinstance(password, str) or not isinstance(encoded, str):
        return False
    try:
        parts = encoded.split("$")
        if parts[:5] != ["scrypt", "v=1", "n=32768", "r=8", "p=1"] or len(parts) != 7:
            return False
        salt = _decode(parts[5])
        expected = _decode(parts[6])
        if len(salt) != _SALT_BYTES or len(expected) != _DIGEST_BYTES:
            return False
        actual = _derive(password, salt)
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False
