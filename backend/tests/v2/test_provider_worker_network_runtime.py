"""Production provider Worker runtime vertical network behavior."""

from __future__ import annotations

import asyncio
import unittest

from flymail.config import FlyMailSettings
from flymail.domain.operations import (
    OperationKind,
    OperationRecord,
    RemoteOperationCommand,
)
from flymail.infrastructure.db.pool import DatabasePool
from flymail.providers.account_runtime import LoadedProviderAccount
from flymail.providers.contracts import ServiceEndpoint, TransportSecurity
from flymail.domain.errors import PermanentError
from flymail.providers.core.smtp_client import (
    SentAppendRequest,
    SentVerificationRequest,
    SmtpDeliveryUncertain,
    SmtpSendRequest,
)
from flymail.providers.network import ResolvedAccountEndpoints, RuntimeCredential
from flymail.repositories.accounts import MailAccount


class FakeLoader:
    def __init__(self, loaded: LoadedProviderAccount) -> None:
        self.loaded = loaded
        self.calls: list[tuple[str, str | None]] = []

    async def load(self, account_id: str, *, expected_user_uid=None, require_active=True):
        self.calls.append((account_id, expected_user_uid))
        return self.loaded


class FakeStateStore:
    def __init__(self) -> None:
        from flymail.providers.runtime import RuntimeRemoteLocator

        self.locator = RuntimeRemoteLocator(
            remote_instance_id="rmi_runtime_1",
            account_id="acc_runtime_1",
            user_uid="usr_runtime_1",
            mailbox_native_key="INBOX",
            remote_uid=42,
            mailbox_native_keys=("INBOX", "Benchmark"),
        )

    async def remote_locator(self, remote_instance_id: str, *, expected_user_uid=None):
        self.locator = type(self.locator)(
            remote_instance_id=remote_instance_id,
            account_id=self.locator.account_id,
            user_uid=self.locator.user_uid,
            mailbox_native_key=self.locator.mailbox_native_key,
            remote_uid=self.locator.remote_uid,
            mailbox_native_keys=self.locator.mailbox_native_keys,
        )
        return self.locator

    async def sent_mailbox(self, account_id: str, *, expected_user_uid=None):
        return "Sent"


class FakeImapClient:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.fetch_payload = {
            42: {
                b"BODY[2]": b"exact-part-bytes",
                b"FLAGS": (b"\\Seen", b"\\Flagged"),
                b"MODSEQ": (17,),
                b"X-GM-LABELS": (b"INBOX", b"Benchmark"),
            }
        }
        self.search_result = [77]
        self.append_result = 88

    def list_folders(self):
        self.calls.append(("list_folders",))
        return []

    def select_folder(self, mailbox: str, readonly=False):
        self.calls.append(("select_folder", mailbox, readonly))
        return {b"UIDVALIDITY": 1}

    def fetch(self, messages, data, modifiers=None):
        self.calls.append(("fetch", tuple(messages), tuple(data), modifiers))
        return self.fetch_payload

    def search(self, criteria, charset=None):
        self.calls.append(("search", criteria, charset))
        return list(self.search_result)

    def add_flags(self, messages, flags, silent=False):
        self.calls.append(("add_flags", tuple(messages), tuple(flags), silent))

    def remove_flags(self, messages, flags, silent=False):
        self.calls.append(("remove_flags", tuple(messages), tuple(flags), silent))

    def add_gmail_labels(self, messages, labels, silent=False):
        self.calls.append(("add_gmail_labels", tuple(messages), tuple(labels), silent))

    def remove_gmail_labels(self, messages, labels, silent=False):
        self.calls.append(("remove_gmail_labels", tuple(messages), tuple(labels), silent))

    def move(self, messages, folder):
        self.calls.append(("move", tuple(messages), folder))

    def copy(self, messages, folder):
        self.calls.append(("copy", tuple(messages), folder))

    def delete_messages(self, messages, silent=False):
        self.calls.append(("delete_messages", tuple(messages), silent))

    def expunge(self, messages=None):
        self.calls.append(("expunge", tuple(messages) if messages else None))

    def append(self, folder, msg, flags=(), msg_time=None):
        self.calls.append(("append", folder, bytes(msg), tuple(flags), msg_time))
        return self.append_result


class FakeImapSession:
    def __init__(self, client: FakeImapClient) -> None:
        self.client = client
        self.connected = False
        self.closed = False

    def connect(self):
        self.connected = True
        return self.client

    def close(self):
        self.closed = True

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        self.close()


