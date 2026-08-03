# Unified Inbox Toggle UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Polish the existing unified inbox setting so it remains visually consistent with FlyMail while using correct native control semantics, a 44px touch target, clear async feedback, and safe failure rollback.

**Architecture:** Keep the current per-user `/api/settings/unified` backend and global `flymail-unified-inbox-setting-changed` event. Change only the Settings page control markup, event handling, scoped CSS, and static regression test; do not add a shared component or dependency.

**Tech Stack:** Vue 3, TypeScript, scoped CSS, Node test runner, Vite, FastAPI, Docker.

## Global Constraints

- Work only in `/home/chatgpt/flymail` on branch `main`.
- Preserve `/Docker/flymail/data`; use independent temporary data for container verification.
- Do not alter the unified inbox API, database schema, authentication, mail data, or account selection behavior.
- Do not add or upgrade dependencies.
- Keep version `0.0.26`.
- Do not stage or commit existing `.benchmarks/*.json` files.
- Use existing semantic tokens, `--touch-target`, light/dark themes, and global reduced-motion handling.

---

### Task 1: Lock the accessible toggle contract

**Files:**
- Modify: `frontend/tests/unified-inbox-toggle.test.mjs`

**Interfaces:**
- Consumes: existing `Settings.vue` source contract.
- Produces: regression assertions requiring a native checkbox switch, labelled description, live async feedback, and a 44px hit area.

- [ ] **Step 1: Replace the old button-specific assertions**

Require `type="checkbox"`, `role="switch"`, `:checked="unifiedInboxEnabled"`, `aria-labelledby`, `aria-describedby`, `aria-live="polite"`, `min-height: var(--touch-target)`, and the absence of `aria-pressed`.

- [ ] **Step 2: Run the focused test and confirm RED**

Run `cd frontend && node --test tests/unified-inbox-toggle.test.mjs`.

Expected: FAIL because `Settings.vue` still uses a 28px button with duplicate switch/button ARIA semantics.

---

### Task 2: Implement the semantic visual switch

**Files:**
- Modify: `frontend/src/views/Settings.vue`

**Interfaces:**
- Consumes: `unifiedInboxEnabled`, `unifiedInboxSaving`, `unifiedInboxError`, and `/settings/unified`.
- Produces: `toggleUnifiedInbox(event: Event)` using the checkbox's requested state, optimistic UI, rollback on failure, and a live saving/error message.

- [ ] **Step 1: Replace the button markup**

Use a labelled native checkbox with `role="switch"`; keep the visual track and knob decorative. Link the control to the title, description, and feedback with IDs.

- [ ] **Step 2: Update the save handler**

Read `checked` from `event.currentTarget as HTMLInputElement`, optimistically update the ref, persist the requested value, dispatch the existing global event only after success, and restore the previous value on error.

- [ ] **Step 3: Refine scoped CSS**

Give the label a `min-height: var(--touch-target)`, make the native input cover the full hit area while remaining visually transparent, draw focus on the adjacent visual track, add hover/active feedback only where appropriate, and keep transitions limited to background, box-shadow, opacity, and transform.

- [ ] **Step 4: Run the focused test and confirm GREEN**

Run `cd frontend && node --test tests/unified-inbox-toggle.test.mjs`.

---

### Task 3: Verify the complete release and deploy locally

**Files:**
- Review: `README.md`
- No version changes.

**Interfaces:**
- Consumes: repository test/build scripts and Docker image definition.
- Produces: verified `benxianyu/flymail:0.0.26` and healthy `flymail` container without changing persistent data.

- [ ] **Step 1: Run frontend and backend tests**

Run `cd frontend && npm test && npm run build`, then `cd backend && python -m unittest discover -s tests -v`.

- [ ] **Step 2: Run repository checks**

Run `bash -n scripts/docker-entrypoint.sh`, `docker compose config`, `git diff --check`, and inspect `git diff`.

- [ ] **Step 3: Build and test a temporary container**

Build `benxianyu/flymail:0.0.26`; launch an isolated temporary container and data directory, verify health/version, MySQL 8.0 data path, database read/write and restart persistence, redacted logs, clean image metadata, and safe shutdown.

- [ ] **Step 4: Replace the current local container**

Recreate `flymail` from the verified local image while preserving `/Docker/flymail/data`, then verify `/api/health`.

- [ ] **Step 5: Commit and push only task files**

Stage `frontend/src/views/Settings.vue`, `frontend/tests/unified-inbox-toggle.test.mjs`, and this plan. Commit with `🎨 优化聚合收件箱开关交互` and push `origin/main`. Do not stage `.benchmarks/*.json`.
