"""Authenticated encryption for FlyMail V2 provider credentials."""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


_ALGORITHM = "AES-256-GCM"
_HKDF_INFO = b"flymail-v2/credentials/v1"
_NONCE_BYTES = 12


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)


def _aad(account_id: str) -> bytes:
    normalized = str(account_id or "").strip()
    if not normalized:
        raise ValueError("account_id is required")
    return f"flymail:{normalized}:provider-credential:v1".encode("utf-8")


@dataclass(frozen=True, slots=True)
class EncryptedValue:
    algorithm: str
    key_version: int
    nonce_b64: str = field(repr=False)
    ciphertext_b64: str = field(repr=False)


class CredentialCipher:
    __slots__ = ("_key", "key_version")

    def __init__(self, key: bytes, key_version: int) -> None:
        self._key = bytes(key)
        self.key_version = int(key_version)

    @classmethod
    def from_master_secret(cls, secret: str, key_version: int = 1) -> "CredentialCipher":
        if not isinstance(secret, str) or len(secret) < 16:
            raise ValueError("master secret must be at least 16 characters")
        if int(key_version) < 1:
            raise ValueError("key version must be at least 1")
        key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=_HKDF_INFO,
        ).derive(secret.encode("utf-8"))
        return cls(key, int(key_version))

    def encrypt(self, account_id: str, plaintext: bytes) -> EncryptedValue:
        if not isinstance(plaintext, bytes):
            raise TypeError("plaintext must be bytes")
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = AESGCM(self._key).encrypt(nonce, plaintext, _aad(account_id))
        return EncryptedValue(
            algorithm=_ALGORITHM,
            key_version=self.key_version,
            nonce_b64=_encode(nonce),
            ciphertext_b64=_encode(ciphertext),
        )

    def decrypt(self, account_id: str, value: EncryptedValue) -> bytes:
        if value.algorithm != _ALGORITHM:
            raise ValueError(f"unsupported credential algorithm: {value.algorithm}")
        if int(value.key_version) != self.key_version:
            raise ValueError(
                f"credential key version {value.key_version} does not match active key version {self.key_version}"
            )
        nonce = _decode(value.nonce_b64)
        ciphertext = _decode(value.ciphertext_b64)
        if len(nonce) != _NONCE_BYTES:
            raise ValueError("credential nonce must be 12 bytes")
        try:
            return AESGCM(self._key).decrypt(nonce, ciphertext, _aad(account_id))
        except InvalidTag:
            raise

    def __repr__(self) -> str:
        return f"CredentialCipher(algorithm={_ALGORITHM!r}, key_version={self.key_version})"
