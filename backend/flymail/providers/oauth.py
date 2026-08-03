"""Production PKCE OAuth gateway for Gmail and Microsoft mail protocols."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlencode

import httpx

from flymail.domain.errors import ConfigurationError, ConflictError, RetryableError


@dataclass(frozen=True, slots=True, repr=False)
class OAuthProviderConfig:
    client_id: str
    client_secret: str
    authorization_endpoint: str
    token_endpoint: str
    scopes: tuple[str, ...]
    authorization_parameters: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        client_id = str(self.client_id or "").strip()
        client_secret = str(self.client_secret or "")
        authorization_endpoint = str(self.authorization_endpoint or "").strip()
        token_endpoint = str(self.token_endpoint or "").strip()
        scopes = tuple(str(value or "").strip() for value in self.scopes if str(value or "").strip())
        if not client_id or not client_secret:
            raise ValueError("OAuth client id and secret are required")
        if not authorization_endpoint.startswith("https://") or not token_endpoint.startswith("https://"):
            raise ValueError("OAuth endpoints must use HTTPS")
        if not scopes:
            raise ValueError("OAuth scopes are required")
        object.__setattr__(self, "client_id", client_id)
        object.__setattr__(self, "client_secret", client_secret)
        object.__setattr__(self, "authorization_endpoint", authorization_endpoint)
        object.__setattr__(self, "token_endpoint", token_endpoint)
        object.__setattr__(self, "scopes", scopes)
        object.__setattr__(
            self,
            "authorization_parameters",
            {str(key): str(value) for key, value in self.authorization_parameters.items()},
        )


class OAuthTransport(Protocol):
    async def post_form(
        self,
        url: str,
        data: dict,
        *,
        proxy_url: str | None,
    ) -> dict: ...


class HttpxOAuthTransport:
    async def post_form(
        self,
        url: str,
        data: dict,
        *,
        proxy_url: str | None,
    ) -> dict:
        try:
            async with httpx.AsyncClient(
                proxy=proxy_url,
                timeout=30.0,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    url,
                    data=data,
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            raise RetryableError("OAuth provider request failed") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise RetryableError("OAuth provider returned an invalid response") from exc
        if not isinstance(payload, dict):
            raise RetryableError("OAuth provider returned an invalid response")
        if response.status_code >= 400 or payload.get("error"):
            code = str(payload.get("error") or "oauth_error").strip().casefold()
            if code in {
                "invalid_grant",
                "invalid_client",
                "unauthorized_client",
                "invalid_request",
                "invalid_scope",
            }:
                raise ConflictError("OAuth authorization must be restarted")
            raise RetryableError("OAuth provider temporarily rejected the request")
        return dict(payload)


def oauth_configs_from_env(environ: Mapping[str, str] | None = None) -> dict[str, OAuthProviderConfig]:
    env = os.environ if environ is None else environ
    configs: dict[str, OAuthProviderConfig] = {}
    gmail_id = str(env.get("GMAIL_CLIENT_ID", "")).strip()
    gmail_secret = str(env.get("GMAIL_CLIENT_SECRET", ""))
    if bool(gmail_id) != bool(gmail_secret):
        raise ConfigurationError(
            "GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET must be configured together"
        )
    if gmail_id and gmail_secret:
        configs["gmail"] = OAuthProviderConfig(
            client_id=gmail_id,
            client_secret=gmail_secret,
            authorization_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
            token_endpoint="https://oauth2.googleapis.com/token",
            scopes=("https://mail.google.com/",),
            authorization_parameters={"access_type": "offline", "prompt": "consent"},
        )
    outlook_id = str(env.get("OUTLOOK_CLIENT_ID", "")).strip()
    outlook_secret = str(env.get("OUTLOOK_CLIENT_SECRET", ""))
    if bool(outlook_id) != bool(outlook_secret):
        raise ConfigurationError(
            "OUTLOOK_CLIENT_ID and OUTLOOK_CLIENT_SECRET must be configured together"
        )
    if outlook_id and outlook_secret:
        configs["outlook"] = OAuthProviderConfig(
            client_id=outlook_id,
            client_secret=outlook_secret,
            authorization_endpoint=(
                "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
            ),
            token_endpoint=(
                "https://login.microsoftonline.com/common/oauth2/v2.0/token"
            ),
            scopes=(
                "offline_access",
                "https://outlook.office.com/IMAP.AccessAsUser.All",
                "https://outlook.office.com/SMTP.Send",
            ),
        )
    return configs


class ProductionOAuthGateway:
    def __init__(
        self,
        *,
        configs: Mapping[str, OAuthProviderConfig] | None = None,
        transport: OAuthTransport | None = None,
        now_fn: Callable[[], float] = time.time,
    ) -> None:
        self.configs = {
            str(key or "").strip().casefold(): value
            for key, value in (oauth_configs_from_env() if configs is None else configs).items()
        }
        self.transport = transport or HttpxOAuthTransport()
        self.now_fn = now_fn

    def _config(self, provider_key: str) -> OAuthProviderConfig:
        key = str(provider_key or "").strip().casefold()
        if key not in {"gmail", "outlook"}:
            raise ConflictError("OAuth is not supported for this provider")
        config = self.configs.get(key)
        if config is None:
            raise ConflictError("OAuth provider client is not configured")
        return config

    def build_authorization_url(
        self,
        *,
        provider_key: str,
        state: str,
        code_challenge: str,
        redirect_uri: str,
        proxy_url: str | None,
    ) -> str:
        del proxy_url
        config = self._config(provider_key)
        query = {
            "client_id": config.client_id,
            "response_type": "code",
            "redirect_uri": str(redirect_uri or "").strip(),
            "scope": " ".join(config.scopes),
            "state": str(state or ""),
            "code_challenge": str(code_challenge or ""),
            "code_challenge_method": "S256",
            **dict(config.authorization_parameters),
        }
        if any(not str(query[key] or "").strip() for key in ("redirect_uri", "state", "code_challenge")):
            raise ValueError("OAuth authorization parameters are incomplete")
        return f"{config.authorization_endpoint}?{urlencode(query)}"

    async def exchange_code(
        self,
        *,
        provider_key: str,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        proxy_url: str | None,
    ) -> dict:
        config = self._config(provider_key)
        payload = await self.transport.post_form(
            config.token_endpoint,
            {
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "grant_type": "authorization_code",
                "code": str(code or ""),
                "code_verifier": str(code_verifier or ""),
                "redirect_uri": str(redirect_uri or ""),
            },
            proxy_url=proxy_url,
        )
        return self._normalize_token(payload)

    async def refresh_token(
        self,
        *,
        provider_key: str,
        refresh_token: str,
        proxy_url: str | None,
    ) -> dict:
        config = self._config(provider_key)
        normalized_refresh = str(refresh_token or "")
        if not normalized_refresh:
            raise ConflictError("OAuth authorization must be restarted")
        payload = await self.transport.post_form(
            config.token_endpoint,
            {
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": normalized_refresh,
                "scope": " ".join(config.scopes),
            },
            proxy_url=proxy_url,
        )
        normalized = self._normalize_token(payload)
        normalized.setdefault("refresh_token", normalized_refresh)
        return normalized

    def _normalize_token(self, payload: Mapping[str, object]) -> dict:
        token = dict(payload)
        access_token = str(token.get("access_token") or "")
        if not access_token:
            raise RetryableError("OAuth provider returned no access token")
        expires_at = token.get("expires_at")
        if expires_at is None:
            try:
                expires_in = max(float(token.get("expires_in") or 0), 0.0)
            except (TypeError, ValueError):
                expires_in = 0.0
            if expires_in:
                token["expires_at"] = float(self.now_fn()) + expires_in
        token.pop("expires_in", None)
        return token


__all__ = [
    "HttpxOAuthTransport",
    "OAuthProviderConfig",
    "OAuthTransport",
    "ProductionOAuthGateway",
    "oauth_configs_from_env",
]