class FakeSmtpClient:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.refused: dict = {}

    def sendmail(self, sender, recipients, source, mail_options=()):
        self.calls.append((sender, tuple(recipients), bytes(source), tuple(mail_options)))
        return dict(self.refused)


class FakeSmtpSession:
    def __init__(self, client: FakeSmtpClient) -> None:
        self.client = client
        self.connected = False
        self.closed = False

    def connect(self):
        self.connected = True
        return self.client

    def close(self):
        self.closed = True

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        self.close()


class ProviderWorkerNetworkRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.account = MailAccount(
            id="acc_runtime_1",
            user_uid="usr_runtime_1",
            provider_key="gmail",
            email="runtime@example.test",
            normalized_email="runtime@example.test",
            display_name="",
            remark="",
            group_name="",
            status="active",
            endpoint_config={},
            icon_mode="provider",
            icon_value="",
            icon_object_sha256=None,
            poll_interval_seconds=300,
            created_at=1,
            updated_at=1,
        )
        self.loaded = LoadedProviderAccount(
            account=self.account,
            endpoints=ResolvedAccountEndpoints(
                ServiceEndpoint("imap.example.test", 993, TransportSecurity.TLS),
                ServiceEndpoint("smtp.example.test", 587, TransportSecurity.STARTTLS),
            ),
            credential=RuntimeCredential("runtime@example.test", "mail-secret", "password"),
            proxy_url="http://proxy.example.test:8080",
        )
        self.loader = FakeLoader(self.loaded)
        self.state = FakeStateStore()
        self.imap_client = FakeImapClient()
        self.smtp_client = FakeSmtpClient()
        settings = FlyMailSettings(
            role="worker",
            database_url="mysql://user:pass@127.0.0.1:3306/flymail_runtime_test",
            data_dir=__import__("pathlib").Path("/tmp/flymail-provider-runtime-test"),
            object_dir=__import__("pathlib").Path("/tmp/flymail-provider-runtime-test/objects"),
            object_tmp_dir=__import__("pathlib").Path("/tmp/flymail-provider-runtime-test/.tmp"),
            session_secret="provider-worker-network-test-secret",
            db_pool_name="provider-runtime-test",
            db_min_connections=1,
            db_max_connections=2,
        )
        from flymail.providers.runtime import ProductionProviderRuntime

        self.runtime = ProductionProviderRuntime(
            DatabasePool.__new__(DatabasePool),
            settings,
            account_loader=self.loader,
            state_store=self.state,
            imap_session_factory=lambda _loaded: FakeImapSession(self.imap_client),
            smtp_session_factory=lambda _loaded: FakeSmtpSession(self.smtp_client),
        )

    async def test_verify_connects_both_protocols_and_exact_part_streams(self):
        await self.runtime.verify(
            account=self.account,
            credential_type="password",
            credential=b"mail-secret",
            endpoint_config={
                "imap": {"host": "imap.example.test", "port": 993, "security": "tls"},
                "smtp": {"host": "smtp.example.test", "port": 587, "security": "starttls"},
            },
            proxy_url="http://proxy.example.test:8080",
        )
        self.assertIn(("list_folders",), self.imap_client.calls)

        from flymail.workers.content_fetch import RemoteContentLocator

        chunks = []
        async for chunk in self.runtime.stream(
            RemoteContentLocator(
                remote_instance_id="rmi_runtime_1",
                account_id="acc_runtime_1",
                provider_key="gmail",
                mailbox_native_key="INBOX",
                uidvalidity=1,
                remote_uid=42,
            ),
            "BODY.PEEK[2]",
        ):
            chunks.append(chunk)
        self.assertEqual(chunks, [b"exact-part-bytes"])
        self.assertIn(("select_folder", "INBOX", True), self.imap_client.calls)

    async def test_send_verify_and_append_sent_copy(self):
        sent = await self.runtime.send(
            SmtpSendRequest(
                account_id="acc_runtime_1",
                message_id_header="<runtime@example.test>",
                envelope_from="runtime@example.test",
                envelope_recipients=("recipient@example.test",),
                source=b"Message-ID: <runtime@example.test>\r\n\r\nBody",
                use_smtp_utf8=True,
            )
        )
        self.assertEqual(sent.response_code, 250)
        self.assertEqual(self.smtp_client.calls[0][3], ("SMTPUTF8",))

        verified = await self.runtime.verify_sent(
            SentVerificationRequest(
                account_id="acc_runtime_1",
                message_id_header="<runtime@example.test>",
                started_at=1,
                recipients=("recipient@example.test",),
            )
        )
        self.assertTrue(verified.found)
        self.assertEqual(verified.remote_uid, 77)

        appended = await self.runtime.append_sent_copy(
            SentAppendRequest(
                account_id="acc_runtime_1",
                message_id_header="<runtime@example.test>",
                source=b"Message-ID: <runtime@example.test>\r\n\r\nBody",
            )
        )
        self.assertEqual(appended.remote_uid, 88)
        self.assertTrue(any(call[0] == "append" and call[1] == "Sent" for call in self.imap_client.calls))

    def test_append_uid_parser_accepts_uidplus_response_shapes(self):
        self.assertEqual(self.runtime._append_uid(88), 88)
        self.assertEqual(
            self.runtime._append_uid(b"[APPENDUID 777 89] APPEND completed"),
            89,
        )
        self.assertEqual(
            self.runtime._append_uid((b"OK", [b"[APPENDUID 777 90]"])),
            90,
        )
        self.assertIsNone(self.runtime._append_uid(b"APPEND completed"))

    async def test_partial_smtp_acceptance_is_uncertain_and_total_rejection_is_permanent(self):
        request = SmtpSendRequest(
            account_id="acc_runtime_1",
            message_id_header="<partial@example.test>",
            envelope_from="runtime@example.test",
            envelope_recipients=("accepted@example.test", "rejected@example.test"),
            source=b"Message-ID: <partial@example.test>\r\n\r\nBody",
            use_smtp_utf8=False,
        )
        self.smtp_client.refused = {
            "rejected@example.test": (550, b"rejected")
        }
        with self.assertRaises(SmtpDeliveryUncertain):
            await self.runtime.send(request)

        self.smtp_client.refused = {
            "accepted@example.test": (550, b"rejected"),
            "rejected@example.test": (550, b"rejected"),
        }
        with self.assertRaises(PermanentError):
            await self.runtime.send(request)

    async def test_observe_and_apply_flags_labels_move_and_delete(self):
        operation = OperationRecord(
            id="op_runtime_1",
            user_uid="usr_runtime_1",
            operation_group_id=None,
            kind=OperationKind.SET_READ,
            target_type="message",
            target_id="msg_runtime_1",
            account_id="acc_runtime_1",
            remote_instance_id="rmi_runtime_1",
            desired_state={"value": True},
            observed_remote_version="",
            status="pending",
            attempt_count=0,
            idempotency_key="runtime-idempotency",
            created_at=1,
            updated_at=1,
        )
        observed = await self.runtime.observe(operation)
        self.assertIsNotNone(observed)
        assert observed is not None
        self.assertTrue(observed.is_read)
        self.assertTrue(observed.is_starred)
        self.assertIn("Benchmark", observed.mailbox_native_keys)

        commands = (
            RemoteOperationCommand(
                "op_read", "rmi_runtime_1", "acc_runtime_1", "gmail",
                OperationKind.SET_READ, observed.remote_version, "read-key",
                desired_value=False,
            ),
            RemoteOperationCommand(
                "op_label", "rmi_runtime_1", "acc_runtime_1", "gmail",
                OperationKind.ADD_LABEL, observed.remote_version, "label-key",
                target_native_key="Important", remote_action="add_label",
            ),
            RemoteOperationCommand(
                "op_move", "rmi_runtime_1", "acc_runtime_1", "gmail",
                OperationKind.MOVE, observed.remote_version, "move-key",
                target_native_key="Archive", remote_action="move",
            ),
            RemoteOperationCommand(
                "op_delete", "rmi_runtime_1", "acc_runtime_1", "gmail",
                OperationKind.DELETE_PERMANENT, observed.remote_version, "delete-key",
                remote_action="delete_permanent",
            ),
        )
        for command in commands:
            fetches_before = sum(1 for call in self.imap_client.calls if call[0] == "fetch")
            result = await self.runtime.apply(command)
            self.assertTrue(result.remote_version)
            fetches_after = sum(1 for call in self.imap_client.calls if call[0] == "fetch")
            if command.kind in {OperationKind.MOVE, OperationKind.DELETE_PERMANENT}:
                self.assertEqual(fetches_after, fetches_before)
            else:
                self.assertEqual(fetches_after, fetches_before + 1)
        names = [call[0] for call in self.imap_client.calls]
        self.assertIn("remove_flags", names)
        self.assertIn("add_gmail_labels", names)
        self.assertIn("move", names)
        self.assertIn("delete_messages", names)
        self.assertIn("expunge", names)


if __name__ == "__main__":
    unittest.main()
