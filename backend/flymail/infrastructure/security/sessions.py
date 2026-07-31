"""Session token and signed-cookie primitives for FlyMail V2."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


_COOKIE_VERSION = "v1"
_HKDF_INFO = b"flymail-v2/session-signing/v1"
_TOKEN_BYTES = 32


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)


def _signing_key(secret: bytes) -> bytes:
    if not isinstance(secret, bytes) or len(secret) < 16:
        raise ValueError("session secret must be at least 16 bytes")
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=_HKDF_INFO,
    ).derive(secret)


def new_session_token() -> tuple[str, str]:
    raw_token = _encode(os.urandom(_TOKEN_BYTES))
    token_hash = hashlib.sha256(raw_token.encode("ascii")).hexdigest()
    return raw_token, token_hash


def sign_session_cookie(session_id: str, secret: bytes) -> str:
    normalized = str(session_id or "").strip()
    if not normalized:
        raise ValueError("session_id is required")
    payload = f"{_COOKIE_VERSION}.{_encode(normalized.encode('utf-8'))}"
    signature = hmac.new(_signing_key(secret), payload.encode("ascii"), hashlib.sha256).digest()
    return f"{payload}.{_encode(signature)}"


def verify_session_cookie(cookie: str, secret: bytes) -> str:
    try:
        version, encoded_session, encoded_signature = str(cookie or "").split(".")
        if version != _COOKIE_VERSION:
            raise InvalidSignature
        payload = f"{version}.{encoded_session}"
        supplied_signature = _decode(encoded_signature)
        expected_signature = hmac.new(
            _signing_key(secret), payload.encode("ascii"), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise InvalidSignature
        session_id = _decode(encoded_session).decode("utf-8")
        if not session_id:
            raise InvalidSignature
        return session_id
    except InvalidSignature:
        raise
    except (TypeError, ValueError, UnicodeError):
        raise InvalidSignature from None
