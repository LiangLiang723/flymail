# History Sync Missing Message Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent locally cached summaries for remotely deleted IMAP UIDs from causing history body-fill jobs to fail with repeated no-progress retries.

**Architecture:** Define a structured `MessageNotFoundError` at the provider boundary and raise it from the shared IMAP detail fetch path. History body fill treats only that permanent absence as a completed empty-body check, preserving the local summary; all other exceptions remain retryable and continue to trigger the existing bounded failure behavior.

**Tech Stack:** Python 3.12, FastAPI services, shared IMAP provider base, `unittest` and `AsyncMock`, Docker/MySQL 8.0.

## Global Constraints

- Do not delete cached messages, attachments, users, or `/Docker/flymail/data`.
- Preserve user and account isolation.
- Do not log credentials, tokens, session secrets, or database URLs with passwords.
- Keep `MessageNotFoundError` compatible with existing `ValueError` handling.
- Do not change retry behavior for connection, authentication, parsing, or other transient failures.
- Bump `VERSION` and synchronize all version references through `npm run sync-version`.

---

### Task 1: Add a structured missing-message provider error

**Files:**
- Modify: `backend/providers/base.py`
- Modify: `backend/providers/base_imap.py`
- Test: `backend/tests/test_imap_dates.py`

**Interfaces:**
- Produces: `providers.base.MessageNotFoundError(ValueError)`.
- Produces: `BaseIMAPReceiver._fetch_detail_sync()` raises `MessageNotFoundError` when UID FETCH is non-OK or contains no raw message bytes.

- [ ] **Step 1: Write the failing test**

Add a fake IMAP connection that selects INBOX successfully but returns no message content, then assert the provider module exposes `MessageNotFoundError` and `_fetch_detail_sync("42", "INBOX")` raises it.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python -m unittest tests.test_imap_dates.ImapDateParsingTest.test_missing_uid_raises_structured_message_not_found -v`

Expected: FAIL because `providers.base.MessageNotFoundError` does not exist.

- [ ] **Step 3: Implement the minimal provider change**

Add:

```python
class MessageNotFoundError(ValueError):
    """The requested remote message UID no longer exists in the selected folder."""
```

Import it in `base_imap.py` and replace only the two `ValueError(f"Message {uid} not found")` raises with `MessageNotFoundError`.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run: `python -m unittest tests.test_imap_dates.ImapDateParsingTest.test_missing_uid_raises_structured_message_not_found -v`

Expected: PASS.

### Task 2: Let history body fill skip only permanently missing UIDs

**Files:**
- Modify: `backend/services/history_sync.py`
- Test: `backend/tests/test_history_sync_folders.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `providers.base.MessageNotFoundError`.
- Produces: `_fill_unchecked_message_bodies()` includes permanently missing UIDs in both `mark_cached_messages_body_checked()` and `mark_cached_messages_empty_body_checked()` calls.
- Preserves: Other exceptions do not add the UID to the checked list.

- [ ] **Step 1: Write the failing regression test**

Configure `_cache_message_detail()` to raise `MessageNotFoundError("Message 101 not found")` for one selected row. Assert the body-fill loop marks UID 101 checked and empty-checked, reports one processed item, and exits when the next query returns no rows.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python -m unittest tests.test_history_sync_folders.HistorySyncFastRefreshTest.test_body_fill_marks_missing_remote_message_complete -v`

Expected: FAIL because the current generic exception path leaves `checked_uids` empty.

- [ ] **Step 3: Implement the minimal history-sync change**

Catch `MessageNotFoundError` before the generic exception handler, log an informational message containing only account, folder, and UID, and append the UID to `checked_uids`. Keep the generic exception branch unchanged.

- [ ] **Step 4: Run focused and related tests**

Run:

```bash
python -m unittest \
  tests.test_history_sync_folders.HistorySyncFastRefreshTest.test_body_fill_marks_missing_remote_message_complete \
  tests.test_history_sync_folders.HistorySyncFastRefreshTest.test_history_sync_fails_after_limited_body_fill_retries_without_progress \
  tests.test_imap_dates -v
```

Expected: PASS, including the existing bounded retry failure test.

- [ ] **Step 5: Update documentation and version**

Document that remotely deleted messages retain local summaries and are recorded as unavailable rather than blocking history sync. After completing all regression fixes in this plan, set `VERSION` to `0.0.32`, then run `npm run sync-version`.

- [ ] **Step 6: Run full verification and deploy**

Run backend tests, frontend tests/build, shell and Compose checks, Docker build, isolated temporary-container persistence/security checks, then replace `flymail` while preserving `/Docker/flymail/data:/data` and its published port. Use the authenticated retry endpoint for the affected account and verify the job completes without deleting its eight cached summaries.

### Task 3: Show the latest history-sync result after retries

**Files:**
- Modify: `backend/routes/settings.py`
- Test: `backend/tests/test_history_sync_progress.py`

**Interfaces:**
- Consumes: `list_history_sync_jobs()` returns jobs ordered by `updated_at DESC, created_at DESC`.
- Produces: `_latest_jobs_by_type(jobs)` keeps the first, newest record for each `job_type`.
- Produces: `/api/history-sync/jobs/{account_id}` returns the newest history-sync job instead of an older failed record.

- [ ] **Step 1: Write the failing route regression test**

Pass the detail endpoint two `history_sync` jobs ordered newest-first: a completed `job-new` and an older failed `job-old`. Assert the response selects `job-new`.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python -m unittest tests.test_history_sync_progress.HistorySyncProgressTest.test_history_sync_detail_uses_latest_job_of_each_type -v`

Expected: FAIL with `job-old != job-new` because the existing dictionary comprehension overwrites the newest entry with the older one.

- [ ] **Step 3: Implement the minimal selection fix**

Add `_latest_jobs_by_type()` that only assigns a non-empty `job_type` when it is not already present, then use it in `get_history_sync_job_detail()`.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run: `python -m unittest tests.test_history_sync_progress.HistorySyncProgressTest.test_history_sync_detail_uses_latest_job_of_each_type -v`

Expected: PASS.

- [ ] **Step 5: Verify the production API after deployment**

Call `/api/history-sync/jobs/{account_id}` with an authenticated session and assert it returns the completed newest job, while MySQL still contains all eight cached summaries and eight unavailable-body markers.
