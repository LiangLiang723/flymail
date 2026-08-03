"""Blocking IMAP/SMTP network adapters with optional HTTP CONNECT tunneling."""

from __future__ import annotations

import base64
import imaplib
import json
import smtplib
import socket
import ssl
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote, urlsplit

from imapclient import IMAPClient

from flymail.providers.contracts import (
    ProviderEndpoints,
    ServiceEndpoint,
    TransportSecurity,
)
from flymail.providers.registry import ProviderRegistry
from flymail.repositories.accounts import MailAccount


SocketConnectionFactory = Callable[[tuple[str, int], float], socket.socket]
ImapClientFactory = Callable[[ServiceEndpoint, str | None], Any]
SmtpClientFactory = Callable[[ServiceEndpoint, str | None], Any]


@dataclass(frozen=True, slots=True, repr=False)
class RuntimeCredential:
    username: str
    secret: str = field(repr=False)
    auth_kind: str = "password"
    refresh_token: str = field(default="", repr=False)
    expires_at: float = 0.0

    def __post_init__(self) -> None:
        username = str(self.username or "").strip()
        secret = str(self.secret or "")
        auth_kind = str(self.auth_kind or "").strip().casefold()
        if not username or not secret:
            raise ValueError("runtime credential username and secret are required")
        if auth_kind not in {"password", "oauth"}:
            raise ValueError("runtime credential auth kind is invalid")
        object.__setattr__(self, "username", username)
        object.__setattr__(self, "secret", secret)
        object.__setattr__(self, "auth_kind", auth_kind)
        object.__setattr__(self, "refresh_token", str(self.refresh_token or ""))
        object.__setattr__(self, "expires_at", max(float(self.expires_at or 0), 0.0))

    def __repr__(self) -> str:
        return (
            "RuntimeCredential("
            f"username={self.username!r}, auth_kind={self.auth_kind!r}, "
            f"has_refresh_token={bool(self.refresh_token)!r}, expires_at={self.expires_at!r})"
        )


@dataclass(frozen=True, slots=True)
class ResolvedAccountEndpoints:
    imap: ServiceEndpoint
    smtp: ServiceEndpoint


