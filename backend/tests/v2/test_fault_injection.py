"""Gate 5 deterministic fault-injection and recovery matrix.

The matrix executes existing production-path tests through their dependency-injected
fake transports and repositories. It intentionally adds no production chaos flag.
"""

from __future__ import annotations

import io
import unittest
from pathlib import Path


SCENARIOS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "api_before_commit": (
        (
            "tests.v2.test_reliable_sender.ReliableSenderTests."
            "test_queue_rolls_back_draft_operation_job_and_outbox_together",
        ),
        ("no_lost_committed_task", "safe_user_error"),
    ),
    "api_after_commit": (
        (
            "tests.v2.test_foundation_integration.FoundationIntegrationTests."
            "test_empty_database_to_restart_and_last_reference_cleanup",
        ),
        ("no_lost_committed_task", "restart_recovery", "no_referenced_object_deletion"),
    ),
    "worker_after_lease_before_remote": (
        (
            "tests.v2.test_worker_scheduler.WorkerSchedulerIntegrationTests."
            "test_shutdown_timeout_cancels_handler_and_releases_worker_lease",
        ),
        ("no_lost_committed_task", "restart_recovery", "explicit_state"),
    ),
    "worker_after_remote_before_local_completion": (
        (
            "tests.v2.test_reliable_sender.ReliableSenderTests."
            "test_append_database_failure_never_repeats_remote_append",
        ),
        ("no_duplicate_smtp", "explicit_state", "safe_user_error"),
    ),
    "mysql_unavailable_claim_and_completion": (
        (
            "tests.v2.test_worker_scheduler.WorkerSchedulerIntegrationTests."
            "test_infrastructure_failure_releases_lease_before_propagating",
            "tests.v2.test_operation_apply.OperationApplyTests."
            "test_database_finish_failure_is_not_misclassified_as_remote_error",
        ),
        ("no_lost_committed_task", "restart_recovery", "explicit_state"),
    ),
    "object_temp_write_failure": (
        (
            "tests.v2.test_object_store.ObjectStoreFilesystemTests."
            "test_interrupted_write_leaves_no_final_or_temporary_file",
        ),
        ("no_referenced_object_deletion", "explicit_state"),
    ),
    "object_missing_after_reference": (
        (
            "tests.v2.test_object_store.ObjectStoreFilesystemTests."
            "test_missing_and_corrupt_objects_have_explicit_verification_states",
        ),
        ("no_referenced_object_deletion", "explicit_state", "safe_user_error"),
    ),
    "imap_stream_disconnect": (
        (
            "tests.v2.test_content_fetch.ContentFetchTests."
            "test_cancelled_body_fetch_does_not_leave_fetching_state",
        ),
        ("no_lost_committed_task", "explicit_state", "restart_recovery"),
    ),
    "smtp_disconnect_after_data": (
        (
            "tests.v2.test_reliable_sender.ReliableSenderTests."
            "test_disconnect_after_data_enters_verification_without_direct_resend",
        ),
        ("no_duplicate_smtp", "explicit_state", "safe_user_error"),
    ),
    "outbox_crash_after_persistence": (
        (
            "tests.v2.test_jobs_outbox.JobsAndOutboxTests."
            "test_outbox_and_business_job_share_transaction_atomicity",
        ),
        ("no_lost_committed_task", "explicit_state"),
    ),
    "idle_half_open_timeout": (
        (
            "tests.v2.test_idle_reconciliation.IdleSupervisorTests."
            "test_idle_refreshes_before_timeout_and_releases_session",
        ),
        ("restart_recovery", "explicit_state"),
    ),
    "provider_rate_limit_storm": (
        (
            "tests.v2.test_worker_scheduler.FairSchedulerTests."
            "test_provider_cooldown_does_not_block_another_provider",
            "tests.v2.test_idle_reconciliation.ReconciliationPlannerTests."
            "test_failures_back_off_exponentially_with_jitter_and_cooldown",
        ),
        ("no_lost_committed_task", "restart_recovery", "safe_user_error"),
    ),
    "sigterm_during_history_batch": (
        (
            "tests.v2.test_jobs_outbox.JobsAndOutboxTests."
            "test_worker_cli_handles_sigterm_and_closes_cleanly",
        ),
        ("no_lost_committed_task", "restart_recovery", "explicit_state"),
    ),
    "clock_adjustment_scheduled_send": (
        (
            "tests.v2.test_reliable_sender.ReliableSenderTests."
            "test_queue_is_atomic_stable_and_scheduled_job_uses_available_at",
            "tests.v2.test_idle_reconciliation.ReconciliationPlannerTests."
            "test_never_viewed_account_is_not_active_when_clock_is_near_epoch",
        ),
        ("no_lost_committed_task", "explicit_state", "safe_user_error"),
    ),
}

REQUIRED_INVARIANTS = {
    "no_lost_committed_task",
    "no_duplicate_smtp",
    "no_wrong_user_access",
    "no_referenced_object_deletion",
    "explicit_state",
    "restart_recovery",
    "safe_user_error",
}

# Tenant isolation is already a direct fault boundary in the remote operation suite.
TENANT_ISOLATION_TEST = (
    "tests.v2.test_operation_apply.OperationApplyTests."
    "test_cross_tenant_remote_id_is_indistinguishable_from_missing"
)
CONCURRENCY_TEST = (
    "tests.v2.test_jobs_outbox.JobsAndOutboxTests."
    "test_two_workers_skip_locked_rows_without_duplicate_claims"
)


def _run_test_names(names: tuple[str, ...]) -> tuple[bool, str, int]:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite(loader.loadTestsFromName(name) for name in names)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=0).run(suite)
    return result.wasSuccessful(), stream.getvalue(), result.testsRun


class FaultInjectionMatrixTests(unittest.TestCase):
    def test_no_production_chaos_environment_backdoor_exists(self):
        root = Path(__file__).resolve().parents[2] / "flymail"
        for source in root.rglob("*.py"):
            self.assertNotIn("FLYMAIL_CHAOS_MODE", source.read_text(encoding="utf-8"), source)

    def test_required_scenarios_execute_and_preserve_invariants(self):
        self.assertEqual(len(SCENARIOS), 14)
        covered = set()
        total = 0
        for scenario, (tests, invariants) in SCENARIOS.items():
            success, output, count = _run_test_names(tests)
            self.assertTrue(success, f"fault scenario {scenario} failed:\n{output}")
            self.assertGreater(count, 0, scenario)
            total += count
            covered.update(invariants)

        success, output, count = _run_test_names((TENANT_ISOLATION_TEST,))
        self.assertTrue(success, f"tenant isolation fault scenario failed:\n{output}")
        self.assertEqual(count, 1)
        total += count
        covered.add("no_wrong_user_access")

        self.assertEqual(covered, REQUIRED_INVARIANTS)
        self.assertGreaterEqual(total, 17)

    def test_concurrent_claim_invariants_hold_for_twenty_iterations(self):
        for iteration in range(20):
            success, output, count = _run_test_names((CONCURRENCY_TEST,))
            self.assertTrue(
                success,
                f"concurrency fault iteration {iteration + 1} failed:\n{output}",
            )
            self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
