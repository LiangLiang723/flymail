from __future__ import annotations

import ast
import dataclasses
import unittest
from pathlib import Path

from flymail.providers.contracts import (
    EndpointVariant,
    MailboxMapping,
    ProviderCapabilities,
    ProviderEndpoints,
    ProviderPlugin,
    SentCopyStrategy,
    ServiceEndpoint,
    TransportSecurity,
)
from flymail.providers.errors import ProviderError, ProviderErrorCode
from flymail.providers.registry import ProviderRegistry


PROVIDER_KEYS = (
    "generic",
    "gmail",
    "outlook",
    "qq",
    "netease",
    "icloud",
    "sina",
)

BOOLEAN_CAPABILITIES = (
    "supports_idle",
    "supports_move",
    "supports_uidplus",
    "supports_condstore",
    "supports_qresync",
    "supports_gmail_labels",
    "supports_special_use",
    "supports_smtp_utf8",
    "supports_oauth",
    "auto_saves_sent_copy",
)


class ProviderContractMixin:
    provider_key: str

    def plugin(self) -> ProviderPlugin:
        return ProviderRegistry.default().get(self.provider_key)

    def test_capabilities_are_explicit_and_immutable(self):
        capabilities = self.plugin().capabilities()
        self.assertIsInstance(capabilities, ProviderCapabilities)
        for field_name in BOOLEAN_CAPABILITIES:
            with self.subTest(field=field_name):
                self.assertIsInstance(getattr(capabilities, field_name), bool)
        self.assertGreaterEqual(capabilities.max_parallel_connections, 1)
        self.assertGreaterEqual(capabilities.recommended_poll_seconds, 60)
        self.assertGreaterEqual(capabilities.idle_refresh_seconds, 60)
        self.assertGreaterEqual(capabilities.max_fetch_batch, 1)
        self.assertGreater(capabilities.max_attachment_bytes, 0)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            capabilities.max_fetch_batch = 1  # type: ignore[misc]

    def test_special_mailbox_mapping_preserves_native_key(self):
        mapping = self.plugin().map_mailbox(
            native_key="INBOX",
            attributes={"\\Inbox"},
        )
        self.assertIsInstance(mapping, MailboxMapping)
        self.assertEqual(mapping.native_key, "INBOX")
        self.assertEqual(mapping.semantic_key, "inbox")
        self.assertIn("\\inbox", mapping.attributes)

    def test_default_endpoints_are_explicit(self):
        endpoints = self.plugin().default_endpoints()
        self.assertIsInstance(endpoints, ProviderEndpoints)
        if self.provider_key == "generic":
            self.assertTrue(endpoints.user_supplied)
            self.assertIsNone(endpoints.imap)
            self.assertIsNone(endpoints.smtp)
        else:
            self.assertFalse(endpoints.user_supplied)
            self.assertIsNotNone(endpoints.imap)
            self.assertIsNotNone(endpoints.smtp)
            self.assertTrue(endpoints.imap.host)
            self.assertTrue(endpoints.smtp.host)
            self.assertGreater(endpoints.imap.port, 0)
            self.assertGreater(endpoints.smtp.port, 0)
            self.assertIsInstance(endpoints.imap.security, TransportSecurity)
            self.assertIsInstance(endpoints.smtp.security, TransportSecurity)

    def test_sent_copy_strategy_matches_capability(self):
        plugin = self.plugin()
        strategy = plugin.sent_copy_strategy()
        self.assertIsInstance(strategy, SentCopyStrategy)
        self.assertEqual(
            strategy is SentCopyStrategy.PROVIDER_AUTO,
            plugin.capabilities().auto_saves_sent_copy,
        )

    def test_label_normalization_is_stable_and_ordered(self):
        normalized = self.plugin().normalize_labels(
            ["  Project  ", "Project", "", "  Receipts", "Receipts  "]
        )
        self.assertEqual(normalized, ("Project", "Receipts"))

    def test_error_classification_is_safe(self):
        error = self.plugin().classify_error(
            "imap.login",
            "NO [AUTHENTICATIONFAILED] password=mail-secret token=oauth-secret",
        )
        self.assertIsInstance(error, ProviderError)
        self.assertEqual(error.code, ProviderErrorCode.AUTHENTICATION_FAILED)
        self.assertFalse(error.retryable)
        rendered = f"{error!r} {error}"
        self.assertNotIn("mail-secret", rendered)
        self.assertNotIn("oauth-secret", rendered)
        self.assertNotIn("password=", rendered.casefold())
        self.assertNotIn("token=", rendered.casefold())


