"""Channel-neutral notification, HTTP, proxy, and image publisher contracts."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping, Protocol, Sequence
from urllib.parse import quote, urlsplit, urlunsplit


EVENT_TYPES = frozenset(
    {
        "mail.new",
        "send.sent",
        "send.failed",
        "backup.completed",
        "backup.failed",
        "account.authorization_required",
        "system.storage_warning",
    }
)
CHANNEL_KEYS = frozenset(
    {
        "in_app",
        "bark",
        "telegram",
        "wecom",
        "dingtalk",
        "feishu",
        "generic_webhook",
    }
)
PUBLISHER_KEYS = frozenset({"flymail_imgbed", "generic_https"})


def _required_text(value: object, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    return normalized


def _safe_unicode(value: object) -> str:
    return str(value or "").encode("utf-8", errors="replace").decode("utf-8")


def _bounded(value: object, maximum: int) -> str:
    return _safe_unicode(value).strip()[:maximum]


def _freeze_mapping(value: Mapping[str, object] | None) -> Mapping[str, object]:
    if value is None:
        return MappingProxyType({})
    if not isinstance(value, Mapping):
        raise TypeError("configuration must be a mapping")
    return MappingProxyType({str(key): item for key, item in value.items()})


def default_resolver(host: str, port: int) -> tuple[str, ...]:
    addresses = []
    for family, _socktype, _proto, _canonname, sockaddr in socket.getaddrinfo(
        host,
        port,
        type=socket.SOCK_STREAM,
    ):
        if family not in {socket.AF_INET, socket.AF_INET6}:
            continue
        address = str(sockaddr[0])
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise ValueError("endpoint host did not resolve")
    return tuple(addresses)


def _public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return bool(address.is_global)


def validate_public_http_url(
    value: object,
    *,
    resolver=default_resolver,
    require_https: bool = False,
) -> str:
    normalized = _required_text(value, "endpoint URL")
    parsed = urlsplit(normalized)
    allowed_schemes = {"https"} if require_https else {"http", "https"}
    if parsed.scheme.casefold() not in allowed_schemes:
        raise ValueError("endpoint must use an allowed HTTP scheme")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("endpoint URL must not contain credentials")
    host = str(parsed.hostname or "").strip().rstrip(".")
    if not host:
        raise ValueError("endpoint host is required")
    if parsed.fragment:
        raise ValueError("endpoint URL must not contain a fragment")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("endpoint port is invalid") from exc
    normalized_port = port or (443 if parsed.scheme.casefold() == "https" else 80)
    try:
        addresses: Sequence[str] = (host,) if ipaddress.ip_address(host) else ()
    except ValueError:
        addresses = tuple(resolver(host, normalized_port))
    if not addresses or any(not _public_address(str(address)) for address in addresses):
        raise ValueError("endpoint must resolve only to public addresses")
    netloc = host
    if ":" in host and not host.startswith("["):
        netloc = f"[{host}]"
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit(
        (
            parsed.scheme.casefold(),
            netloc,
            parsed.path or "/",
            parsed.query,
            "",
        )
    )


@dataclass(frozen=True, slots=True)
class NotificationMessage:
    event_id: str
    event_type: str
    title: str
    summary: str
    action_path: str | None
    occurred_at: float
    account_id: str | None = None
    notification_asset_id: str | None = None
    image_url: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _required_text(self.event_id, "event_id"))
        event_type = _required_text(self.event_type, "event_type")
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unsupported notification event type: {event_type}")
        object.__setattr__(self, "event_type", event_type)
        title = _bounded(self.title, 160)
        if not title:
            raise ValueError("notification title is required")
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "summary", _bounded(self.summary, 700))
        action_path = str(self.action_path or "").strip()
        if action_path and (not action_path.startswith("/") or action_path.startswith("//")):
            raise ValueError("notification action_path must be an internal absolute path")
        object.__setattr__(self, "action_path", action_path or None)
        object.__setattr__(self, "occurred_at", float(self.occurred_at))
        object.__setattr__(
            self,
            "account_id",
            str(self.account_id or "").strip() or None,
        )
        object.__setattr__(
            self,
            "notification_asset_id",
            str(self.notification_asset_id or "").strip() or None,
        )
        image_url = str(self.image_url or "").strip()
        if image_url:
            image_url = validate_http_url_syntax(image_url, require_https=True)
        object.__setattr__(self, "image_url", image_url or None)

    def with_image_url(self, image_url: str | None) -> "NotificationMessage":
        return NotificationMessage(
            event_id=self.event_id,
            event_type=self.event_type,
            title=self.title,
            summary=self.summary,
            action_path=self.action_path,
            occurred_at=self.occurred_at,
            account_id=self.account_id,
            notification_asset_id=self.notification_asset_id,
            image_url=image_url,
        )


@dataclass(frozen=True, slots=True)
class NotificationConfig:
    channel_id: str
    channel_key: str
    public_config: Mapping[str, object] = field(default_factory=dict)
    secret_config: Mapping[str, object] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "channel_id", _required_text(self.channel_id, "channel_id"))
        key = _required_text(self.channel_key, "channel_key")
        if key not in CHANNEL_KEYS:
            raise ValueError(f"unsupported notification channel: {key}")
        object.__setattr__(self, "channel_key", key)
        object.__setattr__(self, "public_config", _freeze_mapping(self.public_config))
        object.__setattr__(self, "secret_config", _freeze_mapping(self.secret_config))


@dataclass(frozen=True, slots=True)
class ProxyConfig:
    proxy_id: str
    scheme: str
    host: str
    port: int
    username: str = ""
    password: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "proxy_id", _required_text(self.proxy_id, "proxy_id"))
        scheme = str(self.scheme or "").strip().casefold()
        if scheme != "http":
            raise ValueError("notification proxy must use http scheme")
        object.__setattr__(self, "scheme", scheme)
        object.__setattr__(self, "host", _required_text(self.host, "proxy host"))
        if isinstance(self.port, bool) or not 1 <= int(self.port) <= 65535:
            raise ValueError("proxy port must be between 1 and 65535")
        object.__setattr__(self, "port", int(self.port))
        object.__setattr__(self, "username", str(self.username or ""))
        object.__setattr__(self, "password", str(self.password or ""))

    @property
    def url(self) -> str:
        credentials = ""
        if self.username:
            credentials = quote(self.username, safe="")
            if self.password:
                credentials += f":{quote(self.password, safe='')}"
            credentials += "@"
        return f"{self.scheme}://{credentials}{self.host}:{self.port}"


@dataclass(frozen=True, slots=True)
class HttpRequest:
    method: str
    url: str = field(repr=False)
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    json_body: Mapping[str, object] = field(default_factory=dict, repr=False)
    content: bytes | None = field(default=None, repr=False)
    content_type: str = "application/json"
    proxy_url: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        method = str(self.method or "").strip().upper()
        if method not in {"POST", "DELETE"}:
            raise ValueError("unsupported notification HTTP method")
        object.__setattr__(self, "method", method)
        object.__setattr__(self, "url", _required_text(self.url, "request URL"))
        object.__setattr__(
            self,
            "headers",
            MappingProxyType({str(key): str(value) for key, value in self.headers.items()}),
        )
        object.__setattr__(self, "json_body", _freeze_mapping(self.json_body))
        if self.content is not None and not isinstance(self.content, bytes):
            raise TypeError("HTTP request content must be bytes")


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    json_data: Mapping[str, object]
    text: str = ""

    def __post_init__(self) -> None:
        code = int(self.status_code)
        if not 100 <= code <= 599:
            raise ValueError("HTTP status code is invalid")
        object.__setattr__(self, "status_code", code)
        object.__setattr__(self, "json_data", _freeze_mapping(self.json_data))
        object.__setattr__(self, "text", _bounded(self.text, 512))


class NotificationHttpTransport(Protocol):
    async def send(self, request: HttpRequest) -> HttpResponse: ...


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    status: str
    external_id: str = ""
    safe_detail: str = ""

    def __post_init__(self) -> None:
        status = str(self.status or "").strip().casefold()
        if status not in {"succeeded", "retry", "failed"}:
            raise ValueError("unsupported notification delivery status")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "external_id", _bounded(self.external_id, 191))
        object.__setattr__(self, "safe_detail", _bounded(self.safe_detail, 512))


@dataclass(frozen=True, slots=True)
class ImageAsset:
    asset_id: str
    filename: str
    content_type: str
    content: bytes = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset_id", _required_text(self.asset_id, "asset_id"))
        object.__setattr__(self, "filename", _required_text(self.filename, "filename"))
        content_type = str(self.content_type or "").strip().casefold()
        if not content_type.startswith("image/"):
            raise ValueError("notification asset must be an image")
        object.__setattr__(self, "content_type", content_type)
        if not isinstance(self.content, bytes):
            raise TypeError("image content must be bytes")


def validate_http_url_syntax(
    value: object,
    *,
    require_https: bool = False,
) -> str:
    return validate_public_http_url(
        value,
        resolver=lambda _host, _port: ("8.8.8.8",),
        require_https=require_https,
    )


@dataclass(frozen=True, slots=True)
class ImagePublisherConfig:
    publisher_id: str
    publisher_key: str
    endpoint_url: str
    public_config: Mapping[str, object] = field(default_factory=dict)
    secret_config: Mapping[str, object] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "publisher_id", _required_text(self.publisher_id, "publisher_id"))
        key = _required_text(self.publisher_key, "publisher_key")
        if key not in PUBLISHER_KEYS:
            raise ValueError(f"unsupported image publisher: {key}")
        object.__setattr__(self, "publisher_key", key)
        object.__setattr__(
            self,
            "endpoint_url",
            validate_http_url_syntax(self.endpoint_url, require_https=True),
        )
        object.__setattr__(self, "public_config", _freeze_mapping(self.public_config))
        object.__setattr__(self, "secret_config", _freeze_mapping(self.secret_config))


@dataclass(frozen=True, slots=True)
class PublishedImage:
    url: str
    cleanup_supported: bool = False
    delete_url: str | None = field(default=None, repr=False)
    expires_at: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "url",
            validate_http_url_syntax(self.url, require_https=True),
        )
        if not isinstance(self.cleanup_supported, bool):
            raise TypeError("cleanup_supported must be bool")
        delete_url = str(self.delete_url or "").strip()
        if delete_url:
            delete_url = validate_http_url_syntax(delete_url, require_https=True)
        object.__setattr__(self, "delete_url", delete_url or None)
        if self.expires_at is not None:
            object.__setattr__(self, "expires_at", float(self.expires_at))


class NotificationImagePublisher(Protocol):
    async def publish(
        self,
        asset: ImageAsset,
        config: ImagePublisherConfig,
        proxy: ProxyConfig | None,
    ) -> PublishedImage: ...

    async def cleanup(
        self,
        published: PublishedImage,
        config: ImagePublisherConfig,
        proxy: ProxyConfig | None,
    ) -> None: ...
