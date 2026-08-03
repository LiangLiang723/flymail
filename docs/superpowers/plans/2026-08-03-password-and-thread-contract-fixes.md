# Password and Thread Contract Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow every user-entered password or authorization secret to use any non-empty length and fix the post-login thread list crash caused by the frontend reading `threads` from an API that returns `items`.

**Architecture:** Keep internal security-key requirements unchanged, but centralize user-password behavior at request and business boundaries as non-empty raw strings with no application-level maximum. Treat the backend thread list schema as the source of truth and normalize raw `ThreadListItemResponse` objects into the existing frontend `ThreadProjection` view model before caching or rendering.

**Tech Stack:** FastAPI, Pydantic 2, Python asyncio/MySQL integration tests, Vue 3, TypeScript, Node test runner, Docker.

## Global Constraints

- User-entered passwords, authorization codes, mailbox credentials, proxy passwords, and backup passwords must be non-empty with no FlyMail minimum or maximum length.
- Password strings must not be trimmed before hashing, encryption, comparison, or storage.
- `FLYMAIL_SESSION_SECRET` and other internal signing/encryption keys keep their existing minimum length requirements.
- `MYSQL_PASSWORD` remains non-empty and rejects newline characters.
- `/api/v2/threads` keeps the backend response `{ items, next_cursor }`; do not change the public API to match stale frontend code.
- Do not delete, migrate, or recreate `/Docker/flymail/data`.
- Release as `0.1.3`; build only the local image `benxianyu/flymail:0.1.3`; do not upload Docker Hub.

---

### Task 1: Prove and remove user-password length limits

**Files:**
- Modify: `backend/tests/v2/test_admin_bootstrap.py`
- Modify: `backend/tests/v2/test_api_auth_admin.py`
- Modify: `backend/tests/v2/test_api_backup_secure.py`
- Create: `backend/tests/v2/test_password_contracts.py`
- Modify: `backend/migrate.py`
- Modify: `backend/flymail/api/schemas/auth.py`
- Modify: `backend/flymail/api/schemas/accounts.py`
- Modify: `backend/flymail/api/schemas/backups.py`
- Modify: `backend/flymail/application/backups.py`
- Modify: `backend/routes/admin_users.py`
- Modify: `backend/routes/local_auth.py`

**Interfaces:**
- Consumes: existing `hash_password`, `verify_password`, Pydantic request models, and backup encryption implementation.
- Produces: all user password/credential request models accept any non-empty Python string without trimming or maximum-length checks.

- [ ] **Step 1: Change the first-admin test to require only non-empty passwords**

Update `test_empty_database_rejects_missing_or_weak_bootstrap_values` so empty password still raises, then create a fresh database administrator using password `"x"` and verify `verify_password("x", record.password_hash)`.

- [ ] **Step 2: Add a V2 authentication integration test for one-character passwords**

Add a test that:

```python
created = await admin_client.post(
    "/api/v2/admin/users",
    headers=headers,
    json={"username": "short-password-user", "password": "a", "role": "user", "enabled": True},
)
self.assertEqual(created.status_code, 201)
self.assertEqual((await self.login(user_client, "short-password-user", "a")).status_code, 200)
```

Then change that user's password to `" "` through `/api/v2/auth/password`, verify the exact space password logs in, reset it to `"b"` through the admin endpoint, and verify `"b"` logs in. Also assert empty strings return 422.

- [ ] **Step 3: Add schema tests for unbounded credentials**

Create `backend/tests/v2/test_password_contracts.py` with direct Pydantic tests using a string longer than 10,000 characters for:

```python
LoginRequest(password=value)
PasswordChangeRequest(current_password=value, new_password=value)
CreateUserRequest(password=value)
ResetPasswordRequest(new_password=value)
CreateAccountRequest(credential=value, ...)
UpdateCredentialRequest(credential=value)
SaveProxyRequest(username="proxy", password=value, ...)
BackupPasswordRequest(password=value)
```

Assert each preserves the exact input. Assert empty values fail only for fields that create or replace a password; `SaveProxyRequest.password=""` remains valid for a proxy without credentials.

- [ ] **Step 4: Add a backup integration test with a one-character password**

Use the existing secure-backup API fixture to create an encrypted archive with password `"x"`, inspect it with `"x"`, and assert an empty password receives 422.

- [ ] **Step 5: Run the new tests and verify they fail for the current length rules**

Run:

```bash
cd backend
python -m unittest \
  tests.v2.test_admin_bootstrap \
  tests.v2.test_password_contracts \
  tests.v2.test_api_auth_admin \
  tests.v2.test_api_backup_secure -v
```

