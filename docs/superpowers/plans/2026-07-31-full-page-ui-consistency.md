# FlyMail Full Page UI Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every authenticated top-level page use consistent content widths, panel surfaces, toolbar treatment, rounded boundaries, loading states, and empty states.

**Architecture:** Keep the existing four `PageFrame` templates and strengthen their shared CSS contracts instead of rewriting page business logic. Extend `UiEmptyState`, add `UiLoadingState`, and replace page-specific state markup in management, split, workspace, and document pages.

**Tech Stack:** Vue 3, TypeScript, scoped CSS, Node test runner, Vite, Docker.

## Global Constraints

- Keep existing FastAPI and Vue 3 architecture.
- Do not add or upgrade production dependencies.
- Do not change APIs, authentication, permissions, mailbox behavior, or persistent data.
- Desktop management content maximum width is 1280px; document content maximum width remains 960px.
- Split pages use a rounded desktop panel and edge-to-edge mobile layout.
- `/Docker/flymail/data` must not be modified by tests.

---

### Task 1: Lock Shared Page Surface Contracts

**Files:**
- Modify: `frontend/tests/page-templates.test.mjs`
- Modify: `frontend/src/styles/tokens.css`
- Modify: `frontend/src/styles/layout-system.css`

**Interfaces:**
- Produces: `--page-content-max`, management alignment rules, inset toolbar panel, split panel surface.

- [ ] Add failing assertions that management headers, toolbars, and stacks share `--page-content-max`.
- [ ] Add failing assertions that `.page-frame__toolbar` has an inset width, border, radius, surface, and shadow.
- [ ] Add failing assertions that split template body has border, radius, surface, shadow, and mobile reset.
- [ ] Run `npm test -- --test-name-pattern="management pages share|split pages use"` and confirm failure.
- [ ] Add `--page-content-max: 1280px` and `--page-document-max: 960px` tokens.
- [ ] Implement the shared alignment and surface rules in `layout-system.css`.
- [ ] Re-run the targeted tests and confirm success.

### Task 2: Standardize Loading and Empty-State Components

**Files:**
- Modify: `frontend/src/components/ui/UiEmptyState.vue`
- Create: `frontend/src/components/ui/UiLoadingState.vue`
- Modify: `frontend/src/styles/layout-system.css`
- Modify: `frontend/tests/page-templates.test.mjs`

**Interfaces:**
- Produces: `compact?: boolean`, `panel?: boolean` props; empty/loading compact and panel modifier classes; reduced-motion-safe spinner.

- [ ] Add failing assertions for the empty-state props and modifier classes.
- [ ] Add failing assertions for the loading component, spinner, and modifier props.
- [ ] Run the targeted tests and confirm failure.
- [ ] Add the props and modifier class bindings to `UiEmptyState`.
- [ ] Create `UiLoadingState` with accessible status markup and a shared spinner.
- [ ] Add compact sizing, panel surfaces, and reduced-motion CSS.
- [ ] Re-run the targeted tests and confirm success.

### Task 3: Convert Visible Page Loading and Empty States

**Files:**
- Modify: `frontend/src/views/AccountList.vue`
- Modify: `frontend/src/views/ContactList.vue`
- Modify: `frontend/src/views/HistorySync.vue`
- Modify: `frontend/src/views/UnifiedInbox.vue`
- Modify: `frontend/src/views/Backup.vue`
- Modify: `frontend/src/views/NotificationSettings.vue`
- Modify: `frontend/tests/page-templates.test.mjs`

**Interfaces:**
- Consumes: `UiEmptyState` and `UiLoadingState` compact/panel variants from Task 2.

- [ ] Add failing source-contract tests requiring data pages to use `UiEmptyState` and asynchronous pages to use `UiLoadingState`.
- [ ] Run the targeted tests and confirm failure.
- [ ] Replace page-specific loading/empty markup while preserving conditions, actions, and wording.
- [ ] Remove only the now-unused local state CSS and imports.
- [ ] Re-run the targeted tests and confirm success.

### Task 4: Audit Remaining Top-Level Surfaces

**Files:**
- Modify: `frontend/src/views/AccountList.vue`
- Modify: `frontend/src/views/UserManagement.vue`
- Modify: `frontend/src/views/ContactList.vue`
- Modify: `frontend/tests/page-templates.test.mjs`

**Interfaces:**
- Produces: shared account toolbar/card and contact split visual behavior without business changes.

- [ ] Add failing assertions that account/user toolbars use `PageToolbar` and that contact split inherits the shared panel boundary.
- [ ] Run the targeted tests and confirm failure.
- [ ] Remove page-specific full-width/background behavior that conflicts with shared templates.
- [ ] Keep contact sidebar/detail backgrounds and internal divider subordinate to the shared split outer panel.
- [ ] Re-run the targeted tests and confirm success.

### Task 5: Version, Documentation, and Full Verification

**Files:**
- Modify: `README.md`
- Modify: `VERSION`
- Generated by sync: `package.json`, `frontend/package.json`, `docker-compose.yml`

**Interfaces:**
- Produces: release version `0.0.21` and documented page consistency behavior.

- [ ] Update README UI capability text.
- [ ] Set `VERSION` to `0.0.21` and run `npm run sync-version`.
- [ ] Run `cd frontend && npm test`.
- [ ] Run `cd frontend && npm run build`.
- [ ] Run backend tests with the built image Python and the workspace mounted read-only, because the host virtual environment may not contain production dependencies.
- [ ] Run `bash -n scripts/docker-entrypoint.sh`, `docker compose --env-file .env.example config -q`, and `git diff --check`.
- [ ] Build `docker build -t benxianyu/flymail:0.0.21 .`.
- [ ] Start an isolated temporary container and verify health, MySQL 8.0, `/data/mysql/`, database read/write, restart persistence, redacted logs, clean image metadata, and graceful shutdown.
- [ ] Replace the current `flymail` container using the existing `/Docker/flymail/data:/data` mount, verify health and restart persistence, then remove the old container.
- [ ] Review README and `.env.example`; confirm no environment variable changes.
- [ ] Stage only task files, commit with `🎨 统一全部页面容器与空状态样式`, and push `origin/main`.
