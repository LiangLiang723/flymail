"""V2 production IMAP/SMTP credential, endpoint, and proxy contracts."""

from __future__ import annotations

import json
import unittest

from flymail.providers.contracts import ServiceEndpoint, TransportSecurity
from flymail.repositories.accounts import MailAccount


class FakeSocket:
    def __init__(self, response: bytes) -> None:
        self.response = response
        self.sent = b""
        self.closed = False

    def sendall(self, value: bytes) -> None:
        self.sent += bytes(value)

    def recv(self, _size: int) -> bytes:
        value, self.response = self.response, b""
        return value

    def close(self) -> None:
        self.closed = True


class FakeImapClient:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.capabilities = {b"IMAP4rev1", b"UIDPLUS"}

    def starttls(self) -> None:
        self.calls.append(("starttls",))

    def login(self, username: str, secret: str) -> None:
        self.calls.append(("login", username, secret))

    def oauth2_login(self, username: str, secret: str) -> None:
        self.calls.append(("oauth2_login", username, secret))

    def logout(self) -> None:
        self.calls.append(("logout",))


class FakeSmtpClient:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def ehlo(self):
        self.calls.append(("ehlo",))
        return 250, b"ok"

    def starttls(self):
        self.calls.append(("starttls",))
        return 220, b"ready"

    def login(self, username: str, secret: str):
        self.calls.append(("login", username, secret))
        return 235, b"ok"

    def docmd(self, command: str, argument: str):
        self.calls.append(("docmd", command, argument))
        return 235, b"ok"

    def quit(self):
        self.calls.append(("quit",))
        return 221, b"bye"


