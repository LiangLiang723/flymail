import importlib.util
import socket
import unittest
from pathlib import Path
from unittest.mock import patch


class CustomProviderSecurityTest(unittest.TestCase):
    def _security(self):
        module_path = Path(__file__).resolve().parents[1] / "providers" / "custom" / "security.py"
        self.assertTrue(module_path.exists(), "providers.custom.security must be implemented")
        spec = importlib.util.spec_from_file_location("custom_security_for_test", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_rejects_plaintext_security_mode(self):
        security = self._security()

        with self.assertRaises(ValueError):
            security.normalize_security_mode("none")

    def test_rejects_private_address(self):
        security = self._security()
        private_result = [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("192.168.1.20", 993)),
        ]

        with patch.object(security.socket, "getaddrinfo", return_value=private_result):
            with self.assertRaises(ValueError):
                security.resolve_public_addresses("mail.example.com", 993)

    def test_rejects_domain_when_any_answer_is_private(self):
        security = self._security()
        mixed_results = [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("203.0.113.10", 993)),
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 993)),
        ]

        with patch.object(security.socket, "getaddrinfo", return_value=mixed_results):
            with self.assertRaises(ValueError):
                security.resolve_public_addresses("mail.example.com", 993)

    def test_accepts_public_address_and_normalizes_host(self):
        security = self._security()
        public_results = [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 993)),
        ]

        with patch.object(security.socket, "getaddrinfo", return_value=public_results):
            results = security.resolve_public_addresses("MAIL.EXAMPLE.COM.", 993)

        self.assertEqual(security.normalize_mail_host("MAIL.EXAMPLE.COM."), "mail.example.com")
        self.assertEqual(results, public_results)


if __name__ == "__main__":
    unittest.main()
