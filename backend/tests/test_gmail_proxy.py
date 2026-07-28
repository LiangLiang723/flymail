import unittest
from unittest.mock import AsyncMock, Mock, patch


class GmailProxyConfigTest(unittest.TestCase):
    def test_proxy_url_is_read_only_from_enabled_account_credentials(self):
        from providers.gmail.config import proxy_url_from_extra

        self.assertEqual(proxy_url_from_extra({"gmail_proxy_enabled": False, "gmail_proxy_url": "http://proxy:8080"}), "")
        self.assertEqual(proxy_url_from_extra({"gmail_proxy_enabled": True, "gmail_proxy_url": " http://proxy:8080 "}), "http://proxy:8080")
        self.assertEqual(proxy_url_from_extra(None), "")


class GmailProxyOAuthTest(unittest.IsolatedAsyncioTestCase):
    async def test_callback_token_exchange_and_userinfo_use_user_proxy(self):
        from providers.gmail.auth import GmailAuthProvider

        token_response = Mock(status_code=200, headers={})
        token_response.json.return_value = {
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_in": 3600,
        }
        userinfo_response = Mock(status_code=200)
        userinfo_response.json.return_value = {"email": "user@gmail.com"}

        token_client = Mock()
        token_client.post = AsyncMock(return_value=token_response)
        userinfo_client = Mock()
        userinfo_client.get = AsyncMock(return_value=userinfo_response)
        token_context = AsyncMock()
        token_context.__aenter__.return_value = token_client
        token_context.__aexit__.return_value = False
        userinfo_context = AsyncMock()
        userinfo_context.__aenter__.return_value = userinfo_client
        userinfo_context.__aexit__.return_value = False

        with (
            patch("providers.gmail.auth.gmail_config.GMAIL_CLIENT_ID", "client-id"),
            patch("providers.gmail.auth.gmail_config.GMAIL_CLIENT_SECRET", "client-secret"),
            patch(
                "providers.gmail.auth.httpx.AsyncClient",
                side_effect=[token_context, userinfo_context],
            ) as client_cls,
        ):
            credentials = await GmailAuthProvider().handle_callback(
                "code",
                redirect_uri="https://mail.example.com/api/auth/callback",
                proxy_url="http://proxy.test:8080",
            )

        self.assertEqual(credentials.extra["email"], "user@gmail.com")
        self.assertEqual(len(client_cls.call_args_list), 2)
        self.assertEqual(client_cls.call_args_list[0].kwargs["proxy"], "http://proxy.test:8080")
        self.assertEqual(client_cls.call_args_list[1].kwargs["proxy"], "http://proxy.test:8080")

    async def test_refresh_token_uses_proxy_from_credentials(self):
        from providers.base import Credentials
        from providers.gmail.auth import GmailAuthProvider

        credentials = Credentials(
            provider_type="gmail",
            access_token="old",
            refresh_token="refresh",
            extra={
                "email": "user@gmail.com",
                "gmail_proxy_enabled": True,
                "gmail_proxy_url": "http://proxy.test:8080",
            },
        )
        response = Mock(status_code=200, headers={})
        response.json.return_value = {"access_token": "new", "expires_in": 3600}
        client = Mock()
        client.post = AsyncMock(return_value=response)
        context = AsyncMock()
        context.__aenter__.return_value = client
        context.__aexit__.return_value = False

        with patch("providers.gmail.auth.httpx.AsyncClient", return_value=context) as client_cls:
            refreshed = await GmailAuthProvider().refresh_token(credentials)

        self.assertEqual(refreshed.access_token, "new")
        self.assertEqual(client_cls.call_args.kwargs["proxy"], "http://proxy.test:8080")


