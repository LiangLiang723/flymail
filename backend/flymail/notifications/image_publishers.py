"""Optional notification image publisher adapters."""

from __future__ import annotations

from collections.abc import Mapping

import httpx

from flymail.notifications.channels import HttpxNotificationTransport
from flymail.notifications.contracts import (
    ImageAsset,
    ImagePublisherConfig,
    NotificationHttpTransport,
    PublishedImage,
    ProxyConfig,
    PUBLISHER_KEYS,
    HttpRequest,
    default_resolver,
    validate_public_http_url,
)


def _required(config: Mapping[str, object], key: str) -> str:
    value = str(config.get(key) or "").strip()
    if not value:
        raise ValueError(f"image publisher configuration requires {key}")
    return value


class _BasePublisher:
    publisher_key: str

    def __init__(self, transport: NotificationHttpTransport, resolver) -> None:
        self.transport = transport
        self.resolver = resolver

    def _validate(self, config: ImagePublisherConfig) -> str:
        if config.publisher_key != self.publisher_key:
            raise ValueError("image publisher configuration does not match adapter")
        return validate_public_http_url(
            config.endpoint_url,
            resolver=self.resolver,
            require_https=True,
        )

    async def cleanup(
        self,
        published: PublishedImage,
        config: ImagePublisherConfig,
        proxy: ProxyConfig | None,
    ) -> None:
        if not published.cleanup_supported or not published.delete_url:
            return
        headers = self._headers(config)
        try:
            await self.transport.send(
                HttpRequest(
                    "DELETE",
                    validate_public_http_url(
                        published.delete_url,
                        resolver=self.resolver,
                        require_https=True,
                    ),
                    headers=headers,
                    proxy_url=proxy.url if proxy else None,
                )
            )
        except (httpx.TimeoutException, httpx.NetworkError, OSError):
            return

    def _headers(self, config: ImagePublisherConfig) -> dict[str, str]:
        raise NotImplementedError

    def _published(
        self,
        response,
        *,
        url_field: str,
        cleanup_default: bool,
    ) -> PublishedImage:
        if not 200 <= response.status_code <= 299:
            raise RuntimeError("image publisher request failed")
        url = str(response.json_data.get(url_field) or response.json_data.get("url") or "").strip()
        if not url:
            raise RuntimeError("image publisher response did not contain a public URL")
        url = validate_public_http_url(
            url,
            resolver=self.resolver,
            require_https=True,
        )
        delete_url = str(response.json_data.get("delete_url") or "").strip() or None
        if delete_url:
            delete_url = validate_public_http_url(
                delete_url,
                resolver=self.resolver,
                require_https=True,
            )
        expires_at = response.json_data.get("expires_at")
        return PublishedImage(
            url=url,
            cleanup_supported=bool(delete_url) or cleanup_default,
            delete_url=delete_url,
            expires_at=float(expires_at) if expires_at is not None else None,
        )


class FlyMailImgBedPublisher(_BasePublisher):
    publisher_key = "flymail_imgbed"

    def _headers(self, config: ImagePublisherConfig) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {_required(config.secret_config, 'token')}",
            "Content-Type": "application/octet-stream",
            "X-FlyMail-Filename": "notification-image",
        }

    async def publish(self, asset, config, proxy) -> PublishedImage:
        endpoint = self._validate(config)
        headers = self._headers(config)
        headers["X-FlyMail-Filename"] = asset.filename
        headers["Content-Type"] = asset.content_type
        response = await self.transport.send(
            HttpRequest(
                "POST",
                endpoint,
                headers=headers,
                content=asset.content,
                content_type=asset.content_type,
                proxy_url=proxy.url if proxy else None,
            )
        )
        return self._published(
            response,
            url_field="url",
            cleanup_default=False,
        )


class GenericHttpsPublisher(_BasePublisher):
    publisher_key = "generic_https"

    def _headers(self, config: ImagePublisherConfig) -> dict[str, str]:
        headers = {"Content-Type": "application/octet-stream"}
        authorization = str(config.secret_config.get("authorization") or "").strip()
        if authorization:
            headers["Authorization"] = authorization
        return headers

    async def publish(self, asset, config, proxy) -> PublishedImage:
        endpoint = self._validate(config)
        headers = self._headers(config)
        headers["Content-Type"] = asset.content_type
        headers["X-Filename"] = asset.filename
        response = await self.transport.send(
            HttpRequest(
                "POST",
                endpoint,
                headers=headers,
                content=asset.content,
                content_type=asset.content_type,
                proxy_url=proxy.url if proxy else None,
            )
        )
        url_field = str(config.public_config.get("url_field") or "url")
        return self._published(
            response,
            url_field=url_field,
            cleanup_default=bool(config.public_config.get("cleanup_supported")),
        )


class ImagePublisherRegistry:
    def __init__(self, publishers: Mapping[str, object]) -> None:
        self._publishers = dict(publishers)
        if set(self._publishers) != set(PUBLISHER_KEYS):
            raise ValueError("image publisher registry must define all stable keys")

    @classmethod
    def default(
        cls,
        transport: NotificationHttpTransport | None = None,
        *,
        resolver=default_resolver,
    ) -> "ImagePublisherRegistry":
        active_transport = transport or HttpxNotificationTransport()
        return cls(
            {
                "flymail_imgbed": FlyMailImgBedPublisher(active_transport, resolver),
                "generic_https": GenericHttpsPublisher(active_transport, resolver),
            }
        )

    def get(self, publisher_key: str):
        key = str(publisher_key or "").strip()
        try:
            return self._publishers[key]
        except KeyError as exc:
            raise KeyError(f"unknown image publisher: {key}") from exc
