"""IPv4 强制连接子类

某些系统（如飞牛OS）优先解析 IPv6，导致 IMAP/SMTP 连接超时。
此模块提供统一的 IPv4 强制连接子类，供所有 Provider 共享使用。

使用方式：
    from providers.ipv4 import IPv4IMAP4_SSL, IPv4SMTP, IPv4SMTP_SSL

    # IMAP 连接（默认 SSL）
    conn = IPv4IMAP4_SSL(host, port, timeout=30)

    # SMTP 连接（STARTTLS 模式）
    smtp = IPv4SMTP(host, port, timeout=30)

    # SMTP 连接（SSL 直连模式）
    smtp = IPv4SMTP_SSL(host, port, timeout=30)
"""
import socket
import ssl
import imaplib
import smtplib


class IPv4IMAP4_SSL(imaplib.IMAP4_SSL):
    """强制 IPv4 的 IMAP4_SSL 子类

    覆盖 open() 方法，使用 socket.getaddrinfo(AF_INET) 强制 IPv4 解析，
    避免某些系统优先使用 IPv6 导致连接超时。
    """

    def open(self, host='', port=993, timeout=None):
        """建立 IPv4 SSL 连接"""
        addr_infos = socket.getaddrinfo(
            host, port or 993, socket.AF_INET, socket.SOCK_STREAM
        )
        if not addr_infos:
            raise socket.gaierror(f"无法解析 {host} 的 IPv4 地址")
        af, socktype, proto, canonname, sa = addr_infos[0]
        sock = socket.socket(af, socktype, proto)
        sock.settimeout(timeout or 30)
        sock.connect(sa)
        context = self._get_ssl_context()
        ssl_sock = context.wrap_socket(sock, server_hostname=host)
        self.host = host
        self.port = port
        self.sock = ssl_sock
        self._set_file_handle(self.sock.makefile('rb'))

    def _set_file_handle(self, file_obj):
        self._file = file_obj
        try:
            self.file = file_obj
        except AttributeError:
            pass

    def _get_ssl_context(self):
        """获取 SSL 上下文，子类可覆盖以自定义（如 Outlook 需要 TLS 1.2）"""
        return ssl.create_default_context()


class IPv4SMTP(smtplib.SMTP):
    """强制 IPv4 的 SMTP 子类（STARTTLS 模式）

    覆盖 _get_socket() 方法，使用 IPv4 强制解析。
    包含 timeout 哨兵值保护，避免 smtplib 传入 _GLOBAL_DEFAULT_TIMEOUT 时报错。
    """

    TIMEOUT = 30

    def _get_socket(self, host, port, timeout):
        """获取 IPv4 socket 连接"""
        if not isinstance(timeout, (int, float)):
            timeout = self.TIMEOUT
        addr_infos = socket.getaddrinfo(
            host, port, socket.AF_INET, socket.SOCK_STREAM
        )
        if not addr_infos:
            raise socket.gaierror(f"无法解析 {host} 的 IPv4 地址")
        af, socktype, proto, canonname, sa = addr_infos[0]
        sock = socket.socket(af, socktype, proto)
        sock.settimeout(timeout)
        sock.connect(sa)
        return sock


class ProxyIMAP4_SSL(IPv4IMAP4_SSL):
    """IMAP4_SSL over an authenticated HTTP CONNECT tunnel."""

    def __init__(self, host, port=993, proxy_url="", timeout=None):
        self._proxy_url = proxy_url
        super().__init__(host, port, timeout=timeout)

    def open(self, host='', port=993, timeout=None):
        from providers.proxy import create_proxy_socket

        target_host = host or getattr(self, "host", "")
        target_port = port or 993
        sock = create_proxy_socket(self._proxy_url, target_host, target_port, timeout or 30)
        try:
            ssl_sock = self._get_ssl_context().wrap_socket(sock, server_hostname=target_host)
        except Exception:
            sock.close()
            raise
        self.host = target_host
        self.port = target_port
        self.sock = ssl_sock
        self._set_file_handle(self.sock.makefile('rb'))


class ProxySMTP(IPv4SMTP):
    """SMTP STARTTLS connection over an HTTP CONNECT tunnel."""

    def __init__(self, host, port=587, proxy_url="", timeout=30):
        self._proxy_url = proxy_url
        super().__init__(host, port, timeout=timeout)

    def _get_socket(self, host, port, timeout):
        from providers.proxy import create_proxy_socket

        if not isinstance(timeout, (int, float)):
            timeout = self.TIMEOUT
        return create_proxy_socket(self._proxy_url, host, port, timeout)


class IPv4SMTP_SSL(smtplib.SMTP_SSL):
    """强制 IPv4 的 SMTP_SSL 子类（SSL 直连模式）

    覆盖 _get_socket() 方法，使用 IPv4 强制解析并包装 SSL。
    """

    TIMEOUT = 30

    def _get_socket(self, host, port, timeout):
        """获取 IPv4 SSL socket 连接"""
        if not isinstance(timeout, (int, float)):
            timeout = self.TIMEOUT
        addr_infos = socket.getaddrinfo(
            host, port, socket.AF_INET, socket.SOCK_STREAM
        )
        if not addr_infos:
            raise socket.gaierror(f"无法解析 {host} 的 IPv4 地址")
        af, socktype, proto, canonname, sa = addr_infos[0]
        sock = socket.socket(af, socktype, proto)
        sock.settimeout(timeout)
        sock.connect(sa)
        ssl_sock = self.context.wrap_socket(sock, server_hostname=self._host)
        return ssl_sock