def decode_runtime_credential(
    account: MailAccount,
    credential_type: str,
    plaintext: bytes,
) -> RuntimeCredential:
    if not isinstance(account, MailAccount):
        raise TypeError("account must be MailAccount")
    kind = str(credential_type or "").strip().casefold()
    value = bytes(plaintext)
    if kind in {"password", "authorization_code"}:
        secret = value.decode("utf-8")
        return RuntimeCredential(account.email, secret, "password")
    if kind != "oauth":
        raise ValueError("unsupported credential type")
    try:
        payload = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("OAuth credential is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("OAuth credential is invalid")
    try:
        expires_at = float(payload.get("expires_at") or 0)
    except (TypeError, ValueError):
        expires_at = 0.0
    return RuntimeCredential(
        account.email,
        str(payload.get("access_token") or ""),
        "oauth",
        refresh_token=str(payload.get("refresh_token") or ""),
        expires_at=expires_at,
    )


def _configured_endpoint(value: object, protocol: str) -> ServiceEndpoint:
    if not isinstance(value, Mapping):
        raise ValueError(f"{protocol} endpoint configuration is required")
    try:
        security = TransportSecurity(str(value.get("security") or "").strip().casefold())
    except ValueError as exc:
        raise ValueError(f"{protocol} endpoint security is invalid") from exc
    return ServiceEndpoint(
        str(value.get("host") or ""),
        int(value.get("port") or 0),
        security,
    )


def resolve_account_endpoints(
    account: MailAccount,
    registry: ProviderRegistry,
) -> ResolvedAccountEndpoints:
    if not isinstance(account, MailAccount):
        raise TypeError("account must be MailAccount")
    if not isinstance(registry, ProviderRegistry):
        raise TypeError("registry must be ProviderRegistry")
    if account.endpoint_config:
        return ResolvedAccountEndpoints(
            _configured_endpoint(account.endpoint_config.get("imap"), "imap"),
            _configured_endpoint(account.endpoint_config.get("smtp"), "smtp"),
        )
    endpoints: ProviderEndpoints = registry.get(account.provider_key).default_endpoints()
    email = account.normalized_email.casefold()
    for variant in endpoints.variants:
        if any(email.endswith(suffix) for suffix in variant.domain_suffixes):
            return ResolvedAccountEndpoints(variant.imap, variant.smtp)
    if endpoints.imap is None or endpoints.smtp is None:
        raise ValueError("mail account endpoints are unavailable")
    return ResolvedAccountEndpoints(endpoints.imap, endpoints.smtp)


def create_http_connect_socket(
    proxy_url: str,
    target_host: str,
    target_port: int,
    *,
    timeout: float,
    connection_factory: SocketConnectionFactory = socket.create_connection,
) -> socket.socket:
    parsed = urlsplit(str(proxy_url or ""))
    if parsed.scheme.casefold() != "http" or not parsed.hostname:
        raise ValueError("HTTP CONNECT proxy URL is invalid")
    proxy_port = parsed.port or 80
    host = str(target_host or "").strip()
    port = int(target_port)
    if not host or not 1 <= port <= 65535:
        raise ValueError("proxy target is invalid")
    sock = connection_factory((parsed.hostname, proxy_port), float(timeout))
    try:
        headers = [
            f"CONNECT {host}:{port} HTTP/1.1",
            f"Host: {host}:{port}",
            "Proxy-Connection: Keep-Alive",
        ]
        if parsed.username is not None:
            username = unquote(parsed.username)
            password = unquote(parsed.password or "")
            encoded = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
            headers.append(f"Proxy-Authorization: Basic {encoded}")
        request = ("\r\n".join(headers) + "\r\n\r\n").encode("ascii")
        sock.sendall(request)
        response = bytearray()
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response.extend(chunk)
            if len(response) > 16 * 1024:
                raise ConnectionError("HTTP CONNECT proxy response is too large")
        status_line = bytes(response).split(b"\r\n", 1)[0]
        parts = status_line.split(b" ", 2)
        if len(parts) < 2 or parts[1] != b"200":
            raise ConnectionError("HTTP CONNECT proxy rejected the target")
        return sock
    except BaseException:
        sock.close()
        raise


def _open_runtime_socket(
    host: str,
    port: int,
    timeout: float,
    proxy_url: str | None,
) -> socket.socket:
    if proxy_url:
        return create_http_connect_socket(
            proxy_url,
            host,
            port,
            timeout=timeout,
        )
    return socket.create_connection((host, port), timeout)


class _RuntimeIMAP4(imaplib.IMAP4):
    def __init__(self, host: str, port: int, timeout: float, proxy_url: str | None) -> None:
        self._runtime_timeout = timeout
        self._runtime_proxy_url = proxy_url
        super().__init__(host, port, timeout=timeout)

    def _create_socket(self, timeout: float | None = None):
        return _open_runtime_socket(
            self.host,
            self.port,
            float(timeout or self._runtime_timeout),
            self._runtime_proxy_url,
        )


class _RuntimeIMAP4SSL(imaplib.IMAP4_SSL):
    def __init__(
        self,
        host: str,
        port: int,
        timeout: float,
        proxy_url: str | None,
        ssl_context: ssl.SSLContext,
    ) -> None:
        self._runtime_timeout = timeout
        self._runtime_proxy_url = proxy_url
        super().__init__(host, port, ssl_context=ssl_context, timeout=timeout)

    def _create_socket(self, timeout: float | None = None):
        raw = _open_runtime_socket(
            self.host,
            self.port,
            float(timeout or self._runtime_timeout),
            self._runtime_proxy_url,
        )
        try:
            return self.ssl_context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


class _RuntimeIMAPClient(IMAPClient):
    def __init__(self, endpoint: ServiceEndpoint, proxy_url: str | None) -> None:
        self._runtime_endpoint = endpoint
        self._runtime_proxy_url = proxy_url
        super().__init__(
            endpoint.host,
            port=endpoint.port,
            use_uid=True,
            ssl=endpoint.security is TransportSecurity.TLS,
            ssl_context=ssl.create_default_context(),
            timeout=30.0,
        )

    def _create_IMAP4(self):
        endpoint = self._runtime_endpoint
        if endpoint.security is TransportSecurity.TLS:
            return _RuntimeIMAP4SSL(
                endpoint.host,
                endpoint.port,
                30.0,
                self._runtime_proxy_url,
                ssl.create_default_context(),
            )
        return _RuntimeIMAP4(
            endpoint.host,
            endpoint.port,
            30.0,
            self._runtime_proxy_url,
        )


def create_imap_client(endpoint: ServiceEndpoint, proxy_url: str | None):
    return _RuntimeIMAPClient(endpoint, proxy_url)


class BlockingImapSession:
    def __init__(
        self,
        endpoint: ServiceEndpoint,
        credential: RuntimeCredential,
        *,
        proxy_url: str | None,
        client_factory: ImapClientFactory = create_imap_client,
    ) -> None:
        self.endpoint = endpoint
        self.credential = credential
        self.proxy_url = proxy_url
        self.client_factory = client_factory
        self.client: Any | None = None

    def connect(self) -> Any:
        if self.client is not None:
            return self.client
        client = self.client_factory(self.endpoint, self.proxy_url)
        try:
            if self.endpoint.security is TransportSecurity.STARTTLS:
                client.starttls()
            if self.credential.auth_kind == "oauth":
                client.oauth2_login(self.credential.username, self.credential.secret)
            else:
                client.login(self.credential.username, self.credential.secret)
        except BaseException:
            try:
                client.logout()
            except Exception:
                pass
            raise
        self.client = client
        return client

    def close(self) -> None:
        client, self.client = self.client, None
        if client is None:
            return
        try:
            client.logout()
        except Exception:
            return

    def __enter__(self) -> "BlockingImapSession":
        self.connect()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()


class _RuntimeSMTP(smtplib.SMTP):
    def __init__(self, endpoint: ServiceEndpoint, proxy_url: str | None) -> None:
        self._runtime_proxy_url = proxy_url
        super().__init__(endpoint.host, endpoint.port, timeout=30.0)

    def _get_socket(self, host: str, port: int, timeout: float):
        return _open_runtime_socket(host, port, float(timeout or 30), self._runtime_proxy_url)


class _RuntimeSMTPSSL(smtplib.SMTP_SSL):
    def __init__(self, endpoint: ServiceEndpoint, proxy_url: str | None) -> None:
        self._runtime_proxy_url = proxy_url
        super().__init__(
            endpoint.host,
            endpoint.port,
            timeout=30.0,
            context=ssl.create_default_context(),
        )

    def _get_socket(self, host: str, port: int, timeout: float):
        raw = _open_runtime_socket(host, port, float(timeout or 30), self._runtime_proxy_url)
        try:
            return self.context.wrap_socket(raw, server_hostname=host)
        except BaseException:
            raw.close()
            raise


def create_smtp_client(endpoint: ServiceEndpoint, proxy_url: str | None):
    if endpoint.security is TransportSecurity.TLS:
        return _RuntimeSMTPSSL(endpoint, proxy_url)
    return _RuntimeSMTP(endpoint, proxy_url)


class BlockingSmtpSession:
    def __init__(
        self,
        endpoint: ServiceEndpoint,
        credential: RuntimeCredential,
        *,
        proxy_url: str | None,
        client_factory: SmtpClientFactory = create_smtp_client,
    ) -> None:
        self.endpoint = endpoint
        self.credential = credential
        self.proxy_url = proxy_url
        self.client_factory = client_factory
        self.client: Any | None = None

    def connect(self) -> Any:
        if self.client is not None:
            return self.client
        client = self.client_factory(self.endpoint, self.proxy_url)
        try:
            client.ehlo()
            if self.endpoint.security is TransportSecurity.STARTTLS:
                client.starttls()
                client.ehlo()
            if self.credential.auth_kind == "oauth":
                raw = (
                    f"user={self.credential.username}\x01"
                    f"auth=Bearer {self.credential.secret}\x01\x01"
                ).encode("utf-8")
                encoded = base64.b64encode(raw).decode("ascii")
                code, _response = client.docmd("AUTH", f"XOAUTH2 {encoded}")
                if int(code) != 235:
                    raise smtplib.SMTPAuthenticationError(code, b"OAuth authentication failed")
            else:
                client.login(self.credential.username, self.credential.secret)
        except BaseException:
            try:
                client.quit()
            except Exception:
                pass
            raise
        self.client = client
        return client

    def close(self) -> None:
        client, self.client = self.client, None
        if client is None:
            return
        try:
            client.quit()
        except Exception:
            return

    def __enter__(self) -> "BlockingSmtpSession":
        self.connect()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()


__all__ = [
    "BlockingImapSession",
    "BlockingSmtpSession",
    "ResolvedAccountEndpoints",
    "RuntimeCredential",
    "create_http_connect_socket",
    "create_imap_client",
    "create_smtp_client",
    "decode_runtime_credential",
    "resolve_account_endpoints",
]
