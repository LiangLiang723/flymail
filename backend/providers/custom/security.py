"""Security validation for user-configured IMAP/SMTP endpoints."""

from __future__ import annotations

import ipaddress
import socket
from typing import Iterable


_ALLOWED_SECURITY_MODES = frozenset({"ssl", "starttls"})


def normalize_security_mode(value: str) -> str:
    """Return a supported encrypted transport mode.

    Plaintext authentication is intentionally unsupported because account
    passwords and authorization codes must never cross the network in clear
    text.
    """
    normalized = (value or "").strip().lower()
    if normalized not in _ALLOWED_SECURITY_MODES:
        raise ValueError("加密方式仅支持 SSL/TLS 或 STARTTLS")
    return normalized


def normalize_mail_host(value: str) -> str:
    """Normalize and validate a mail server host without accepting URLs."""
    host = (value or "").strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if not host:
        raise ValueError("邮件服务器地址不能为空")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in host):
        raise ValueError("邮件服务器地址包含非法字符")
    if "://" in host or any(ch in host for ch in ("/", "@", "?", "#")):
        raise ValueError("邮件服务器地址只能填写主机名或 IP 地址")
    host = host.rstrip(".").lower()
    if not host:
        raise ValueError("邮件服务器地址不能为空")
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    if len(host) > 253:
        raise ValueError("邮件服务器域名过长")
    labels = host.split(".")
    for label in labels:
        if not label or len(label) > 63:
            raise ValueError("邮件服务器域名格式不正确")
        if label[0] == "-" or label[-1] == "-":
            raise ValueError("邮件服务器域名格式不正确")
        if not all(ch.isalnum() or ch == "-" for ch in label):
            raise ValueError("邮件服务器域名格式不正确")
    return host


def _validate_port(port: int) -> int:
    try:
        normalized = int(port)
    except (TypeError, ValueError) as exc:
        raise ValueError("邮件服务器端口必须是数字") from exc
    if not 1 <= normalized <= 65535:
        raise ValueError("邮件服务器端口必须在 1 到 65535 之间")
    return normalized


def _address_from_sockaddr(sockaddr: tuple) -> ipaddress._BaseAddress:
    if not sockaddr:
        raise ValueError("邮件服务器解析结果无效")
    try:
        address = ipaddress.ip_address(sockaddr[0])
    except ValueError as exc:
        raise ValueError("邮件服务器解析结果无效") from exc
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address


def _ensure_all_public(results: Iterable[tuple]) -> list[tuple]:
    validated = list(results)
    if not validated:
        raise ValueError("无法解析邮件服务器地址")
    for item in validated:
        if len(item) < 5:
            raise ValueError("邮件服务器解析结果无效")
        address = _address_from_sockaddr(item[4])
        if not address.is_global:
            raise ValueError("邮件服务器地址不能指向本机、内网或保留网络")
    return validated


def resolve_public_addresses(host: str, port: int) -> list[tuple]:
    """Resolve a mail server and reject any non-public DNS answer.

    Rejecting the whole hostname when one answer is restricted prevents a
    resolver from selecting an unchecked private address after validation.
    Callers should connect using one of the returned address tuples.
    """
    normalized_host = normalize_mail_host(host)
    normalized_port = _validate_port(port)
    try:
        results = socket.getaddrinfo(
            normalized_host,
            normalized_port,
            socket.AF_UNSPEC,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
        )
    except socket.gaierror as exc:
        raise ValueError("无法解析邮件服务器地址") from exc
    return _ensure_all_public(results)


def validate_server_config(host: str, port: int, security_mode: str) -> tuple[str, int, str, list[tuple]]:
    """Validate and normalize one IMAP or SMTP endpoint."""
    normalized_host = normalize_mail_host(host)
    normalized_port = _validate_port(port)
    normalized_mode = normalize_security_mode(security_mode)
    addresses = resolve_public_addresses(normalized_host, normalized_port)
    return normalized_host, normalized_port, normalized_mode, addresses


def open_public_socket(host: str, port: int, timeout: float | None = None) -> socket.socket:
    """Connect to one already-validated public address without a second DNS lookup."""
    normalized_host = normalize_mail_host(host)
    normalized_port = _validate_port(port)
    addresses = resolve_public_addresses(normalized_host, normalized_port)
    last_error: OSError | None = None
    for family, socktype, proto, _canonname, sockaddr in addresses:
        sock = socket.socket(family, socktype, proto)
        try:
            sock.settimeout(timeout)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            last_error = exc
            sock.close()
    if last_error is not None:
        raise last_error
    raise ConnectionError("无法连接邮件服务器")
