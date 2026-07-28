"""HTTP CONNECT proxy helpers for IMAP/SMTP sockets."""
from __future__ import annotations

import base64
import socket
from urllib.parse import unquote, urlparse

from utils.logger import get_logger

logger = get_logger("proxy")


def _parse_proxy_url(proxy_url: str) -> tuple[str, int, str, str]:
    parsed = urlparse((proxy_url or "").strip())
    if parsed.scheme.lower() != "http" or not parsed.hostname:
        raise ValueError("代理地址必须使用 http://host:port 格式")
    try:
        port = parsed.port or 8080
    except ValueError as exc:
        raise ValueError("代理端口格式无效") from exc
    if not 1 <= int(port) <= 65535:
        raise ValueError("代理端口必须在 1 到 65535 之间")
    username = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    return parsed.hostname, int(port), username, password


def create_proxy_socket(
    proxy_url: str,
    target_host: str,
    target_port: int,
    timeout: int | float = 30,
) -> socket.socket:
    """Open a TCP tunnel through an HTTP CONNECT proxy.

    Authentication credentials may be supplied in the proxy URL. They are
    written only to the CONNECT request and are never included in logs or
    user-facing errors.
    """
    proxy_host, proxy_port, username, password = _parse_proxy_url(proxy_url)
    if not target_host or not 1 <= int(target_port) <= 65535:
        raise ValueError("代理目标地址无效")

    addr_infos = socket.getaddrinfo(
        proxy_host,
        proxy_port,
        socket.AF_INET,
        socket.SOCK_STREAM,
    )
    if not addr_infos:
        raise socket.gaierror("无法解析代理服务器地址")

    af, socktype, proto, _canonname, sockaddr = addr_infos[0]
    sock = socket.socket(af, socktype, proto)
    try:
        sock.settimeout(timeout)
        sock.connect(sockaddr)
        lines = [
            f"CONNECT {target_host}:{int(target_port)} HTTP/1.1",
            f"Host: {target_host}:{int(target_port)}",
            "Proxy-Connection: Keep-Alive",
        ]
        if username:
            token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
            lines.append(f"Proxy-Authorization: Basic {token}")
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))

        response = b""
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("代理服务器未返回 CONNECT 响应")
            response += chunk
            if len(response) > 8192:
                raise ConnectionError("代理 CONNECT 响应头过长")

        status_line = response.split(b"\r\n", 1)[0].decode("ascii", errors="replace")
        parts = status_line.split(" ", 2)
        if len(parts) < 2 or parts[1] != "200":
            status = parts[1] if len(parts) >= 2 else "unknown"
            raise ConnectionError(f"代理 CONNECT 被拒绝（HTTP {status}）")

        logger.debug(
            "代理隧道建立成功: proxy_host=%s proxy_port=%d target=%s:%d",
            proxy_host,
            proxy_port,
            target_host,
            int(target_port),
        )
        return sock
    except Exception:
        sock.close()
        raise
