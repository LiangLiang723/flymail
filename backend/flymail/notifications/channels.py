"""Stateless notification channel adapters and HTTP result classification."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol
from urllib.parse import quote

import httpx

from flymail.notifications.contracts import (
    CHANNEL_KEYS,
    DeliveryResult,
    HttpRequest,
    HttpResponse,
    NotificationConfig,
    NotificationHttpTransport,
    NotificationMessage,
    ProxyConfig,
    default_resolver,
    validate_public_http_url,
)


class NotificationChannel(Protocol):
    async def send(
        self,
        message: NotificationMessage,
        config: NotificationConfig,
        proxy: ProxyConfig | None,
    ) -> DeliveryResult: ...


class HttpxNotificationTransport:
    async def send(self, request: HttpRequest) -> HttpResponse:
        kwargs: dict[str, object] = {
            "headers": dict(request.headers),
            "timeout": httpx.Timeout(20.0, connect=10.0),
            "follow_redirects": False,
        }
        if request.proxy_url:
            kwargs["proxy"] = request.proxy_url
        async with httpx.AsyncClient(**kwargs) as client:
            if request.method == "DELETE":
                response = await client.delete(request.url)
            elif request.content is not None:
                headers = dict(request.headers)
                headers.setdefault("Content-Type", request.content_type)
                response = await client.post(
                    request.url,
                    content=request.content,
                    headers=headers,
                )
            else:
                response = await client.post(
                    request.url,
                    json=dict(request.json_body),
                )
        try:
            decoded = response.json()
        except (ValueError, TypeError):
            decoded = {}
        if not isinstance(decoded, Mapping):
            decoded = {}
        return HttpResponse(response.status_code, decoded, response.text)


def _required(config: Mapping[str, object], key: str) -> str:
    value = str(config.get(key) or "").strip()
    if not value:
        raise ValueError(f"notification configuration requires {key}")
    return value


def _proxy_url(proxy: ProxyConfig | None) -> str | None:
    return proxy.url if proxy is not None else None


def _classify(response: HttpResponse) -> DeliveryResult:
    code = response.status_code
    external_id = ""
    for key in ("id", "message_id", "task_id", "request_id"):
        value = response.json_data.get(key)
        if value is not None:
            external_id = str(value)
            break
    if 200 <= code <= 299:
        return DeliveryResult("succeeded", external_id, "notification accepted")
    if code in {408, 409, 425, 429} or code >= 500:
        return DeliveryResult("retry", safe_detail=f"notification HTTP {code}")
    return DeliveryResult("failed", safe_detail=f"notification HTTP {code}")


def _classify_provider_status(
    response: HttpResponse,
    *,
    status_key: str,
    success_values: tuple[object, ...],
    retry_codes: tuple[int, ...] = (),
    retry_from: int | None = None,
) -> DeliveryResult:
    base = _classify(response)
    if base.status != "succeeded" or status_key not in response.json_data:
        return base
    status_value = response.json_data.get(status_key)
    if status_value in success_values:
        return base
    error_value = response.json_data.get("error_code", status_value)
    try:
        numeric_error = int(error_value)
    except (TypeError, ValueError):
        numeric_error = 0
    if numeric_error in retry_codes or (
        retry_from is not None and numeric_error >= retry_from
    ):
        return DeliveryResult(
            "retry",
            safe_detail=f"notification provider error {numeric_error or 'unknown'}",
        )
    return DeliveryResult(
        "failed",
        safe_detail=f"notification provider error {numeric_error or 'unknown'}",
    )


class _HttpChannel:
    channel_key: str

    def __init__(self, transport: NotificationHttpTransport, resolver) -> None:
        self.transport = transport
        self.resolver = resolver

    async def _send(
        self,
        request: HttpRequest,
        *,
        provider_status_key: str | None = None,
        success_values: tuple[object, ...] = (),
        retry_codes: tuple[int, ...] = (),
        retry_from: int | None = None,
    ) -> DeliveryResult:
        try:
            response = await self.transport.send(request)
        except (httpx.TimeoutException, httpx.NetworkError, OSError):
            return DeliveryResult("retry", safe_detail="notification network failure")
        if provider_status_key is not None:
            return _classify_provider_status(
                response,
                status_key=provider_status_key,
                success_values=success_values,
                retry_codes=retry_codes,
                retry_from=retry_from,
            )
        return _classify(response)

    def _validate_config(self, config: NotificationConfig) -> None:
        if config.channel_key != self.channel_key:
            raise ValueError("notification channel configuration does not match adapter")


class InAppChannel(_HttpChannel):
    channel_key = "in_app"

    async def send(
        self,
        message: NotificationMessage,
        config: NotificationConfig,
        proxy: ProxyConfig | None,
    ) -> DeliveryResult:
        self._validate_config(config)
        return DeliveryResult("succeeded", safe_detail="in-app event already persisted")


class BarkChannel(_HttpChannel):
    channel_key = "bark"

    async def send(self, message, config, proxy) -> DeliveryResult:
        self._validate_config(config)
        server = str(
            config.public_config.get("endpoint_url")
            or config.public_config.get("server")
            or "https://api.day.app"
        ).strip().rstrip("/")
        device_key = _required(config.secret_config, "device_key")
        endpoint = validate_public_http_url(
            f"{server}/{quote(device_key, safe='')}",
            resolver=self.resolver,
            require_https=True,
        )
        payload: dict[str, object] = {
            "title": message.title,
            "body": message.summary or "\u200b",
        }
        if message.image_url:
            payload["image"] = message.image_url
        return await self._send(
            HttpRequest(
                "POST",
                endpoint,
                json_body=payload,
                proxy_url=_proxy_url(proxy),
            ),
            provider_status_key="code",
            success_values=(200,),
            retry_from=500,
        )


class TelegramChannel(_HttpChannel):
    channel_key = "telegram"

    async def send(self, message, config, proxy) -> DeliveryResult:
        self._validate_config(config)
        token = _required(config.secret_config, "bot_token")
        chat_id = _required(config.public_config, "chat_id")
        if message.image_url:
            endpoint = validate_public_http_url(
                f"https://api.telegram.org/bot{token}/sendPhoto",
                resolver=self.resolver,
                require_https=True,
            )
            payload: dict[str, object] = {
                "chat_id": chat_id,
                "photo": message.image_url,
            }
        else:
            endpoint = validate_public_http_url(
                f"https://api.telegram.org/bot{token}/sendMessage",
                resolver=self.resolver,
                require_https=True,
            )
            text = f"{message.title}\n\n{message.summary}"
            if message.action_path:
                text += f"\n\n{message.action_path}"
            payload = {
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True,
            }
        return await self._send(
            HttpRequest(
                "POST",
                endpoint,
                json_body=payload,
                proxy_url=_proxy_url(proxy),
            ),
            provider_status_key="ok",
            success_values=(True,),
            retry_codes=(408, 409, 425, 429),
            retry_from=500,
        )


class WeComChannel(_HttpChannel):
    channel_key = "wecom"

    async def send(self, message, config, proxy) -> DeliveryResult:
        self._validate_config(config)
        endpoint = validate_public_http_url(
            _required(config.secret_config, "webhook_url"),
            resolver=self.resolver,
            require_https=True,
        )
        content = f"**{message.title}**\n{message.summary}"
        if message.action_path:
            content += f"\n{message.action_path}"
        if message.image_url:
            content += f"\n{message.image_url}"
        return await self._send(
            HttpRequest(
                "POST",
                endpoint,
                json_body={"msgtype": "markdown", "markdown": {"content": content}},
                proxy_url=_proxy_url(proxy),
            ),
            provider_status_key="errcode",
            success_values=(0,),
        )


class DingTalkChannel(_HttpChannel):
    channel_key = "dingtalk"

    async def send(self, message, config, proxy) -> DeliveryResult:
        self._validate_config(config)
        endpoint = validate_public_http_url(
            _required(config.secret_config, "webhook_url"),
            resolver=self.resolver,
            require_https=True,
        )
        text = f"### {message.title}\n\n{message.summary}"
        if message.action_path:
            text += f"\n\n{message.action_path}"
        if message.image_url:
            text += f"\n\n![]({message.image_url})"
        return await self._send(
            HttpRequest(
                "POST",
                endpoint,
                json_body={
                    "msgtype": "markdown",
                    "markdown": {"title": message.title, "text": text},
                },
                proxy_url=_proxy_url(proxy),
            ),
            provider_status_key="errcode",
            success_values=(0,),
        )


class FeishuChannel(_HttpChannel):
    channel_key = "feishu"

    async def send(self, message, config, proxy) -> DeliveryResult:
        self._validate_config(config)
        endpoint = validate_public_http_url(
            _required(config.secret_config, "webhook_url"),
            resolver=self.resolver,
            require_https=True,
        )
        content: dict[str, object] = {
            "title": message.title,
            "text": message.summary,
            "action_path": message.action_path or "",
        }
        if message.image_url:
            content["image_url"] = message.image_url
        return await self._send(
            HttpRequest(
                "POST",
                endpoint,
                json_body={"msg_type": "interactive", "content": content},
                proxy_url=_proxy_url(proxy),
            ),
            provider_status_key="code",
            success_values=(0,),
        )


class GenericWebhookChannel(_HttpChannel):
    channel_key = "generic_webhook"

    async def send(self, message, config, proxy) -> DeliveryResult:
        self._validate_config(config)
        endpoint = validate_public_http_url(
            _required(config.public_config, "endpoint_url"),
            resolver=self.resolver,
        )
        headers: dict[str, str] = {"Content-Type": "application/json"}
        authorization = str(config.secret_config.get("authorization") or "").strip()
        if authorization:
            headers["Authorization"] = authorization
        payload: dict[str, object] = {
            "event_id": message.event_id,
            "event_type": message.event_type,
            "title": message.title,
            "summary": message.summary,
            "action_path": message.action_path or "",
            "occurred_at": message.occurred_at,
            "account_id": message.account_id or "",
        }
        if message.image_url:
            payload["image_url"] = message.image_url
        return await self._send(
            HttpRequest(
                "POST",
                endpoint,
                headers=headers,
                json_body=payload,
                proxy_url=_proxy_url(proxy),
            )
        )


class ChannelRegistry:
    def __init__(self, channels: Mapping[str, NotificationChannel]) -> None:
        self._channels = dict(channels)
        if set(self._channels) != set(CHANNEL_KEYS):
            raise ValueError("notification channel registry must define all stable keys")

    @classmethod
    def default(
        cls,
        transport: NotificationHttpTransport | None = None,
        *,
        resolver=default_resolver,
    ) -> "ChannelRegistry":
        active_transport = transport or HttpxNotificationTransport()
        return cls(
            {
                "in_app": InAppChannel(active_transport, resolver),
                "bark": BarkChannel(active_transport, resolver),
                "telegram": TelegramChannel(active_transport, resolver),
                "wecom": WeComChannel(active_transport, resolver),
                "dingtalk": DingTalkChannel(active_transport, resolver),
                "feishu": FeishuChannel(active_transport, resolver),
                "generic_webhook": GenericWebhookChannel(active_transport, resolver),
            }
        )

    def get(self, channel_key: str) -> NotificationChannel:
        key = str(channel_key or "").strip()
        try:
            return self._channels[key]
        except KeyError as exc:
            raise KeyError(f"unknown notification channel: {key}") from exc