class GmailProxyIdleTest(unittest.TestCase):
    def test_idle_config_carries_account_proxy_url(self):
        from providers.base import Credentials
        from services.sync import MailSyncService

        account = Mock(provider="gmail")
        credentials = Credentials(
            provider_type="gmail",
            access_token="token",
            extra={
                "email": "user@gmail.com",
                "gmail_proxy_enabled": True,
                "gmail_proxy_url": "http://proxy.test:8080",
            },
        )

        config = MailSyncService()._get_idle_config(account, credentials)

        self.assertEqual(config["proxy_url"], "http://proxy.test:8080")


class GmailProxyProviderTest(unittest.TestCase):
    def test_gmail_receiver_uses_proxy_from_credentials(self):
        from providers.base import Credentials
        from providers.gmail.receiver import GmailReceiver

        credentials = Credentials(
            provider_type="gmail",
            access_token="token",
            extra={
                "email": "user@gmail.com",
                "gmail_proxy_enabled": True,
                "gmail_proxy_url": "http://proxy.test:8080",
            },
        )
        connection = Mock()
        with patch("providers.gmail.receiver.ProxyIMAP4_SSL", return_value=connection) as proxy_cls:
            result = GmailReceiver()._connect_imap(credentials)

        self.assertIs(result, connection)
        proxy_cls.assert_called_once_with("imap.gmail.com", 993, proxy_url="http://proxy.test:8080")
        connection.authenticate.assert_called_once()

    def test_gmail_sender_uses_proxy_from_credentials(self):
        from providers.base import Credentials
        from providers.gmail.sender import GmailSender

        credentials = Credentials(
            provider_type="gmail",
            access_token="token",
            extra={
                "email": "user@gmail.com",
                "gmail_proxy_enabled": True,
                "gmail_proxy_url": "http://proxy.test:8080",
            },
        )
        connection = Mock()
        connection.docmd.return_value = (235, b"ok")
        with patch("providers.gmail.sender.ProxySMTP", return_value=connection) as proxy_cls:
            result = GmailSender()._connect_smtp(credentials)

        self.assertIs(result, connection)
        proxy_cls.assert_called_once_with("smtp.gmail.com", 587, proxy_url="http://proxy.test:8080")
        connection.starttls.assert_called_once()


class GmailProxyTransportTest(unittest.TestCase):
    def test_proxy_smtp_uses_connect_tunnel_socket(self):
        from providers.ipv4 import ProxySMTP

        tunnel = Mock()
        client = ProxySMTP.__new__(ProxySMTP)
        client._proxy_url = "http://proxy.test:8080"
        with patch("providers.proxy.create_proxy_socket", return_value=tunnel) as create:
            result = client._get_socket("smtp.gmail.com", 587, 20)

        self.assertIs(result, tunnel)
        create.assert_called_once_with("http://proxy.test:8080", "smtp.gmail.com", 587, 20)


class GmailProxySocketTest(unittest.TestCase):
    def test_authenticated_connect_never_logs_proxy_credentials(self):
        from providers.proxy import create_proxy_socket

        sock = Mock()
        sock.recv.return_value = b"HTTP/1.1 200 Connection established\r\n\r\n"
        with (
            patch("providers.proxy.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("203.0.113.10", 8080))]),
            patch("providers.proxy.socket.socket", return_value=sock),
            patch("providers.proxy.logger.debug") as debug,
        ):
            result = create_proxy_socket(
                "http://user:secret@example-proxy.test:8080",
                "imap.gmail.com",
                993,
                timeout=12,
            )

        self.assertIs(result, sock)
        request = sock.sendall.call_args.args[0].decode("ascii")
        self.assertIn("Proxy-Authorization: Basic", request)
        logged = " ".join(str(value) for call in debug.call_args_list for value in call.args)
        self.assertNotIn("user", logged)
        self.assertNotIn("secret", logged)

    def test_invalid_proxy_scheme_is_rejected_before_socket_creation(self):
        from providers.proxy import create_proxy_socket

        with patch("providers.proxy.socket.socket") as socket_factory:
            with self.assertRaises(ValueError):
                create_proxy_socket("socks5://127.0.0.1:1080", "imap.gmail.com", 993)
        socket_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
