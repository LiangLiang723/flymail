# Mail Search and Conversations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade FlyMail search with fast composable local filters and add RFC-aware conversation grouping without risking existing persisted mail data.

**Architecture:** Search remains MySQL-cache-first and is extended with structured filters plus lightweight operator parsing. Conversation identity is derived when caching messages from RFC `References` / `In-Reply-To`, with conservative subject fallback scoped to the same user/account; list and detail endpoints read precomputed thread keys.

**Tech Stack:** FastAPI, Pydantic, aiomysql/MySQL 8.0, Vue 3, TypeScript, Vite, Node test runner, Python unittest, Docker.

## Global Constraints

- Work only in `/home/chatgpt/flymail` on `main`.
- Do not add or upgrade production dependencies.
- Do not delete or migrate `/Docker/flymail/data` or `/data/mysql`.
- Every DB query must preserve `user_uid` and account isolation.
- Search must not trigger a full remote mailbox scan.
- Existing single-message delete/move/read behavior remains unchanged.
- `VERSION` remains the version source of truth and is synchronized before release.

---

### Task 1: Search query parsing and filter contract

**Files:**
- Create: `backend/services/message_search.py`
- Create: `backend/tests/test_message_search.py`
- Modify: `backend/routes/messages.py`

**Interfaces:**
- Produces: `ParsedMessageSearch` and `parse_message_search(query: str)` with free text, from/to/subject, after/before, read state, attachment and starred fields.
- Consumes: normal query text from `/api/messages/search` and structured query parameters.

- [ ] Write failing unit tests for `from:`, `to:`, `subject:`, quoted values, `after:`, `before:`, `has:attachment`, `is:unread`, `is:read`, `is:starred`, and remaining free text.
- [ ] Run `cd backend && python -m unittest tests.test_message_search -v` and confirm failure because the parser does not exist.
- [ ] Implement the smallest pure parser with no external dependency; invalid dates stay in free text rather than crashing the request.
- [ ] Re-run the focused test and confirm PASS.

### Task 2: RFC thread identity and persistence

**Files:**
- Create: `backend/services/message_threads.py`
- Create: `backend/tests/test_message_threads.py`
- Modify: `backend/providers/base.py`
- Modify: `backend/providers/base_imap.py`
- Modify: `backend/models/__init__.py`
- Modify: `backend/db/__init__.py`
- Modify: `backend/services/outgoing_mail.py`

**Interfaces:**
- Produces: `normalize_message_id`, `normalize_subject_for_thread`, `build_thread_key(account_id, message_id, in_reply_to, references, subject)`.
- Adds `in_reply_to`, `references_header`, and `thread_key` to provider/cache message objects.

- [ ] Write failing tests proving References root wins, In-Reply-To is second choice, Message-ID is used for standalone mail, and normalized subject fallback is account-scoped.
- [ ] Run `cd backend && python -m unittest tests.test_message_threads -v` and confirm expected failure.
- [ ] Implement pure thread helpers.
- [ ] Extend IMAP header/detail parsing and outgoing local-cache parsing to capture thread headers.
- [ ] Extend `cached_messages` schema with additive columns and indexes; extend upsert/select mapping without destructive migration.
- [ ] Re-run focused thread tests and relevant existing outgoing/cache tests.

### Task 3: Structured local search and conversation queries

**Files:**
- Create: `backend/tests/test_message_search_db.py`
- Modify: `backend/db/__init__.py`
- Modify: `backend/schemas.py`
- Modify: `backend/routes/messages.py`

**Interfaces:**
- Extends `search_cached_messages_by_folder(...)` to accept structured filters while preserving existing keyword behavior.
- Produces `get_message_conversations(...)` and `get_conversation_messages(...)` scoped by user/account/folder.
- Adds `/api/messages/conversations` and `/api/messages/conversation?thread_key=...`.

