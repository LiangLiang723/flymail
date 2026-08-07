import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from models import Account


def _load_scheduler_module():
    data_paths_stub = types.ModuleType("data_paths")
    data_paths_stub.CONFIG_DIR = Path(".")
    data_paths_stub.ensure_data_dirs = lambda: None

    sqlalchemy_stub = types.ModuleType("apscheduler.jobstores.sqlalchemy")
    sqlalchemy_stub.SQLAlchemyJobStore = lambda *args, **kwargs: object()

    scheduler_stub = types.ModuleType("apscheduler.schedulers.asyncio")

    class _Scheduler:
        def __init__(self, *args, **kwargs):
            self.running = False

        def add_job(self, *args, **kwargs):
            pass

    scheduler_stub.AsyncIOScheduler = _Scheduler

    outgoing_stub = types.ModuleType("services.outgoing_mail")
    outgoing_stub.ensure_sent_message_cached = AsyncMock(return_value="Sent")

    inline_stub = types.ModuleType("services.inline_images")
    inline_stub.prepare_inline_images = AsyncMock(
        return_value=types.SimpleNamespace(
            body_html='<p>prepared</p><img src="cid:scheduled@flymail">',
            inline_images=[types.SimpleNamespace(content_id="scheduled@flymail")],
        )
    )

    previous = {
        name: sys.modules.get(name)
        for name in (
            "data_paths",
            "apscheduler.jobstores.sqlalchemy",
            "apscheduler.schedulers.asyncio",
            "services.outgoing_mail",
            "services.inline_images",
        )
    }
    sys.modules.update(
        {
            "data_paths": data_paths_stub,
            "apscheduler.jobstores.sqlalchemy": sqlalchemy_stub,
            "apscheduler.schedulers.asyncio": scheduler_stub,
            "services.outgoing_mail": outgoing_stub,
            "services.inline_images": inline_stub,
        }
    )
    try:
        module_path = Path(__file__).resolve().parents[1] / "services" / "scheduler.py"
        spec = importlib.util.spec_from_file_location("scheduler_for_test", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module, outgoing_stub, inline_stub
    finally:
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


class SchedulerUrlTest(unittest.TestCase):
    def test_redacts_database_password_from_jobstore_url(self):
        scheduler, _, _ = _load_scheduler_module()

        redacted = scheduler._redact_jobstore_url(
            "mysql+pymysql://flymail:secret-password@127.0.0.1:3306/flymail?charset=utf8mb4"
        )

        self.assertEqual(
            redacted,
            "mysql+pymysql://flymail:***@127.0.0.1:3306/flymail?charset=utf8mb4",
        )
        self.assertNotIn("secret-password", redacted)


class SchedulerDraftTest(unittest.IsolatedAsyncioTestCase):
    async def test_scheduled_send_deletes_source_draft_and_refreshes_counts(self):
        scheduler, outgoing_stub, inline_stub = _load_scheduler_module()
        account = Account(
            id="account-1",
            user_uid="user-1",
            email="sender@example.com",
            provider="qq",
        )

        sender = AsyncMock()
        receiver = AsyncMock()

        factory_stub = types.ModuleType("providers.factory")
        factory_stub.ProviderFactory = types.SimpleNamespace(
            get_sender=lambda _provider: sender,
            get_receiver=lambda _provider: receiver,
        )

        db_stub = types.ModuleType("db")
        db_stub.get_accounts = AsyncMock(return_value=[account])

        draft_stub = types.ModuleType("services.draft")
        draft_stub.delete_draft_from_imap = AsyncMock(return_value=True)

        mail_cache_stub = types.ModuleType("services.mail_cache")
        mail_cache_stub.sync_folder_to_cache = AsyncMock(return_value=0)

        sync_stub = types.ModuleType("services.sync")
        sync_stub.sync_service = types.SimpleNamespace(
            refresh_clients=AsyncMock(),
            notify_schedule_result=AsyncMock(),
        )

        token_stub = types.ModuleType("services.token")
        token_stub.ensure_token = AsyncMock(return_value=object())

        modules = {
            "db": db_stub,
            "providers.factory": factory_stub,
            "services.draft": draft_stub,
            "services.mail_cache": mail_cache_stub,
            "services.sync": sync_stub,
            "services.token": token_stub,
        }
        previous = {name: sys.modules.get(name) for name in modules}
        sys.modules.update(modules)
        try:
            await scheduler._send_scheduled_email(
                user_uid="user-1",
                account_id="account-1",
                to=["to@example.com"],
                cc=[],
                bcc=[],
                subject="scheduled",
                body_html="<p>body</p>",
                attachment_paths=[],
                in_reply_to=None,
                draft_message_id="account-1_Drafts_42",
                draft_folder="Drafts",
            )
        finally:
            for name, value in previous.items():
                if value is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = value

        inline_stub.prepare_inline_images.assert_awaited_once_with("user-1", "<p>body</p>")
        sender.send_message.assert_awaited_once()
        send_kwargs = sender.send_message.await_args.kwargs
        self.assertEqual(send_kwargs["body_html"], '<p>prepared</p><img src="cid:scheduled@flymail">')
        self.assertEqual(len(send_kwargs["inline_images"]), 1)
        outgoing_stub.ensure_sent_message_cached.assert_awaited_once()
        cache_kwargs = outgoing_stub.ensure_sent_message_cached.await_args.kwargs
        self.assertEqual(cache_kwargs["body_html"], '<p>prepared</p><img src="cid:scheduled@flymail">')
        self.assertEqual(len(cache_kwargs["inline_images"]), 1)
        draft_stub.delete_draft_from_imap.assert_awaited_once_with(receiver, 42, folder="Drafts")
        mail_cache_stub.sync_folder_to_cache.assert_awaited_once_with(account, "Drafts")
        sync_stub.sync_service.refresh_clients.assert_awaited_once_with("account-1", "Drafts", user_uid="user-1")


if __name__ == "__main__":
    unittest.main()
