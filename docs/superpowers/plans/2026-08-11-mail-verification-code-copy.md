# Mail Verification Code Copy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect 4–8 digit verification codes from FlyMail message subjects or cached bodies and expose a one-click copy action in the mail list.

**Architecture:** Keep OTP extraction as a dependency-free pure backend service. List endpoints first inspect subjects and only bulk-read bounded cached body snippets for rows that still need detection, preserving user/account/folder scoping and avoiding full-body list payloads. The Vue mail list receives a single optional `verification_code` field and renders a fixed copy action without changing mail-open semantics.

**Tech Stack:** Python 3 / FastAPI / MySQL 8, Vue 3 / TypeScript / CSS, unittest, Node test runner, Vite, Docker.

## Global Constraints

- Work only in `/home/chatgpt/flymail` on `main`.
- Do not add or upgrade production dependencies.
- Do not add database columns or migrate/delete user mail data.
- Do not write test data into `/Docker/flymail/data`; temporary validation must use an isolated host directory.
- Never log verification codes, mailbox credentials, database passwords, tokens, or session secrets.
- Preserve per-user and per-account isolation on every body-source query.
- Follow `DESIGN.md`: fixed actions do not shrink, dynamic subject content owns shrinking, and the mail list must not introduce horizontal overflow.
- Do not upload Docker Hub unless explicitly requested.

---

### Task 1: Lock verification-code extraction behavior with failing backend tests

**Files:**
- Create: `backend/tests/test_verification_code.py`
- Create: `backend/services/verification_code.py`

**Interfaces:**
- Produces: `extract_verification_code(subject: str = '', body_text: str = '', body_html: str = '') -> str`.
- Returns the normalized 4–8 digit code or an empty string.

- [ ] **Step 1: Add failing tests for subject, text, HTML, grouped codes, and false positives**

Tests must cover:

```python
self.assertEqual(extract(subject="24681357 是您的验证码"), "24681357")
self.assertEqual(extract(subject="登录验证", body_text="您的验证码是 123-456"), "123456")
self.assertEqual(extract(body_text="Your verification code is 654321."), "654321")
self.assertEqual(extract(body_html="<p>Security code: <strong>778899</strong></p>"), "778899")
self.assertEqual(extract(subject="订单 123456 已发货"), "")
self.assertEqual(extract(body_text="日期 2026-08-11，订单号 123456"), "")
self.assertEqual(extract(body_text="订单号 123456，您的验证码是 654321"), "654321")
```

The test loader must convert a missing service module into an assertion failure so the first RED run is an actual failed test rather than an import crash.

- [ ] **Step 2: Run the focused backend test and verify RED**

Run: `cd backend && python -m unittest tests.test_verification_code -v`
Expected before implementation: FAIL because the verification-code service does not yet exist.

- [ ] **Step 3: Implement the minimal dependency-free extractor**

Create `backend/services/verification_code.py` with:

```python
def extract_verification_code(subject: str = "", body_text: str = "", body_html: str = "") -> str:
    ...
```

Implementation requirements:
- HTML is converted to visible text using Python standard library parsing.
- Candidate digits are 4–8 digits, optionally grouped by one space or hyphen, and normalized before returning.
- A candidate is accepted only when a positive verification keyword is within a bounded neighborhood.
- Nearest positive context wins; subject wins over body sources.
- Date-like spans and negative order/invoice/tracking/phone/amount/promo contexts are suppressed.

- [ ] **Step 4: Re-run the focused backend test and verify GREEN**

Run: `cd backend && python -m unittest tests.test_verification_code -v`
Expected: all verification-code parser tests pass.

### Task 2: Add scoped cached-body lookup and list decoration

**Files:**
- Modify: `backend/db/__init__.py`
- Modify: `backend/routes/messages.py`
- Modify: `backend/schemas.py`
- Modify: `backend/tests/test_message_search_db.py`
- Modify: `backend/tests/test_message_folder_resolution.py` or add focused route tests if required by the existing fixture pattern.

**Interfaces:**
- Produces: `get_cached_verification_sources(user_uid: str, account_id: str, folder: str, uids: list[int]) -> dict[int, dict[str, str]]`.
- Produces an async route helper that decorates message dicts with `verification_code` without returning the body snippets used for detection.

- [ ] **Step 1: Add a failing DB-scope test**

The test must assert the lookup SQL includes all of:

```text
user_uid = ?
account_id = ?
folder IN (...)
uid IN (...)
LEFT(COALESCE(body_text, ''), ...)
LEFT(COALESCE(body_html, ''), ...)
```

and maps rows by UID without exposing unrelated account data.

- [ ] **Step 2: Run the DB test and verify RED**

Run: `cd backend && python -m unittest tests.test_message_search_db.MessageSearchDbTests.test_verification_source_lookup_is_scoped_and_bounded -v`
Expected: FAIL because the lookup function is missing.

- [ ] **Step 3: Implement the scoped bulk lookup**

Add one bounded MySQL query for unique positive UIDs. Reuse `_expand_folder_aliases(folder)` and return only `body_text` / `body_html` snippets keyed by UID.

- [ ] **Step 4: Add failing route/list decoration tests**

Cover these behaviors:
- subject-only code is attached without requiring a body hit;
- body-only code is attached from the scoped lookup;
- no-code messages get `verification_code == ''`;
- `_message_to_item()` applies the same extractor to remote fallback data.

- [ ] **Step 5: Verify RED, implement minimal route decoration, then verify GREEN**

The route helper must batch unresolved UIDs, never log code values, and run for normal cached message lists, search results, conversation lists, and refresh/local-result paths that pass through `_load_local_messages`. Remote fallback rows use `_message_to_item()`.