- [ ] Write failing DB/query builder tests for sender, recipient including Cc, subject, body, date range, read state, attachment, starred and user/account isolation.
- [ ] Write failing route/service tests for conversation count, unread count, latest-message ordering and detail chronological ordering.
- [ ] Run focused tests and confirm expected failures.
- [ ] Implement safe SQL condition construction with bound parameters only.
- [ ] Implement conversation aggregation from precomputed/lazily backfilled `thread_key` values with no cross-account grouping.
- [ ] Re-run focused tests and existing message-folder isolation tests.

### Task 4: Reusable search UI and mail-list conversation mode

**Files:**
- Create: `frontend/src/components/mail/MailSearchBar.vue`
- Create: `frontend/src/utils/mail-search.ts`
- Create: `frontend/tests/mail-search.test.ts`
- Modify: `frontend/src/types/mail.ts`
- Modify: `frontend/src/views/MailList.vue`

**Interfaces:**
- `MailSearchBar` emits a normalized search state with keyword, from, to, subject, date range, read, attachment and starred flags.
- `serializeMailSearchParams(state)` returns API query parameters.
- MailList adds `listMode: 'messages' | 'conversations'` and requests the matching endpoint.

- [ ] Write failing frontend tests for search parameter serialization, clearing state, and omission of empty filters.
- [ ] Run `cd frontend && npm test -- --test-name-pattern='mail search'` (or the project test command if pattern forwarding is unsupported) and confirm failure.
- [ ] Implement the pure serializer, then the search component using existing UI tokens/classes.
- [ ] Replace the old search input in MailList with the component; keep quick unread/read/attachment filters and add starred.
- [ ] Add message/conversation segmented toggle, conversation count badges, unread count, latest sender/date, and click-through to chronological conversation detail.
- [ ] Re-run focused frontend tests and `npm run build`.

### Task 5: Unified inbox search

**Files:**
- Modify: `backend/db/__init__.py`
- Modify: `backend/routes/messages.py`
- Modify: `frontend/src/views/UnifiedInbox.vue`
- Modify: `frontend/tests/unified-inbox-page.test.mjs` or create a focused equivalent if none exists.

**Interfaces:**
- `/api/messages/unified` accepts `keyword` and applies it only to the configured current-user account set.

- [ ] Write a failing test showing unified keyword search cannot return a message from an unselected or foreign account.
- [ ] Run the focused backend/frontend test and confirm expected failure.
- [ ] Add bounded local keyword search to unified inbox and a search input to the existing filter card.
- [ ] Re-run focused tests.

### Task 6: Documentation, version, full verification and deployment validation

**Files:**
- Modify: `README.md`
- Modify: `VERSION`
- Modify via `npm run sync-version`: `package.json`, `frontend/package.json`, `docker-compose.yml`, README image references.

**Interfaces:**
- Final release version: next patch after `0.0.41`.

- [ ] Update README with search operators, advanced filters, conversation behavior and compatibility notes.
- [ ] Set `VERSION` to `0.0.42`, run `npm run sync-version`, and verify root/frontend/docker/README versions match.
- [ ] Run `cd backend && python -m unittest discover -s tests -v`.
- [ ] Run `cd frontend && npm install && npm test && npm run build`.
- [ ] Run `bash -n scripts/docker-entrypoint.sh`, `docker compose config`, `git diff --check`, `git status --short`, and inspect `git diff`.
- [ ] Build `docker build -t benxianyu/flymail:0.0.42 .`.
- [ ] Start a temporary container with an isolated temporary host data directory and unique container/port; verify healthy `/api/health`, MySQL 8.0 and `/data/mysql/`, `/data/flymail`, DB read/write, restart persistence, redacted logs, image metadata secret absence, and clean MySQL shutdown.
- [ ] Remove only temporary container/data.
- [ ] Re-check README and `.env.example` for synchronization needs.
- [ ] Stage only task files, inspect staged diff, commit with a specific Chinese title, and push `origin/main`; do not push Docker Hub.
