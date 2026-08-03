"""Production Gmail and Outlook OAuth gateway contracts."""

from __future__ import annotations

import asyncio
import time
import unittest
from urllib.parse import parse_qs, urlsplit

from flymail.domain.errors import ConfigurationError, ConflictError


class FakeOAuthTransport:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict, str | None]] = []

    async def post_form(self, url: str, data: dict, *, proxy_url: str | None) -> dict:
        self.calls.append((url, dict(data), proxy_url))
        if not self.responses:
            raise AssertionError("unexpected OAuth transport call")
        return dict(self.responses.pop(0))


class ProviderOAuthRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def configs(self):
        from flymail.providers.oauth import OAuthProviderConfig

        return {
            "gmail": OAuthProviderConfig(
                client_id="gmail-client-id",
                client_secret="gmail-client-secret",
                authorization_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
                token_endpoint="https://oauth2.googleapis.com/token",
                scopes=("https://mail.google.com/",),
                authorization_parameters={"access_type": "offline", "prompt": "consent"},
            ),
            "outlook": OAuthProviderConfig(
                client_id="outlook-client-id",
                client_secret="outlook-client-secret",
                authorization_endpoint="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
                token_endpoint="https://login.microsoftonline.com/common/oauth2/v2.0/token",
                scopes=(
                    "offline_access",
                    "https://outlook.office.com/IMAP.AccessAsUser.All",
                    "https://outlook.office.com/SMTP.Send",
                ),
            ),
        }

    def test_authorization_url_uses_pkce_state_and_exact_provider_scope(self):
        from flymail.providers.oauth import ProductionOAuthGateway

        gateway = ProductionOAuthGateway(configs=self.configs(), transport=FakeOAuthTransport([]))
        gmail = gateway.build_authorization_url(
            provider_key="gmail",
            state="state-value",
            code_challenge="challenge-value",
            redirect_uri="https://mail.example.test/oauth/callback",
            proxy_url="http://proxy-user:proxy-secret@proxy.example.test:8080",
        )
        gmail_query = parse_qs(urlsplit(gmail).query)
        self.assertEqual(gmail_query["client_id"], ["gmail-client-id"])
        self.assertEqual(gmail_query["state"], ["state-value"])
        self.assertEqual(gmail_query["code_challenge"], ["challenge-value"])
        self.assertEqual(gmail_query["code_challenge_method"], ["S256"])
        self.assertEqual(gmail_query["scope"], ["https://mail.google.com/"])
        self.assertNotIn("proxy-secret", gmail)

        outlook = gateway.build_authorization_url(
            provider_key="outlook",
            state="state-value",
            code_challenge="challenge-value",
            redirect_uri="https://mail.example.test/oauth/callback",
            proxy_url=None,
        )
        outlook_query = parse_qs(urlsplit(outlook).query)
        self.assertEqual(
            outlook_query["scope"],
            [
                "offline_access https://outlook.office.com/IMAP.AccessAsUser.All "
                "https://outlook.office.com/SMTP.Send"
            ],
        )

    async def test_code_exchange_and_refresh_use_user_proxy_and_absolute_expiry(self):
        from flymail.providers.oauth import ProductionOAuthGateway

        transport = FakeOAuthTransport(
            [
                {"access_token": "access-one", "refresh_token": "refresh-one", "expires_in": 3600},
                {"access_token": "access-two", "expires_in": 1800},
            ]
        )
        gateway = ProductionOAuthGateway(configs=self.configs(), transport=transport, now_fn=lambda: 1000.0)
        token = await gateway.exchange_code(
            provider_key="gmail",
            code="authorization-code",
            code_verifier="verifier-value",
            redirect_uri="https://mail.example.test/oauth/callback",
            proxy_url="http://proxy.example.test:8080",
        )
        self.assertEqual(token["access_token"], "access-one")
        self.assertEqual(token["refresh_token"], "refresh-one")
        self.assertEqual(token["expires_at"], 4600.0)
        self.assertEqual(transport.calls[0][2], "http://proxy.example.test:8080")
        self.assertEqual(transport.calls[0][1]["code_verifier"], "verifier-value")

        refreshed = await gateway.refresh_token(
            provider_key="gmail",
            refresh_token="refresh-one",
            proxy_url="http://proxy.example.test:8080",
        )
        self.assertEqual(refreshed["access_token"], "access-two")
        self.assertEqual(refreshed["refresh_token"], "refresh-one")
        self.assertEqual(refreshed["expires_at"], 2800.0)

    def test_partial_environment_client_configuration_is_rejected(self):
        from flymail.providers.oauth import oauth_configs_from_env

        with self.assertRaises(ConfigurationError):
            oauth_configs_from_env({"GMAIL_CLIENT_ID": "gmail-client"})
        with self.assertRaises(ConfigurationError):
            oauth_configs_from_env({"OUTLOOK_CLIENT_SECRET": "outlook-secret"})

    def test_missing_or_unsupported_provider_configuration_is_rejected_safely(self):
        from flymail.providers.oauth import ProductionOAuthGateway

        gateway = ProductionOAuthGateway(configs={}, transport=FakeOAuthTransport([]))
        with self.assertRaises(ConflictError) as missing:
            gateway.build_authorization_url(
                provider_key="gmail",
                state="state",
                code_challenge="challenge",
                redirect_uri="https://mail.example.test/oauth/callback",
                proxy_url=None,
            )
        self.assertNotIn("secret", str(missing.exception).casefold())
        with self.assertRaises(ConflictError):
            gateway.build_authorization_url(
                provider_key="qq",
                state="state",
                code_challenge="challenge",
                redirect_uri="https://mail.example.test/oauth/callback",
                proxy_url=None,
            )


if __name__ == "__main__":
    unittest.main()
