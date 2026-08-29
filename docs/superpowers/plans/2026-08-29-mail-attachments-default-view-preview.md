# Mail Attachments, Default View, and Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move received-mail attachments above the body, persist a per-user default mail/conversation list mode, and preview common safe attachment types without adding production dependencies.

**Architecture:** Reuse `/api/settings` and the existing `user_settings` table for `default_mail_view`. `MailList.vue` loads that preference before its first list request and keeps Drafts in message mode. Attachment preview reuses the existing authenticated download route, fetches the attachment as a Blob, and renders only browser-native image/PDF/text/audio/video types through a dedicated modal component using object URLs.

**Tech Stack:** Vue 3 + TypeScript + Node test runner, FastAPI + Pydantic + unittest, existing Axios API client.

**Spec:** Current user request in this conversation on 2026-08-29; bounded change with no separate spec file.

## Global Constraints

- Do not add or upgrade production dependencies.
- Do not modify or delete `/Docker/flymail/data`.
- Preserve user/mail isolation and existing attachment cache behavior.
- Draft folders must remain in message mode even when the default is conversation mode.
- Preview only browser-native common types; unsupported Office/archive/binary types remain download-only.
- Keep all UI inside existing FlyMail design tokens and scroll ownership rules.

---

### Task 1: Persist the default list mode per user

**Files:**
- Modify: `backend/schemas.py`
- Modify: `backend/routes/settings.py`
- Modify: `backend/tests/test_attachment_cache_routes.py`

**Interfaces:**
- Consumes: existing `get_user_settings(uid, keys)` / `set_user_settings(uid, values)`.
- Produces: `GET /api/settings -> default_mail_view: "messages" | "conversations"`; `PUT /api/settings` accepts the same field.

- [ ] **Step 1: Write failing backend tests** for default `messages`, stored `conversations`, valid schema values, invalid schema values, and per-user save.
- [ ] **Step 2: Run the focused backend test** and confirm failure is caused by the missing field/behavior.
- [ ] **Step 3: Add the Pydantic response/request field** constrained to `messages|conversations`.
- [ ] **Step 4: Read/write `default_mail_view` through `user_settings`** while leaving global OAuth settings untouched.
- [ ] **Step 5: Run the focused backend test again** and confirm it passes.

### Task 2: Expose and apply the default list mode in the frontend

**Files:**
- Modify: `frontend/src/views/Settings.vue`
- Modify: `frontend/src/views/MailList.vue`
- Create/modify test: `frontend/tests/mail-default-view.test.mjs`

**Interfaces:**
- Settings uses `PUT /settings` with `{ default_mail_view }` and announces changes with `flymail-default-mail-view-changed`.
- Mail list loads `/settings` before first message request, applies the saved mode, and falls back to `messages` on error or Drafts.

- [ ] **Step 1: Write a failing frontend contract test** covering the settings control, persisted field, first-load preference, and Drafts fallback.
- [ ] **Step 2: Run the focused frontend test** and confirm it fails because the setting does not exist.
- [ ] **Step 3: Add the settings card and save feedback** using the existing card/button/token language.
- [ ] **Step 4: Load/apply the preference in MailList before first fetch** and listen for the setting-change event.
- [ ] **Step 5: Run the focused frontend test** and confirm it passes.

### Task 3: Move attachments above the body and add common previews

**Files:**
- Modify: `frontend/src/views/MailList.vue`
- Create: `frontend/src/utils/attachment-preview.ts`
- Create: `frontend/src/components/mail/AttachmentPreview.vue`
- Modify/create test: `frontend/tests/mail-attachment-preview.test.mjs`

**Interfaces:**
- `getAttachmentPreviewKind(attachment)` returns `image|pdf|text|audio|video|null`.
- `AttachmentPreview.vue` accepts `{ open, title, kind, url }` and emits `close`.
- `MailList.vue` fetches the existing attachment route as `blob`, creates an object URL, opens the preview modal, and revokes the URL on close/unmount.

- [ ] **Step 1: Write failing frontend tests** proving the attachment block appears before body content and that preview types/actions/modal plumbing exist.
- [ ] **Step 2: Run the focused frontend test** and confirm failure is for missing preview/top placement.
- [ ] **Step 3: Implement preview type detection** with MIME first and conservative filename fallbacks; exclude HTML/SVG/Office/archive executables.
- [ ] **Step 4: Implement the modal component** with native `<img>`, `<iframe>` for PDF/text blob URLs, `<audio>`, and `<video>`, keyboard Escape, close button, and no new dependency.
- [ ] **Step 5: Move the attachment list between header and message body** and add Preview before Download/NAS only when supported.
- [ ] **Step 6: Run focused tests** and confirm they pass.

### Task 4: Documentation, version, and full verification

**Files:**
- Modify: `README.md`
- Modify: `VERSION`
- Generated by `npm run sync-version`: `package.json`, `frontend/package.json`, `docker-compose.yml`, `README.md`

**Interfaces:**
- `VERSION` remains the single version source.

- [ ] **Step 1: Update README user-visible capabilities** for default mail view and attachment previews.
- [ ] **Step 2: Bump patch version from 0.0.60 to 0.0.61 and run `npm run sync-version`.**
- [ ] **Step 3: Run frontend focused tests, full frontend tests/build, and full backend unittest suite.**
- [ ] **Step 4: Run `bash -n scripts/docker-entrypoint.sh`, `docker compose config`, `git diff --check`, and inspect the final diff/status.**
- [ ] **Step 5: Build `benxianyu/flymail:0.0.61`.**
- [ ] **Step 6: Start an isolated temporary container/data directory and verify health/version, MySQL 8.0 + `/data/mysql`, `/data/flymail`, DB read/write, restart persistence, redacted logs, no secret-bearing image metadata, and graceful MySQL shutdown.**
- [ ] **Step 7: Rebuild/recreate the project `flymail` container against `/Docker/flymail/data` only after temporary verification passes; do not delete or migrate data.**
- [ ] **Step 8: Commit only this task's files with a compliant Chinese commit title and push `main` to `origin`; do not upload Docker Hub.**
