# Contact Stats SQL Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make contact interaction statistics return the real locally cached mail count instead of silently showing zero when the MySQL prefilter query fails.

**Architecture:** Keep the existing two-stage matching model: MySQL performs a cheap literal substring prefilter and Python `email.utils.getaddresses` confirms exact mailbox equality. Replace the broken wildcard `LIKE ... ESCAPE` prefilter with `LOCATE(?, LOWER(field)) > 0`, which treats `%` and `_` literally and avoids MySQL escape syntax entirely. Do not change contact storage, cached mail data, or remote mailbox behavior.

**Tech Stack:** FastAPI, Python 3.12, aiomysql/PyMySQL, MySQL 8.0, unittest, Vue 3, Docker.

## Global Constraints

- Work only in `/home/chatgpt/flymail` on `main`.
- Preserve `/Docker/flymail/data`; no database migration or data deletion.
- Keep user isolation: every stats query must include `cached_messages.user_uid = current user`.
- Count only exact parsed email-address matches after the SQL prefilter.
- Treat `%`, `_`, backslashes, plus-addresses, and other valid mailbox characters as literal search text, not SQL wildcards.
- Do not connect to IMAP/SMTP for contact statistics; use local `cached_messages` only.
- Do not add or upgrade production dependencies.
- Release as `0.0.41`, build only `benxianyu/flymail:0.0.41`, and do not upload Docker Hub.

---

### Task 1: Reproduce and lock the MySQL-safe contact stats query

**Files:**
- Modify: `backend/tests/test_contacts_isolation.py`
- Modify: `backend/db/__init__.py`

**Interfaces:**
- Consumes: `get_contact_stats(user_uid: str, email: str) -> dict` and `_address_field_contains_email`.
- Produces: the same response shape `{count: int, last_date: str}`, with a MySQL-safe literal prefilter.

- [x] **Step 1: Add a failing regression test**

Add a fake DB test that calls `get_contact_stats("user-a", "neal_chen@example.com")`, returns rows containing an exact address plus a deceptive longer address, and asserts:

```python
sql, params = fake.calls[0]
self.assertIn("LOCATE", sql.upper())
self.assertNotIn(" ESCAPE ", sql.upper())
self.assertEqual(tuple(params), (
    "user-a",
    "neal_chen@example.com",
    "neal_chen@example.com",
    "neal_chen@example.com",
))
self.assertEqual(result, {"count": 1, "last_date": "2026-08-07T10:00:00Z"})
```

Use rows where one header contains `Neal <neal_chen@example.com>` and another contains `neal_chen@example.com.invalid` so the Python exact matcher proves substrings are not over-counted.

- [x] **Step 2: Run the focused test and confirm RED**

Run:

```bash
cd backend
python -m unittest tests.test_contacts_isolation -v
```

Expected: the new test fails because current SQL still uses `LIKE ... ESCAPE`.

- [x] **Step 3: Implement the minimal SQL fix**

Replace the escaped wildcard pattern construction and SQL with:

```python
cursor = await db.execute(
    """SELECT date, from_addr, to_addr, cc FROM cached_messages
       WHERE user_uid = ? AND (
         LOCATE(?, LOWER(from_addr)) > 0
         OR LOCATE(?, LOWER(to_addr)) > 0
         OR LOCATE(?, LOWER(cc)) > 0
       )""",
    (user_uid, normalized, normalized, normalized),
)
```

Keep `_address_field_contains_email` and the existing exact-filter list comprehension unchanged.

- [x] **Step 4: Verify focused tests**

Run:

```bash
cd backend
python -m unittest tests.test_contacts_isolation tests.test_contact_candidates -v
```

Expected: all pass.

---

### Task 2: Release and complete verification

**Files:**
- Modify: `VERSION`
- Modify via version sync: `package.json`
- Modify via version sync: `frontend/package.json`
- Modify via version sync: `docker-compose.yml`
- Modify: `README.md` only if contact-statistics behavior needs user-facing clarification
- Update: `docs/superpowers/plans/2026-08-07-contact-stats-sql-fix.md`

**Interfaces:**
- Produces release `0.0.41` and local image `benxianyu/flymail:0.0.41`.
- Production remains container `flymail` with `/Docker/flymail/data:/data` and the existing port/network/restart policy.

- [x] **Step 1: Bump and synchronize version**

Set `VERSION` to `0.0.41`, synchronize `package.json`, `frontend/package.json`, `docker-compose.yml`, and README through DevSpace file edits, then verify VERSION/root/frontend/Compose/README image tags all match `0.0.41`. The DevSpace shell is read/test/build-only, so the write-producing `npm run sync-version` script is not used directly.

- [x] **Step 2: Run complete code verification**

Run:

```bash
cd backend && python -m unittest discover -s tests -v
cd ../frontend && npm install && npm test && npm run build
cd ..
bash -n scripts/docker-entrypoint.sh
docker compose config
git diff --check
git status --short
git diff
```

If `.env` is intentionally absent, validate Compose with `.env.example` without exposing production secrets.

- [x] **Step 3: Build and validate isolated Docker/MySQL**

Build `docker build -t benxianyu/flymail:0.0.41 .`. Start a uniquely named temporary container with a unique temporary `/Docker/flymail/` data directory and a database password containing special characters. Verify health/version, MySQL 8.0 `/data/mysql/`, database read/write, contact-stats SQL against seeded cached messages including an underscore mailbox, restart persistence, log redaction, image metadata secret absence, and safe MySQL shutdown. Clean the temporary container/data.

- [x] **Step 4: Replace production with rollback protection**

Capture the existing `flymail` image, port, mount, network, restart policy, health, MySQL version/datadir, and non-sensitive data fingerprint. Preserve the old container while starting `benxianyu/flymail:0.0.41` with identical settings. Require health `0.0.41`, unchanged data fingerprint, and successful contact-statistics reads for existing contacts without printing real addresses. Restart once more before deleting rollback.

- [x] **Step 5: Final Git review and push**

Mark plan steps complete, run fresh verification, stage only this task's files, review `git diff --staged` and a secret scan, commit with `🐛 修复联系人往来邮件统计查询失败`, fetch origin, confirm remote is not ahead, and push `origin/main` without force. Verify local and remote SHA match and the workspace is clean.