class ProviderNetworkRuntimeTests(unittest.TestCase):
    def account(self, *, provider_key: str = "generic", email: str = "user@example.test", endpoint_config=None):
        return MailAccount(
            id="acc_network_1",
            user_uid="usr_network_1",
            provider_key=provider_key,
            email=email,
            normalized_email=email,
            display_name="",
            remark="",
            group_name="",
            status="active",
            endpoint_config=dict(endpoint_config or {}),
            icon_mode="provider",
            icon_value="",
            icon_object_sha256=None,
            poll_interval_seconds=300,
            created_at=1,
            updated_at=1,
        )

    def test_password_and_oauth_credentials_are_secret_free_and_exact(self):
        from flymail.providers.network import decode_runtime_credential

        password = decode_runtime_credential(
            self.account(),
            "password",
            b"mail-password",
        )
        self.assertEqual(password.username, "user@example.test")
        self.assertEqual(password.secret, "mail-password")
        self.assertEqual(password.auth_kind, "password")
        self.assertNotIn("mail-password", repr(password))

        oauth = decode_runtime_credential(
            self.account(provider_key="gmail"),
            "oauth",
            json.dumps(
                {
                    "access_token": "oauth-access-token",
                    "refresh_token": "oauth-refresh-token",
                    "expires_at": 1234,
                }
            ).encode(),
        )
        self.assertEqual(oauth.auth_kind, "oauth")
        self.assertEqual(oauth.secret, "oauth-access-token")
        self.assertEqual(oauth.refresh_token, "oauth-refresh-token")
        self.assertEqual(oauth.expires_at, 1234.0)
        self.assertNotIn("oauth-access-token", repr(oauth))
        self.assertNotIn("oauth-refresh-token", repr(oauth))

    def test_endpoint_resolution_uses_user_config_and_provider_domain_variant(self):
        from flymail.providers.network import resolve_account_endpoints
        from flymail.providers.registry import ProviderRegistry

        registry = ProviderRegistry.default()
        configured = resolve_account_endpoints(
            self.account(
                endpoint_config={
                    "imap": {"host": "imap.example.test", "port": 993, "security": "tls"},
                    "smtp": {"host": "smtp.example.test", "port": 587, "security": "starttls"},
                }
            ),
            registry,
        )
        self.assertEqual(configured.imap.host, "imap.example.test")
        self.assertEqual(configured.smtp.security, TransportSecurity.STARTTLS)

        netease = resolve_account_endpoints(
            self.account(provider_key="netease", email="user@126.com"),
            registry,
        )
        self.assertEqual(netease.imap.host, "imap.126.com")
        self.assertEqual(netease.smtp.host, "smtp.126.com")

    def test_http_connect_uses_basic_auth_but_never_exposes_it_in_errors(self):
        from flymail.providers.network import create_http_connect_socket

        socket = FakeSocket(b"HTTP/1.1 200 Connection established\r\nProxy-Agent: test\r\n\r\n")
        returned = create_http_connect_socket(
            "http://proxy-user:proxy-password@proxy.example.test:8080",
            "imap.example.test",
            993,
            timeout=10,
            connection_factory=lambda _address, _timeout: socket,
        )
        self.assertIs(returned, socket)
        self.assertIn(b"CONNECT imap.example.test:993 HTTP/1.1", socket.sent)
        self.assertIn(b"Proxy-Authorization: Basic", socket.sent)
        self.assertNotIn(b"proxy-password", socket.sent)

        rejected = FakeSocket(b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n")
        with self.assertRaises(ConnectionError) as error:
            create_http_connect_socket(
                "http://proxy-user:proxy-password@proxy.example.test:8080",
                "imap.example.test",
                993,
                timeout=10,
                connection_factory=lambda _address, _timeout: rejected,
            )
        self.assertNotIn("proxy-password", str(error.exception))
        self.assertTrue(rejected.closed)

    def test_blocking_imap_session_authenticates_password_oauth_and_starttls(self):
        from flymail.providers.network import BlockingImapSession, RuntimeCredential

        endpoint = ServiceEndpoint("imap.example.test", 143, TransportSecurity.STARTTLS)
        password_client = FakeImapClient()
        session = BlockingImapSession(
            endpoint,
            RuntimeCredential("user@example.test", "mail-password", "password"),
            proxy_url=None,
            client_factory=lambda _endpoint, _proxy: password_client,
        )
        session.connect()
        session.close()
        self.assertEqual(password_client.calls[:2], [("starttls",), ("login", "user@example.test", "mail-password")])

        oauth_client = FakeImapClient()
        oauth = BlockingImapSession(
            ServiceEndpoint("imap.example.test", 993, TransportSecurity.TLS),
            RuntimeCredential("user@example.test", "oauth-token", "oauth"),
            proxy_url="http://proxy.example.test:8080",
            client_factory=lambda _endpoint, _proxy: oauth_client,
        )
        oauth.connect()
        self.assertEqual(oauth_client.calls[0], ("oauth2_login", "user@example.test", "oauth-token"))

    def test_blocking_smtp_session_authenticates_password_and_oauth(self):
        from flymail.providers.network import BlockingSmtpSession, RuntimeCredential

        password_client = FakeSmtpClient()
        session = BlockingSmtpSession(
            ServiceEndpoint("smtp.example.test", 587, TransportSecurity.STARTTLS),
            RuntimeCredential("user@example.test", "mail-password", "password"),
            proxy_url=None,
            client_factory=lambda _endpoint, _proxy: password_client,
        )
        session.connect()
        session.close()
        self.assertEqual(
            password_client.calls[:4],
            [("ehlo",), ("starttls",), ("ehlo",), ("login", "user@example.test", "mail-password")],
        )

        oauth_client = FakeSmtpClient()
        oauth = BlockingSmtpSession(
            ServiceEndpoint("smtp.example.test", 465, TransportSecurity.TLS),
            RuntimeCredential("user@example.test", "oauth-token", "oauth"),
            proxy_url=None,
            client_factory=lambda _endpoint, _proxy: oauth_client,
        )
        oauth.connect()
        command = next(call for call in oauth_client.calls if call[0] == "docmd")
        self.assertEqual(command[1], "AUTH")
        self.assertTrue(command[2].startswith("XOAUTH2 "))
        self.assertNotIn("oauth-token", command[2])


if __name__ == "__main__":
    unittest.main()