class GenericProviderContractTests(ProviderContractMixin, unittest.TestCase):
    provider_key = "generic"


class GmailProviderContractTests(ProviderContractMixin, unittest.TestCase):
    provider_key = "gmail"


class OutlookProviderContractTests(ProviderContractMixin, unittest.TestCase):
    provider_key = "outlook"


class QQProviderContractTests(ProviderContractMixin, unittest.TestCase):
    provider_key = "qq"


class NetEaseProviderContractTests(ProviderContractMixin, unittest.TestCase):
    provider_key = "netease"


class ICloudProviderContractTests(ProviderContractMixin, unittest.TestCase):
    provider_key = "icloud"


class SinaProviderContractTests(ProviderContractMixin, unittest.TestCase):
    provider_key = "sina"


class ProviderRegistryTests(unittest.TestCase):
    def test_default_registry_exposes_exact_stable_keys(self):
        registry = ProviderRegistry.default()
        self.assertEqual(registry.keys(), PROVIDER_KEYS)
        self.assertIs(registry.get(" GMAIL "), registry.get("gmail"))
        for key in PROVIDER_KEYS:
            with self.subTest(key=key):
                self.assertEqual(registry.get(key).key, key)
                self.assertIsInstance(registry.get(key), ProviderPlugin)

    def test_unknown_or_empty_provider_is_rejected_without_fallback(self):
        registry = ProviderRegistry.default()
        for key in ("", "unknown", "custom", "gmail.example"):
            with self.subTest(key=key):
                with self.assertRaisesRegex(KeyError, "unknown provider"):
                    registry.get(key)

    def test_duplicate_registration_is_rejected(self):
        gmail = ProviderRegistry.default().get("gmail")
        with self.assertRaisesRegex(ValueError, "duplicate provider"):
            ProviderRegistry((gmail, gmail))


class ProviderEndpointTests(unittest.TestCase):
    def test_verified_primary_endpoints_match_existing_project_configuration(self):
        registry = ProviderRegistry.default()
        expected = {
            "gmail": ("imap.gmail.com", 993, "smtp.gmail.com", 587),
            "outlook": ("outlook.office365.com", 993, "smtp-mail.outlook.com", 587),
            "qq": ("imap.qq.com", 993, "smtp.qq.com", 465),
            "netease": ("imap.163.com", 993, "smtp.163.com", 465),
            "icloud": ("imap.mail.me.com", 993, "smtp.mail.me.com", 587),
            "sina": ("imap.sina.com", 993, "smtp.sina.com", 465),
        }
        for key, values in expected.items():
            with self.subTest(key=key):
                endpoints = registry.get(key).default_endpoints()
                self.assertEqual(
                    (
                        endpoints.imap.host,
                        endpoints.imap.port,
                        endpoints.smtp.host,
                        endpoints.smtp.port,
                    ),
                    values,
                )

    def test_multi_domain_providers_preserve_known_endpoint_variants(self):
        registry = ProviderRegistry.default()
        netease = registry.get("netease").default_endpoints()
        self.assertEqual(
            {variant.domain_suffixes for variant in netease.variants},
            {("@126.com",), ("@188.com",), ("@yeah.net",)},
        )
        sina = registry.get("sina").default_endpoints()
        self.assertEqual(
            {variant.domain_suffixes for variant in sina.variants},
            {("@sina.cn",), ("@vip.sina.com",), ("@vip.sina.cn",)},
        )

    def test_transport_security_matches_existing_connections(self):
        registry = ProviderRegistry.default()
        self.assertIs(
            registry.get("gmail").default_endpoints().smtp.security,
            TransportSecurity.STARTTLS,
        )
        self.assertIs(
            registry.get("outlook").default_endpoints().smtp.security,
            TransportSecurity.STARTTLS,
        )
        self.assertIs(
            registry.get("icloud").default_endpoints().smtp.security,
            TransportSecurity.STARTTLS,
        )
        for key in ("qq", "netease", "sina"):
            with self.subTest(key=key):
                self.assertIs(
                    registry.get(key).default_endpoints().smtp.security,
                    TransportSecurity.TLS,
                )


