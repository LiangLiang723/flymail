import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.sync import MailSyncService
from services.sync_coordinator import AccountSyncCoordinator


class AccountSyncCoordinatorTest(unittest.IsolatedAsyncioTestCase):
    async def test_exclusive_blocks_interactive_and_background_operations(self):
        coordinator = AccountSyncCoordinator()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def hold_exclusive():
            async with coordinator.exclusive("account-1") as allowed:
                self.assertTrue(allowed)
                entered.set()
                await release.wait()

        task = asyncio.create_task(hold_exclusive())
        await entered.wait()
        try:
            async with coordinator.interactive("account-1") as allowed:
                self.assertFalse(allowed)
            async with coordinator.background("account-1") as allowed:
                self.assertFalse(allowed)
            self.assertTrue(await coordinator.is_exclusive("account-1"))
        finally:
            release.set()
            await task

    async def test_interactive_operation_blocks_new_background_operation(self):
        coordinator = AccountSyncCoordinator()

        async with coordinator.interactive("account-1") as interactive_allowed:
            self.assertTrue(interactive_allowed)
            async with coordinator.background("account-1") as background_allowed:
                self.assertFalse(background_allowed)

    async def test_exclusive_waiter_blocks_new_operations_until_background_finishes(self):
        coordinator = AccountSyncCoordinator()
        background_entered = asyncio.Event()
        release_background = asyncio.Event()
        exclusive_entered = asyncio.Event()

        async def hold_background():
            async with coordinator.background("account-1") as allowed:
                self.assertTrue(allowed)
                background_entered.set()
                await release_background.wait()

        async def wait_for_exclusive():
            async with coordinator.exclusive("account-1") as allowed:
                self.assertTrue(allowed)
                exclusive_entered.set()

        background_task = asyncio.create_task(hold_background())
        await background_entered.wait()
        exclusive_task = asyncio.create_task(wait_for_exclusive())
        await asyncio.sleep(0)

        async with coordinator.interactive("account-1") as allowed:
            self.assertFalse(allowed)
        async with coordinator.background("account-1") as allowed:
            self.assertFalse(allowed)

        release_background.set()
        await background_task
        await exclusive_entered.wait()
        await exclusive_task


