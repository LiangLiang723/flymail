import unittest
from unittest.mock import AsyncMock, patch

import db


HISTORY_JOB_COLUMNS = [
    "id",
    "account_id",
    "user_uid",
    "job_type",
    "status",
    "current_folder",
    "current_page",
    "current_uid",
    "total_folders",
    "completed_folders",
    "fetched_messages",
    "downloaded_attachments",
    "downloaded_inline_images",
    "error_message",
    "created_at",
    "updated_at",
    "finished_at",
]


class _FakeCursor:
    description = [(column,) for column in HISTORY_JOB_COLUMNS]

    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return self._rows


class _FakeDb:
    def __init__(self, rows):
        self._cursor = _FakeCursor(rows)

    async def execute(self, _sql, _params):
        return self._cursor


class HistorySyncJobStalenessTests(unittest.IsolatedAsyncioTestCase):
    async def test_touch_updates_only_active_job_timestamp(self):
        database = AsyncMock()
        database.execute.return_value = type("Cursor", (), {"rowcount": 1})()

        with (
            patch.object(db, "get_db", AsyncMock(return_value=database)),
            patch.object(db.time, "time", return_value=702.0),
        ):
            touched = await db.touch_history_sync_job("job-1")

        self.assertTrue(touched)
        sql, params = database.execute.await_args.args
        self.assertIn("status IN ('pending', 'running')", sql)
        self.assertEqual(params, (702.0, "job-1"))
        database.commit.assert_awaited_once()

    async def test_list_marks_stale_running_job_failed(self):
        row = (
            "job-1",
            "account-1",
            "user-1",
            "history_sync",
            "running",
            "INBOX",
            1,
            0,
            5,
            0,
            0,
            0,
            0,
            "",
            50.0,
            100.0,
            0.0,
        )
        update_job = AsyncMock()

        with (
            patch.object(db, "get_db", AsyncMock(return_value=_FakeDb([row]))),
            patch.object(db, "update_history_sync_job", update_job),
            patch.object(db.time, "time", return_value=701.0),
        ):
            jobs = await db.list_history_sync_jobs("user-1")

        self.assertEqual(jobs[0]["status"], "failed")
        self.assertEqual(
            jobs[0]["error_message"],
            "同步任务超过 10 分钟没有进度，已标记为失败，可重试",
        )
        self.assertEqual(jobs[0]["finished_at"], 701.0)
        update_job.assert_awaited_once_with(
            "job-1",
            status="failed",
            error_message="同步任务超过 10 分钟没有进度，已标记为失败，可重试",
            finished_at=701.0,
        )

    async def test_list_keeps_recent_running_job_active(self):
        row = (
            "job-2",
            "account-2",
            "user-1",
            "history_sync",
            "running",
            "INBOX",
            1,
            0,
            5,
            0,
            0,
            0,
            0,
            "",
            650.0,
            650.0,
            0.0,
        )
        update_job = AsyncMock()

        with (
            patch.object(db, "get_db", AsyncMock(return_value=_FakeDb([row]))),
            patch.object(db, "update_history_sync_job", update_job),
            patch.object(db.time, "time", return_value=701.0),
        ):
            jobs = await db.list_history_sync_jobs("user-1")

        self.assertEqual(jobs[0]["status"], "running")
        update_job.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