class ProviderBehaviorTests(unittest.TestCase):
    def test_gmail_declares_native_labels_and_auto_saved_sent_copy(self):
        gmail = ProviderRegistry.default().get("gmail")
        capabilities = gmail.capabilities()
        self.assertTrue(capabilities.supports_gmail_labels)
        self.assertTrue(capabilities.supports_oauth)
        self.assertTrue(capabilities.auto_saves_sent_copy)
        self.assertIs(gmail.sent_copy_strategy(), SentCopyStrategy.PROVIDER_AUTO)
        all_mail = gmail.map_mailbox("[Gmail]/All Mail", set())
        self.assertEqual(all_mail.semantic_key, "all_mail")
        self.assertEqual(all_mail.native_key, "[Gmail]/All Mail")

    def test_existing_realtime_modes_are_reflected_conservatively(self):
        registry = ProviderRegistry.default()
        for key in ("gmail", "outlook", "qq"):
            with self.subTest(key=key):
                self.assertTrue(registry.get(key).capabilities().supports_idle)
        for key in ("generic", "netease", "icloud", "sina"):
            with self.subTest(key=key):
                self.assertFalse(registry.get(key).capabilities().supports_idle)

    def test_generic_mapping_uses_special_use_then_conservative_aliases(self):
        generic = ProviderRegistry.default().get("generic")
        self.assertEqual(
            generic.map_mailbox("Anything", {"\\Sent"}).semantic_key,
            "sent",
        )
        self.assertEqual(generic.map_mailbox("草稿箱", set()).semantic_key, "drafts")
        self.assertEqual(generic.map_mailbox("Team/2026", set()).semantic_key, "custom")

    def test_capabilities_do_not_claim_unverified_advanced_imap_features(self):
        for key in PROVIDER_KEYS:
            with self.subTest(key=key):
                capabilities = ProviderRegistry.default().get(key).capabilities()
                self.assertFalse(capabilities.supports_condstore)
                self.assertFalse(capabilities.supports_qresync)


class ProviderErrorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = ProviderRegistry.default().get("generic")

    def test_error_code_values_are_exact_and_complete(self):
        self.assertEqual(
            tuple(code.value for code in ProviderErrorCode),
            (
                "authentication_failed",
                "authorization_required",
                "connection_failed",
                "rate_limited",
                "mailbox_not_found",
                "message_not_found",
                "message_too_large",
                "unsupported_operation",
                "server_rejected",
                "temporary_server_error",
                "protocol_error",
            ),
        )

    def test_representative_responses_classify_to_stable_categories(self):
        cases = (
            ("oauth.refresh", {"error": "invalid_grant"}, ProviderErrorCode.AUTHORIZATION_REQUIRED, False),
            ("imap.connect", TimeoutError("timed out"), ProviderErrorCode.CONNECTION_FAILED, True),
            ("imap.fetch", {"status": 429, "message": "Too many requests"}, ProviderErrorCode.RATE_LIMITED, True),
            ("imap.select", "NO mailbox does not exist", ProviderErrorCode.MAILBOX_NOT_FOUND, False),
            ("imap.fetch", "NO message not found", ProviderErrorCode.MESSAGE_NOT_FOUND, False),
            ("smtp.data", {"code": 552, "message": "message size exceeds fixed maximum"}, ProviderErrorCode.MESSAGE_TOO_LARGE, False),
            ("imap.move", "BAD command not supported", ProviderErrorCode.UNSUPPORTED_OPERATION, False),
            ("smtp.rcpt", {"code": 550, "message": "recipient rejected"}, ProviderErrorCode.SERVER_REJECTED, False),
            ("smtp.data", {"code": 451, "message": "temporary local problem"}, ProviderErrorCode.TEMPORARY_SERVER_ERROR, True),
            ("imap.fetch", "BAD malformed response", ProviderErrorCode.PROTOCOL_ERROR, False),
        )
        for operation, response, expected_code, retryable in cases:
            with self.subTest(operation=operation, response=repr(response)):
                error = self.plugin.classify_error(operation, response)
                self.assertEqual(error.code, expected_code)
                self.assertEqual(error.retryable, retryable)
                self.assertTrue(error.safe_detail)

    def test_debug_context_redacts_credentials_recursively(self):
        error = self.plugin.classify_error(
            "oauth.refresh",
            {
                "status": 401,
                "authorization": "Bearer raw-access-token",
                "nested": {
                    "password": "mail-password",
                    "client_secret": "oauth-client-secret",
                    "refresh_token": "oauth-refresh-token",
                },
            },
        )
        debug = repr(error.debug_context)
        rendered = f"{error!r} {error} {debug}"
        for secret in (
            "raw-access-token",
            "mail-password",
            "oauth-client-secret",
            "oauth-refresh-token",
        ):
            self.assertNotIn(secret, rendered)
        self.assertIn("***", debug)


class ProviderValueValidationTests(unittest.TestCase):
    def test_integer_contracts_reject_boolean_values(self):
        with self.assertRaisesRegex(TypeError, "port"):
            ServiceEndpoint("imap.example.com", True, TransportSecurity.TLS)
        values = ProviderRegistry.default().get("generic").capabilities()
        capability_data = {
            field.name: getattr(values, field.name)
            for field in dataclasses.fields(ProviderCapabilities)
        }
        capability_data["max_parallel_connections"] = True
        with self.assertRaisesRegex(TypeError, "max_parallel_connections"):
            ProviderCapabilities(**capability_data)

    def test_endpoint_variant_domains_cannot_overlap(self):
        endpoint = ServiceEndpoint("imap.example.com", 993, TransportSecurity.TLS)
        smtp = ServiceEndpoint("smtp.example.com", 465, TransportSecurity.TLS)
        first = EndpointVariant(("@example.com",), endpoint, smtp)
        second = EndpointVariant(("@example.com",), endpoint, smtp)
        with self.assertRaisesRegex(ValueError, "must not overlap"):
            ProviderEndpoints(endpoint, smtp, variants=(first, second))


class ProviderImportBoundaryTests(unittest.TestCase):
    def test_plugins_do_not_import_application_or_persistence_layers(self):
        plugins_root = Path(__file__).resolve().parents[2] / "flymail" / "providers" / "plugins"
        forbidden_prefixes = (
            "fastapi",
            "flymail.repositories",
            "flymail.infrastructure.db",
            "flymail.infrastructure.object_store",
        )
        plugin_files = sorted(path for path in plugins_root.glob("*.py") if path.name != "__init__.py")
        self.assertGreaterEqual(len(plugin_files), len(PROVIDER_KEYS))

        violations: list[str] = []
        for path in plugin_files:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.Import):
                    modules.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules.append(node.module)
                for module in modules:
                    if module.startswith(forbidden_prefixes):
                        violations.append(f"{path.name}: {module}")
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