Expected: failures mention minimum lengths, maximum lengths, password trimming, or `backup_password_too_short`.

- [ ] **Step 6: Implement the minimal backend policy change**

Apply these exact rules:

```python
password: str = Field(min_length=1, repr=False)
```

Use the equivalent field name for current/new password and credentials. Remove password-specific `max_length`. Remove the backup whitespace validator. In `bootstrap_initial_admin`, reject only `normalized_password == ""`. In backup application code, reject only `password == ""` with code `backup_password_required`. In legacy routes, stop calling `.strip()` on password values and reject only `== ""`.

- [ ] **Step 7: Run the focused backend tests and verify they pass**

Run the command from Step 5. Expected: PASS.

- [ ] **Step 8: Commit Task 1**

```bash
git add backend/migrate.py backend/flymail/api/schemas backend/flymail/application/backups.py backend/routes backend/tests/v2

git commit -m "🔓 统一用户密码为仅非空校验"
```

---

### Task 2: Fix the thread-list API/view-model boundary

**Files:**
- Modify: `frontend/src/shared/api/generated.ts`
- Modify: `frontend/src/features/threads/thread-query.ts`
- Modify: `frontend/src/features/threads/ThreadListPage.vue`
- Modify: `frontend/tests/v2/thread-list.test.ts`
- Modify: `frontend/tests/v2/full-workflow.test.ts`

**Interfaces:**
- Consumes: backend `ThreadListResponse(items, next_cursor)` and `ThreadListItem` fields.
- Produces: `normalizeThreadListItem(item: ThreadListItemResponse): ThreadProjection` and `ThreadCursorMemory.set()` that cache normalized view models.

- [ ] **Step 1: Add a failing test using the real backend response shape**

In `frontend/tests/v2/thread-list.test.ts`, add a raw helper with `latest_message_at`, `latest_snippet`, `participants_summary`, `pending_operation_count`, and assert:

```ts
const memory = new ThreadCursorMemory();
const page = memory.set('inbox', { items: [], next_cursor: null });
assert.deepEqual(page.threads, []);

const populated = memory.set('inbox', { items: [rawThread('t1')], next_cursor: 'next' });
assert.equal(populated.threads[0].latest_at, 123);
assert.equal(populated.threads[0].snippet, 'preview');
assert.equal(populated.threads[0].pending_state, 'pending');
```

- [ ] **Step 2: Run the thread-list test and verify the `items/threads` failure**

Run:

```bash
cd frontend
node --test tests/v2/thread-list.test.ts
```

Expected: FAIL because `ThreadListResponse` has no `items` and `ThreadCursorMemory.set` reads `response.threads`.

- [ ] **Step 3: Define the raw API type and normalizer**

Add `ThreadListItemResponse` with the exact backend fields. Change `ThreadListResponse` to:

```ts
export interface ThreadListResponse {
  items: ThreadListItemResponse[];
  next_cursor?: string | null;
}
```

Add:

```ts
export function normalizeThreadListItem(item: ThreadListItemResponse): ThreadProjection {
  return {
    id: item.id,
    subject: item.subject,
    snippet: item.latest_snippet,
    latest_at: item.latest_message_at,
    unread_count: item.unread_count,
    message_count: item.message_count,
    is_starred: item.is_starred,
    has_attachments: item.has_attachments,
    account_ids: [...item.account_ids],
    pending_state: item.pending_operation_count > 0 ? 'pending' : null,
  };
}
```

Change `ThreadCursorMemory.get/set` to accept/return the local cached page type with `threads`, while `set` consumes raw `ThreadListResponse.items`. Update controller and page annotations accordingly.

- [ ] **Step 4: Update existing workflow fixtures to use `{ items }`**

Replace synthetic thread-list fetch responses in `thread-list.test.ts` and `full-workflow.test.ts` with the backend shape. Do not add optional fallbacks for stale `{ threads }` responses.

- [ ] **Step 5: Run V2 frontend tests and verify the crash regression passes**

Run:

```bash
cd frontend
npm run test:v2
```

Expected: all V2 tests pass, including empty `items` and populated normalization.

- [ ] **Step 6: Commit Task 2**

```bash
git add frontend/src/shared/api/generated.ts frontend/src/features/threads frontend/tests/v2

git commit -m "🐛 修复线程列表响应字段错配导致页面崩溃"
```

---

### Task 3: Remove frontend password-length gates and synchronize documentation

**Files:**
- Modify: `frontend/src/features/admin/AdminPage.vue`
- Modify: `frontend/src/features/backup/BackupPage.vue`
- Modify: `frontend/tests/v2/settings-admin-backup.test.ts`
- Modify: `README.md`
- Modify: `.env.example`