class SyncSchedulingRulesTest(unittest.IsolatedAsyncioTestCase):
    def test_provider_specific_periodic_intervals_have_safe_minimums(self):
        service = MailSyncService()

        self.assertEqual(
            service._periodic_sync_interval(SimpleNamespace(provider="qq", poll_interval_seconds=10)),
            180,
        )
        self.assertEqual(
            service._periodic_sync_interval(SimpleNamespace(provider="icloud", poll_interval_seconds=10)),
            120,
        )
        self.assertEqual(
            service._periodic_sync_interval(SimpleNamespace(provider="custom", poll_interval_seconds=10)),
            60,
        )
        self.assertEqual(
            service._periodic_sync_interval(SimpleNamespace(provider="custom", poll_interval_seconds=600)),
            600,
        )

    async def test_periodic_account_tasks_can_run_for_different_accounts_concurrently(self):
        service = MailSyncService()
        active = 0
        max_active = 0
        release = asyncio.Event()
        both_started = asyncio.Event()

        async def sync_recent(_account, _folder):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            if active == 2:
                both_started.set()
            await release.wait()
            active -= 1
            return 0

        accounts = [
            SimpleNamespace(id="account-1", email="a@example.com", user_uid="user-1"),
            SimpleNamespace(id="account-2", email="b@example.com", user_uid="user-1"),
        ]
        with (
            patch.object(service, "_get_idle_folders_from_config", AsyncMock(return_value=["INBOX"])),
            patch("services.mail_cache.sync_recent_folder_to_cache", AsyncMock(side_effect=sync_recent)),
        ):
            tasks = [asyncio.create_task(service._periodic_sync_account(account)) for account in accounts]
            await asyncio.wait_for(both_started.wait(), timeout=1)
            release.set()
            await asyncio.gather(*tasks)

        self.assertEqual(max_active, 2)

    async def test_reconnect_delay_advances_after_idle_provider_failures(self):
        service = MailSyncService()
        service._running = True
        account = SimpleNamespace(
            id="account-1",
            user_uid="user-1",
            email="a@example.com",
            provider="qq",
            status="connected",
            poll_interval_seconds=10,
        )
        delays = []

        async def fail_token(_account):
            raise RuntimeError("temporary network failure")

        async def fake_sleep(delay):
            delays.append(delay)
            if len(delays) >= 4:
                service._running = False

        with (
            patch("services.sync.get_accounts", AsyncMock(return_value=[account])),
            patch("services.token.ensure_token", AsyncMock(side_effect=fail_token)),
            patch("services.sync.asyncio.sleep", AsyncMock(side_effect=fake_sleep)),
            patch.object(service, "notify_connection_status", AsyncMock()),
        ):
            await service._idle_loop(account)

        self.assertEqual(delays[:4], [5, 5, 5, 10])

    async def test_poll_connection_repair_reuses_live_folder_and_rebuilds_dead_folder(self):
        service = MailSyncService()
        account = SimpleNamespace(id="account-1", email="a@example.com")
        live = SimpleNamespace(connected=True)
        dead = SimpleNamespace(connected=False)
        replacement = SimpleNamespace(connected=True)
        manager = SimpleNamespace(get_or_create=AsyncMock(return_value=replacement))
        connections = {"INBOX": live, "Drafts": dead}
        config = {
            "host": "imap.example.com",
            "port": 993,
            "email": "a@example.com",
            "auth_type": "login",
            "auth_credential": "secret",
        }

        result = await service._ensure_poll_connections(
            account,
            ["INBOX", "Drafts"],
            connections,
            manager,
            config,
        )

        self.assertIs(result["INBOX"], live)
        self.assertIs(result["Drafts"], replacement)
        manager.get_or_create.assert_awaited_once_with(
            "account-1",
            **config,
            folder="Drafts",
        )

    async def test_non_idle_status_scan_checks_all_folders_in_one_batch(self):
        service = MailSyncService()
        account = SimpleNamespace(id="account-1", email="a@example.com", user_uid="user-1")
        receiver = SimpleNamespace(
            fetch_folder_counts=AsyncMock(return_value={
                "INBOX": {"total": 11, "unread": 3},
                "ROVO": {"total": 5, "unread": 0},
            })
        )
        previous = {
            "INBOX": {"total": 10, "unread": 2},
            "ROVO": {"total": 5, "unread": 0},
        }

        with patch.object(service, "_handle_new_mail", AsyncMock()) as handle_new_mail:
            current = await service._scan_non_idle_folders(
                receiver,
                account,
                ["INBOX", "ROVO"],
                previous,
            )

        receiver.fetch_folder_counts.assert_awaited_once_with(["INBOX", "ROVO"])
        handle_new_mail.assert_awaited_once_with(account, "INBOX")
        self.assertEqual(current["INBOX"]["total"], 11)


class SyncWebSocketProgressTest(unittest.IsolatedAsyncioTestCase):
    async def test_history_sync_update_targets_current_user(self):
        service = MailSyncService()
        with patch.object(service, "_broadcast", AsyncMock()) as broadcast:
            await service.notify_history_sync_updated("account-1", "user-1")

        payload = broadcast.await_args.args[0]
        self.assertIn('"type": "history_sync_updated"', payload)
        self.assertIn('"account_id": "account-1"', payload)
        self.assertEqual(broadcast.await_args.args[1], "user-1")


class SyncSuspensionReferenceCountTest(unittest.IsolatedAsyncioTestCase):
    async def test_nested_suspensions_resume_listener_only_after_last_release(self):
        service = MailSyncService()
        service._running = True

        with (
            patch.object(service, "remove_account", AsyncMock()) as remove_account,
            patch.object(service, "add_account", AsyncMock()) as add_account,
        ):
            await service.suspend_account("account-1")
            await service.suspend_account("account-1")

            remove_account.assert_awaited_once_with("account-1")
            self.assertTrue(service.is_account_suspended("account-1"))

            await service.resume_account("account-1")
            add_account.assert_not_awaited()
            self.assertTrue(service.is_account_suspended("account-1"))

            await service.resume_account("account-1")
            add_account.assert_awaited_once_with("account-1")
            self.assertFalse(service.is_account_suspended("account-1"))


if __name__ == "__main__":
    unittest.main()
