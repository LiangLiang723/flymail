"""FlyMail V2 deterministic capacity dataset and query benchmark contracts."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import statistics
import sys
import tempfile
import time
import unittest
import warnings
from pathlib import Path
from typing import Any, Awaitable, Callable

from flymail.infrastructure.db.migrations.runner import run_migrations
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
GENERATOR_PATH = REPOSITORY_ROOT / "scripts" / "generate-v2-benchmark-data.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("flymail_v2_benchmark_generator", GENERATOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load capacity generator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _percentile(samples: list[float], percentile: float) -> float:
    ordered = sorted(samples)
    index = min(max(int(round((len(ordered) - 1) * percentile)), 0), len(ordered) - 1)
    return ordered[index]


class CapacityGeneratorSmallScaleTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.capacity_temp = tempfile.TemporaryDirectory(prefix="flymail-v2-capacity-small-")

    async def asyncTearDown(self) -> None:
        self.capacity_temp.cleanup()
        await super().asyncTearDown()

    async def test_small_scale_dataset_is_deterministic_isolated_and_representative(self):
        generator = _load_generator()
        config = generator.GenerationConfig(
            database_url=self.database_url(),
            users=5,
            accounts=12,
            messages=200,
            seed=20260731,
            batch_size=50,
            body_cache_ratio=0.10,
            object_root=str(Path(self.capacity_temp.name) / "objects"),
            reset=True,
        )

        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            first = await generator.generate(config)
            fingerprint = generator.dataset_fingerprint(config)
            second = await generator.generate(config)
        self.assertEqual(captured, [])

        self.assertEqual(first, second)
        self.assertEqual(first["fingerprint"], fingerprint)
        self.assertEqual(first["users"], 5)
        self.assertEqual(first["accounts"], 12)
        self.assertEqual(first["messages"], 200)
        self.assertEqual(first["threads"], 50)
        self.assertEqual(first["thread_projections"], 50)
        self.assertEqual(first["body_documents"], 20)

        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM thread_messages"), 200)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM message_memberships"), 200)
        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM users WHERE username LIKE 'bench-user-%%'"),
            5,
        )
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM mail_accounts WHERE normalized_email NOT LIKE '%%@example.test'"
            ),
            0,
        )
        self.assertEqual(
            await self.scalar(
                """
                SELECT COUNT(*)
                FROM messages m JOIN threads t ON t.id=m.thread_id
                WHERE m.user_uid <> t.user_uid
                """
            ),
            0,
        )
        self.assertGreater(
            await self.scalar(
                """
                SELECT COUNT(*) FROM (
                    SELECT tm.thread_id
                    FROM thread_messages tm
                    JOIN message_remote_instances ri ON ri.message_id=tm.message_id
                    GROUP BY tm.thread_id
                    HAVING COUNT(DISTINCT ri.account_id) > 1
                ) cross_account
                """
            ),
            0,
        )
        self.assertGreater(
            await self.scalar(
                "SELECT COUNT(*) FROM mailboxes WHERE mailbox_type='label' AND semantic_key='label'"
            ),
            0,
        )
        self.assertGreater(
            await self.scalar(
                "SELECT COUNT(*) FROM account_runtime_state WHERE status='active'"
            ),
            0,
        )
        self.assertGreater(
            await self.scalar(
                "SELECT COUNT(*) FROM account_runtime_state WHERE status='quiet'"
            ),
            0,
        )
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM body_search_documents WHERE body_text LIKE '%%capacityterm%%'"
            ),
            20,
        )

    def test_generation_parallelism_is_fixed_and_bounded(self):
        generator = _load_generator()
        self.assertEqual(generator.GENERATION_WRITE_CONCURRENCY, 4)
        self.assertLessEqual(generator.GENERATION_WRITE_CONCURRENCY, 4)

    def test_generation_commits_group_logical_batches_without_unbounded_transactions(self):
        generator = _load_generator()
        self.assertFalse(generator.should_commit_batch(5_000, 20_000_000, 5_000))
        self.assertFalse(generator.should_commit_batch(95_000, 20_000_000, 5_000))
        self.assertTrue(generator.should_commit_batch(100_000, 20_000_000, 5_000))
        self.assertTrue(generator.should_commit_batch(105_000, 105_000, 5_000))
        self.assertTrue(generator.should_commit_batch(250_000, 20_000_000, 250_000))

    async def test_deferred_fulltext_indexes_are_removed_and_restored_together(self):
        generator = _load_generator()
        await run_migrations(self.api_pool)
        async with self.api_pool.acquire() as connection:
            await generator._drop_deferred_indexes(connection)
            dropped = await generator._existing_indexes(
                connection,
                "body_search_documents",
            )
            self.assertNotIn("ft_body_search", dropped)
            self.assertNotIn("ft_body_search_standard", dropped)

            await generator._restore_deferred_indexes(connection)
            restored = await generator._existing_indexes(
                connection,
                "body_search_documents",
            )
            self.assertIn("ft_body_search", restored)
            self.assertIn("ft_body_search_standard", restored)

    async def test_deferred_capacity_indexes_are_restored_after_bulk_generation(self):
        generator = _load_generator()
        config = generator.GenerationConfig(
            database_url=self.database_url(),
            users=2,
            accounts=4,
            messages=40,
            seed=20260731,
            batch_size=20,
            body_cache_ratio=0.10,
            object_root=str(Path(self.capacity_temp.name) / "deferred-objects"),
            reset=True,
            defer_indexes=True,
        )
        await generator.generate(config)
        expected = {
            "uq_threads_user_key",
            "idx_thread_projection_cursor",
            "uq_messages_user_key",
            "idx_messages_user_received",
            "idx_messages_subject_fallback",
            "idx_remote_instances_message",
            "idx_memberships_user_mailbox",
            "idx_thread_messages_user_message",
            "idx_message_bodies_user_state",
            "idx_content_references_user_lru",
            "ft_body_search",
            "ft_body_search_standard",
        }
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT DISTINCT index_name FROM information_schema.statistics WHERE table_schema = DATABASE()"
                )
                actual = {str(row[0]) for row in await cursor.fetchall()}
        self.assertTrue(expected.issubset(actual), expected - actual)

    def test_explain_plan_checks_require_promised_indexes_and_reject_table_scans(self):
        generator = _load_generator()
        passing = generator.evaluate_plan(
            "thread_list",
            ["Index lookup on thread_projections using idx_thread_projection_cursor"],
        )
        missing = generator.evaluate_plan(
            "thread_list",
            ["Index lookup on thread_projections using PRIMARY"],
        )
        scanning = generator.evaluate_plan(
            "thread_list",
            ["Table scan on thread_projections using idx_thread_projection_cursor"],
        )
        self.assertTrue(passing["passed"])
        self.assertFalse(missing["passed"])
        self.assertFalse(scanning["passed"])

    def test_production_targets_and_realistic_addresses_are_rejected(self):
        generator = _load_generator()
        with self.assertRaises(ValueError):
            generator.validate_target(
                "mysql://user:pass@127.0.0.1:3306/flymail",
                "/tmp/objects",
            )
        with self.assertRaises(ValueError):
            generator.validate_target(
                "mysql://user:pass@127.0.0.1:3306/flymail_v2_capacity",
                "/Docker/flymail/data/objects",
            )
        self.assertTrue(generator.synthetic_address(7).endswith("@example.test"))


async def _measure(
    name: str,
    operation: Callable[[], Awaitable[Any]],
    *,
    warmups: int = 5,
    samples: int = 30,
) -> dict[str, Any]:
    for _ in range(warmups):
        await operation()
    timings: list[float] = []
    for _ in range(samples):
        started = time.perf_counter()
        await operation()
        timings.append((time.perf_counter() - started) * 1000.0)
    return {
        "name": name,
        "samples": samples,
        "p50_ms": round(_percentile(timings, 0.50), 3),
        "p95_ms": round(_percentile(timings, 0.95), 3),
        "p99_ms": round(_percentile(timings, 0.99), 3),
        "mean_ms": round(statistics.fmean(timings), 3),
    }


async def run_capacity_benchmark(database_url: str, output: Path) -> dict[str, Any]:
    generator = _load_generator()
    return await generator.run_benchmark(database_url=database_url, output=output, measure=_measure)


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--database-url", default=os.getenv("FLYMAIL_CAPACITY_DATABASE_URL", ""))
    parser.add_argument("--output", type=Path)
    args, remaining = parser.parse_known_args()
    if not args.benchmark:
        unittest.main(argv=[sys.argv[0], *remaining])
        return 0
    if not args.database_url or args.output is None:
        parser.error("--benchmark requires --database-url and --output")
    result = asyncio.run(run_capacity_benchmark(args.database_url, args.output))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