**Interfaces:**
- Consumes: backend non-empty password policy from Task 1.
- Produces: UI forms submit any non-empty password and display no numeric password-length requirement.

- [ ] **Step 1: Add failing static UI assertions**

Extend `settings-admin-backup.test.ts` to assert Admin and Backup pages do not contain `minlength="12"`, `length < 12`, or `至少 12`, and do contain non-empty guards such as `!resetPassword` and `!password`.

- [ ] **Step 2: Run the focused frontend test and verify it fails**

Run:

```bash
cd frontend
node --test tests/v2/settings-admin-backup.test.ts
```

Expected: FAIL on current numeric-length attributes and conditions.

- [ ] **Step 3: Implement the minimal UI changes**

- Keep HTML `required` for create-user password.
- Change reset guard to `!resetPassword.value` and button condition to `!resetPassword`.
- Remove all backup `minlength` attributes and disable backup actions only when `!password`.
- Replace numeric hints with `密码不能为空` only where a hint is needed.

- [ ] **Step 4: Update deployment documentation**

Document user-entered passwords as non-empty with no FlyMail length limit. Explicitly retain the 16-character `FLYMAIL_SESSION_SECRET` requirement and MySQL newline prohibition. Update `.env.example` placeholders without embedding real secrets.

- [ ] **Step 5: Run frontend compatibility and V2 tests**

Run:

```bash
cd frontend
npm test
npm run test:v2
npm run build
```

Expected: all tests and bundle budgets pass.

- [ ] **Step 6: Commit Task 3**

```bash
git add frontend/src/features frontend/tests/v2 README.md .env.example

git commit -m "📝 同步无长度密码规则与前端表单"
```

---

### Task 4: Release, image verification, and production replacement

**Files:**
- Modify: `VERSION`
- Modify through `npm run sync-version`: `package.json`, `frontend/package.json`, `docker-compose.yml`, `README.md`
- Modify: `frontend/package-lock.json`
- Modify: `backend/tests/v2/fixtures/openapi-v2.json`
- Create: `docs/benchmarks/flymail-0.1.3-password-thread-fixes.md`

**Interfaces:**
- Consumes: completed Tasks 1-3.
- Produces: local image `benxianyu/flymail:0.1.3` and a healthy production `flymail` container using the existing data mount.

- [ ] **Step 1: Set version `0.1.3` and synchronize version files**

Run `npm run sync-version`, update the frontend lockfile root version fields, generate the OpenAPI fingerprint, and update the frozen fixture.

- [ ] **Step 2: Run complete verification**

Run:

```bash
cd backend && python -m unittest discover -s tests -v
cd ../frontend && npm install && npm test && npm run test:v2 && npm run build
cd ..
python -m compileall -q backend
bash -n scripts/docker-entrypoint.sh
bash -n scripts/test-v2-container.sh
bash -n scripts/check-v2-secrets.sh
docker compose config
git diff --check
```

- [ ] **Step 3: Build and verify the image**

```bash
docker build -t benxianyu/flymail:0.1.3 .
scripts/test-v2-container.sh benxianyu/flymail:0.1.3
scripts/check-v2-secrets.sh benxianyu/flymail:0.1.3
```

Additionally start an isolated empty-data container with one-character admin, MySQL, mailbox credential, proxy, and backup passwords where applicable. Verify root HTML, login, bootstrap, empty thread list, logout, schema 17, MySQL loopback binding, persistence restart, and safe shutdown.

- [ ] **Step 4: Record evidence and commit/push**

Document exact image ID, test counts, root-cause evidence, password boundary, and unresolved real-provider/browser limits. Commit with:

```bash
git commit -m "🚀 发布 FlyMail 0.1.3 密码与线程列表修复"
git push origin main
```

- [ ] **Step 5: Replace production with rollback protection**

Reuse `/Docker/flymail/data`, current environment values, port `36080:8080`, and restart policy `always`. Rename the healthy `0.1.2` container before starting `0.1.3`; automatically restore it if health, root HTML, login/bootstrap, empty thread list, or restart persistence fails.

- [ ] **Step 6: Final production verification**

Verify:

- `/api/health` reports `0.1.3`, schema 17, database/worker/object store OK.
- `/` returns HTML.
- Existing administrator can log in.
- `/api/v2/threads` returns `items` and the page remains usable when empty.
- Creating and authenticating a temporary one-character-password user succeeds, then remove that temporary user through the API.
- MySQL remains on `127.0.0.1:3306` and 3306 is not published.
- Logs and image metadata contain no secrets.
- `/Docker/flymail/data` remains the active mount and survives one restart.
