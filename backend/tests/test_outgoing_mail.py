import importlib
import importlib.util
import sys
import tempfile
import types
import unittest
from email import message_from_bytes
from pathlib import Path
from unittest.mock import AsyncMock

from models import Account


def _load_outgoing_mail_module():
    db_stub = types.ModuleType("db")
    db_stub.get_folder_stats = AsyncMock(return_value={"total_count": 0, "unread_count": 0, "updated_at": 0})
    db_stub.upsert_cached_messages = AsyncMock(return_value=1)
    db_stub.upsert_folder_stats = AsyncMock()

    factory_stub = types.ModuleType("providers.factory")

    class _ProviderFactory:
        get_receiver = staticmethod(lambda provider: None)

    factory_stub.ProviderFactory = _ProviderFactory

    mail_cache_stub = types.ModuleType("services.mail_cache")
    mail_cache_stub.sync_folder_to_cache = AsyncMock(return_value=0)
    mail_cache_stub.sync_missing_messages = AsyncMock(return_value=0)

    sync_stub = types.ModuleType("services.sync")
    sync_stub.sync_service = types.SimpleNamespace(refresh_clients=AsyncMock())

    token_stub = types.ModuleType("services.token")
    token_stub.ensure_token = AsyncMock(return_value=object())

    logger_stub = types.ModuleType("utils.logger")
    logger_stub.get_logger = lambda name: types.SimpleNamespace(
        debug=lambda *args, **kwargs: None,
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
    )

    previous = {
        name: sys.modules.get(name)
        for name in (
            "db",
            "providers.factory",
            "services.mail_cache",
            "services.sync",
            "services.token",
            "utils.logger",
        )
    }
    sys.modules.update(
        {
            "db": db_stub,
            "providers.factory": factory_stub,
            "services.mail_cache": mail_cache_stub,
            "services.sync": sync_stub,
            "services.token": token_stub,
            "utils.logger": logger_stub,
        }
    )
    try:
        module_path = Path(__file__).resolve().parents[1] / "services" / "outgoing_mail.py"
        spec = importlib.util.spec_from_file_location("outgoing_mail_for_test", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module, db_stub, mail_cache_stub, sync_stub
    finally:
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


class OutgoingMailTest(unittest.IsolatedAsyncioTestCase):
    async def test_build_outgoing_message_preserves_html_and_plain_line_breaks(self):
        outgoing_mail, _db_stub, _mail_cache_stub, _sync_stub = _load_outgoing_mail_module()
        body_html = "<p>第一行</p><p></p><p>第二行<br>继续第二行</p><ul><li>列表项</li></ul>"

        raw = outgoing_mail.build_outgoing_message_bytes(
            from_email="sender@example.com",
            to=["to@example.com"],
            cc=[],
            bcc=[],
            subject="format",
            body_html=body_html,
            attachments=[],
        )

        msg = message_from_bytes(raw)
        html_parts = []
        plain_parts = []
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                html_parts.append(part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8"))
            if part.get_content_type() == "text/plain":
                plain_parts.append(part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8"))

        self.assertEqual(html_parts, ["<p>第一行</p><p><br></p><p>第二行<br>继续第二行</p><ul><li>列表项</li></ul>"])
        self.assertEqual(plain_parts, ["第一行\n\n第二行\n继续第二行\n- 列表项"])

    async def test_build_outgoing_message_embeds_inline_images_and_keeps_normal_attachments(self):
        mime_spec = importlib.util.find_spec("services.mime_parts")
        self.assertIsNotNone(mime_spec, "services.mime_parts must exist")
        mime_parts = importlib.import_module("services.mime_parts")
        outgoing_mail, _db_stub, _mail_cache_stub, _sync_stub = _load_outgoing_mail_module()
        inline = mime_parts.InlineImagePart(
            content_id="sig-image@flymail",
            data=b"webp-inline-image",
            content_type="image/webp",
            filename="signature.webp",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            attachment_path = Path(temp_dir) / "notes.txt"
            attachment_path.write_bytes(b"normal-attachment")
            raw = outgoing_mail.build_outgoing_message_bytes(
                from_email="sender@example.com",
                to=["to@example.com"],
                cc=[],
                bcc=[],
                subject="inline",
                body_html='<p>Hello</p><img src="cid:sig-image@flymail" width="200">',
                attachments=[str(attachment_path)],
                inline_images=[inline],
            )

        msg = message_from_bytes(raw)
        html = next(part for part in msg.walk() if part.get_content_type() == "text/html")
        html_text = html.get_payload(decode=True).decode(html.get_content_charset() or "utf-8")
        image = next(part for part in msg.walk() if part.get_content_type() == "image/webp")
        attachment = next(
            part for part in msg.walk()
            if part.get_content_disposition() == "attachment"
        )

        self.assertIn('src="cid:sig-image@flymail"', html_text)
        self.assertEqual(image.get("Content-ID"), "<sig-image@flymail>")
        self.assertEqual(image.get_content_disposition(), "inline")
        self.assertEqual(image.get_payload(decode=True), b"webp-inline-image")
        self.assertEqual(attachment.get_payload(decode=True), b"normal-attachment")

    async def test_append_failure_caches_sent_message_locally(self):
        outgoing_mail, db_stub, mail_cache_stub, sync_stub = _load_outgoing_mail_module()
        account = Account(
            id="account-1",
            user_uid="user-1",
            email="sender@example.com",
            provider="gmail",
        )

        receiver = AsyncMock()
        receiver.fetch_folders.return_value = [
            types.SimpleNamespace(name="已发送", path="[Gmail]/Sent Mail"),
        ]
        receiver.save_draft.side_effect = RuntimeError("append failed")
        receiver.disconnect = AsyncMock()
        outgoing_mail.ProviderFactory.get_receiver = staticmethod(lambda provider: receiver)

        sent_folder = await outgoing_mail.ensure_sent_message_cached(
            account=account,
            user_uid="user-1",
            to=["to@example.com"],
            cc=[],
            bcc=[],
            subject="hello",
            body_html="<p>Hello</p>",
            attachments=[],
        )

        self.assertEqual(sent_folder, "[Gmail]/Sent Mail")
        db_stub.upsert_cached_messages.assert_awaited_once()
        cached = db_stub.upsert_cached_messages.await_args.args[0][0]
        self.assertEqual(cached.folder, "[Gmail]/Sent Mail")
        self.assertEqual(cached.subject, "hello")
        self.assertEqual(cached.from_addr, "sender@example.com")
        self.assertEqual(cached.to_addr, "to@example.com")
        self.assertTrue(cached.is_read)
        db_stub.upsert_folder_stats.assert_awaited_once_with("account-1", "[Gmail]/Sent Mail", 1, 0)
        self.assertEqual(mail_cache_stub.sync_folder_to_cache.await_count, 2)
        mail_cache_stub.sync_missing_messages.assert_awaited_once_with(account, "[Gmail]/Sent Mail")
        self.assertEqual(sync_stub.sync_service.refresh_clients.await_count, 2)
        sync_stub.sync_service.refresh_clients.assert_any_await(
            "account-1",
            "[Gmail]/Sent Mail",
            user_uid="user-1",
        )
        sync_stub.sync_service.refresh_clients.assert_any_await(
            "account-1",
            "Sent",
            user_uid="user-1",
        )

    async def test_append_failure_keeps_inline_image_visible_in_local_sent_cache(self):
        mime_parts = importlib.import_module("services.mime_parts")
        outgoing_mail, db_stub, _mail_cache_stub, _sync_stub = _load_outgoing_mail_module()
        account = Account(
            id="account-1",
            user_uid="user-1",
            email="sender@example.com",
            provider="gmail",
        )
        inline = mime_parts.InlineImagePart(
            content_id="cache-image@flymail",
            data=b"cached-inline-image",
            content_type="image/webp",
            filename="signature.webp",
        )

        receiver = AsyncMock()
        receiver.fetch_folders.return_value = [types.SimpleNamespace(name="Sent", path="Sent")]
        receiver.save_draft.side_effect = RuntimeError("append failed")
        receiver.disconnect = AsyncMock()
        outgoing_mail.ProviderFactory.get_receiver = staticmethod(lambda provider: receiver)

        await outgoing_mail.ensure_sent_message_cached(
            account=account,
            user_uid="user-1",
            to=["to@example.com"],
            cc=[],
            bcc=[],
            subject="inline fallback",
            body_html='<p>x</p><img src="cid:cache-image@flymail">',
            attachments=[],
            inline_images=[inline],
        )

        cached = db_stub.upsert_cached_messages.await_args.args[0][0]
        self.assertIn("data:image/webp;base64,", cached.body_html)
        self.assertNotIn("cid:cache-image@flymail", cached.body_html)
        self.assertFalse(cached.has_attachments)

    async def test_sent_cache_always_appends_even_when_same_subject_exists(self):
        outgoing_mail, db_stub, mail_cache_stub, sync_stub = _load_outgoing_mail_module()
        account = Account(
            id="account-1",
            user_uid="user-1",
            email="sender@example.com",
            provider="qq",
        )

        receiver = AsyncMock()
        receiver.fetch_folders.return_value = [
            types.SimpleNamespace(name="已发送", path="Sent Messages"),
        ]
        receiver.disconnect = AsyncMock()
        outgoing_mail.ProviderFactory.get_receiver = staticmethod(lambda provider: receiver)

        sent_folder = await outgoing_mail.ensure_sent_message_cached(
            account=account,
            user_uid="user-1",
            to=["to@example.com"],
            cc=[],
            bcc=[],
            subject="same subject",
            body_html="<p>same body</p>",
            attachments=[],
        )

        self.assertEqual(sent_folder, "Sent Messages")
        receiver.save_draft.assert_awaited_once()
        db_stub.upsert_cached_messages.assert_not_awaited()
        self.assertEqual(mail_cache_stub.sync_folder_to_cache.await_count, 2)
        mail_cache_stub.sync_missing_messages.assert_awaited_once_with(account, "Sent Messages")
        self.assertEqual(sync_stub.sync_service.refresh_clients.await_count, 2)


if __name__ == "__main__":
    unittest.main()