Run the focused route/database tests until all pass.

- [ ] **Step 6: Extend the response schema**

Add:

```python
verification_code: str = Field(default="", description="识别到的验证码")
```

to `MessageItem`.

### Task 3: Add the one-click copy action with frontend contract tests

**Files:**
- Create: `frontend/tests/mail-verification-code.test.mjs`
- Modify: `frontend/src/types/mail.ts`
- Modify: `frontend/src/views/MailList.vue`

**Interfaces:**
- Consumes: `Message.verification_code?: string`.
- Produces: a `.verification-code-copy` button that copies without opening the mail row.

- [ ] **Step 1: Add failing frontend source-contract tests**

Assert that:

```js
assert.match(mailTypeSource, /verification_code\?: string/)
assert.match(mailListSource, /class="verification-code-copy"/)
assert.match(mailListSource, /@click\.stop="copyVerificationCode\(msg\.verification_code\)"/)
assert.match(mailListSource, /验证码已复制/)
assert.match(mailListSource, /\.verification-code-copy\s*\{[^}]*flex-shrink:\s*0;/s)
```

Also assert the row is no longer an outer `<button class="mail-item">`, preventing invalid nested buttons.

- [ ] **Step 2: Run the focused frontend test and verify RED**

Run: `cd frontend && node --test tests/mail-verification-code.test.mjs`
Expected: FAIL because the type, button, and copy handler are absent.

- [ ] **Step 3: Implement the frontend behavior**

Add `verification_code?: string` to `Message`.

Change the mail row to a keyboard-operable `div role="button" tabindex="0"`, preserving click, Enter/Space, right-click select, hover prefetch, unread, and selection behavior.

Insert an actual inner button between `.mail-info` and `.mail-status-tag`:

```vue
<button
  v-if="msg.verification_code && !selectMode"
  class="verification-code-copy"
  type="button"
  :aria-label="`复制验证码 ${msg.verification_code}`"
  @click.stop="copyVerificationCode(msg.verification_code)"
>
  复制验证码
</button>
```

The copy handler prefers `navigator.clipboard.writeText`, falls back to a temporary textarea and `document.execCommand('copy')`, and only shows `uiStore.success('验证码已复制')`.

- [ ] **Step 4: Add responsive styles**

Desktop: fixed action stays between subject and status/date, `flex-shrink: 0`, compact token-based border/background/text.

Mobile: update the row grid so the copy action stays in the second row beside the subject, while the subject remains `minmax(0, 1fr)` and `.list-items` keeps `overflow-x: hidden`.

- [ ] **Step 5: Run focused frontend tests and verify GREEN**

Run: `cd frontend && node --test tests/mail-verification-code.test.mjs`
Expected: all focused tests pass.

### Task 4: Documentation, version, and regression verification

**Files:**
- Modify: `README.md`
- Modify: `DESIGN.md` only if the fixed-action rule needs a concrete mail-list example.
- Modify: `VERSION`
- Modify: `package.json`
- Modify: `frontend/package.json`
- Modify: `docker-compose.yml`

**Interfaces:**
- Consumes: current version `0.0.48`.
- Produces: release `0.0.49`.

- [ ] **Step 1: Document the behavior and capability boundary**

README must state that FlyMail locally recognizes common 4–8 digit verification codes from subject/cached body and offers one-click copy; recognition is heuristic and only cached body content participates.

- [ ] **Step 2: Bump VERSION to `0.0.49` and synchronize**

Set `VERSION` to `0.0.49`, run `npm run sync-version`, and verify root package, frontend package, Compose image tag, and README image tag all match.

- [ ] **Step 3: Run complete source verification**

Run:

```bash
cd backend && python -m unittest discover -s tests -v
cd frontend && npm install && npm test && npm run build
bash -n scripts/docker-entrypoint.sh
docker compose config --quiet
git diff --check
git status --short
git diff
```

Expected: all tests/build/config checks pass with no secret-bearing output.

### Task 5: Build, isolated Docker validation, production replacement, and Git delivery

**Files:** None beyond verified task files.

**Interfaces:**
- Consumes: verified source and image `benxianyu/flymail:0.0.49`.
- Produces: healthy local `flymail` container running `0.0.49` with existing `/Docker/flymail/data:/data` untouched.

- [ ] **Step 1: Build the local image**

Run: `docker build -t benxianyu/flymail:0.0.49 .`
Expected: successful build; no Docker Hub push.

- [ ] **Step 2: Run isolated temporary-container validation**

Use a unique temporary host data directory and container name. Use a generated session secret and a MySQL password containing quotes/backslash/`@`/`:`/`/`/`%`. Verify:
- container reaches `healthy`;
- `/api/health` returns version `0.0.49`;
- MySQL reports 8.0 and `@@datadir` is `/data/mysql/`;
- `/data/flymail` exists;
- a temporary DB record survives container restart;
- logs redact database passwords;
- image metadata contains no real password/session secret;
- SIGTERM stops MySQL cleanly.

Clean up only the temporary container/data after validation.

- [ ] **Step 3: Safely rebuild the current `flymail` container**

Preserve current host port, environment, restart policy, and `/Docker/flymail/data:/data`. Do not delete or initialize the host data directory. After replacement, verify Docker health, `/api/health`, MySQL 8.0 datadir, and existing database readability.

- [ ] **Step 4: Read verification-before-completion, review final diffs, commit, and push**

Run pre-commit checks:

```bash
git status --short
git diff --check
git diff
git add <only task files>
git diff --staged
```

Commit with a specific Chinese title such as:

```text
✨ 新增邮件验证码识别与一键复制
```

Push `origin/main` (443 SSH fallback if necessary) and verify local `main` matches remote.
