# Signature Image Rehydration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make legacy and newly uploaded signature images display immediately after save/reselection without page refresh, while preserving image sizing and existing mail data.

**Architecture:** The backend performs a one-time compatibility promotion for legacy FlyMail mail-attachment image URLs when signatures are listed: owned, locally cached images are copied into the stable signature-image store and the signature HTML is rewritten to the public signature-image URL. The frontend treats persisted image width as authoritative during initial NodeView rendering and remounts the editor whenever a saved signature is explicitly rehydrated.

**Tech Stack:** FastAPI, aiomysql/MySQL, Pillow-backed signature image storage, Vue 3, Pinia, Tiptap 3, Node test runner, Playwright, Docker.

## Global Constraints

- Work only in `/home/chatgpt/flymail` on `main`.
- Never delete or migrate `/Docker/flymail/data`; legacy mail attachments remain untouched.
- No new production dependencies.
- Only promote legacy FlyMail attachment URLs that resolve to the current user's owned account and an existing local cached image.
- If a legacy image cannot be safely resolved, preserve the original HTML unchanged.
- Release target is `0.0.38`.

---

### Task 1: Promote legacy mail-attachment signature images

**Files:**
- Modify: `backend/services/signature_images.py`
- Modify: `backend/routes/signatures.py`
- Modify: `backend/tests/test_signature_images.py`

**Interfaces:**
- Produces: `parse_legacy_attachment_image_url(src: str) -> LegacyAttachmentImageRef | None`.
- Produces: an async route helper that receives the current `user_uid`, `Request`, and signature HTML and returns rewritten HTML plus whether it changed.
- Consumes existing `get_account_by_id`, `get_cached_attachment`, `resolve_cached_attachment_path`, `save_signature_image`, and `update_signature`.

- [x] **Step 1: Write failing parser and promotion tests**

Add tests proving an absolute or relative FlyMail URL like `/api/messages/4942/attachments/4?account_id=a1&folder=INBOX` parses into account, UID, part and folder, while unrelated URLs and malformed IDs return `None`. Add an async route test with mocked ownership/cache functions asserting successful promotion replaces only the `src` URL, keeps `width="367"`, and calls signature-image storage without deleting the source attachment.

- [x] **Step 2: Run tests and confirm RED**

Run: `cd backend && python -m unittest tests.test_signature_images -v`

Expected: FAIL because the legacy parser/promotion helper is absent.

- [x] **Step 3: Implement minimal compatibility promotion**

Use `urllib.parse.urlparse/parse_qs` and a strict regex for `/api/messages/{message_id}/attachments/{part}`. Resolve the account and verify `account.user_uid == user_uid`; read only `get_cached_attachment(..., touch=False)` and `resolve_cached_attachment_path(..., touch=False)`. Copy bytes through `save_signature_image`, generate the new URL with `request.url_for('get_signature_image', image_id=...)`, replace the matching `src` in the HTML, and persist only if at least one image changed.

- [x] **Step 4: Run focused tests and full backend suite**

Run: `cd backend && python -m unittest tests.test_signature_images -v && python -m unittest discover -s tests -v`

Expected: focused and full backend tests pass.

- [x] **Step 5: Commit**

Commit title: `🖼️ 迁移旧签名附件图片到稳定存储`

### Task 2: Fix Tiptap image rehydration after save/reselection

**Files:**
- Modify: `frontend/src/utils/editor-image-size.ts`
- Modify: `frontend/src/utils/resizable-image-node-view.ts`
- Modify: `frontend/src/views/SignatureManagement.vue`
- Modify: `frontend/tests/editor-image-size.test.ts`
- Modify: `frontend/tests/resizable-image-node-view.test.mjs`
- Modify: `frontend/tests/signature-management-page.test.mjs`

**Interfaces:**
- Produces: `imageWidthForInitialRender(width: number, containerWidth: number) -> number`, where unavailable container width does not shrink a persisted width.
- Signature management owns an `editorRevision` key and increments it after successful save and explicit selection/reselection.
- Initial NodeView synchronization must not access `editor.view` before Tiptap mounts the EditorView.

- [x] **Step 1: Write failing tests**

Add a pure test: `imageWidthForInitialRender(367, 0) === 367`, `imageWidthForInitialRender(367, 1) === 367`, and `imageWidthForInitialRender(367, 300) === 300`. Extend the signature-management contract to require `:key` on `TiptapEditor`, an editor revision, and rehydration when the current signature is clicked. Add a NodeView contract test proving initial synchronization never reads `editor.view`.

- [x] **Step 2: Run tests and confirm RED**

Run: `cd frontend && node --test tests/editor-image-size.test.ts tests/resizable-image-node-view.test.mjs tests/signature-management-page.test.mjs`

Expected: FAIL for the missing initial-render helper/revision behavior and the pre-mount `editor.view` access.

- [x] **Step 3: Implement minimal frontend fix**

Use the new helper only from `syncImageAttributes()` with the already-mounted node container width; initial construction must not read `editor.view`. Active drag and quick-size operations continue using strict `clampImageWidth`. Add `editorRevision`, bind it to the editor key, increment after `saveDraft()` and whenever `requestSelect()` intentionally reloads a signature. Clicking the already-selected signature calls `beginEdit(id)` when safe instead of returning without rehydrating.

- [x] **Step 4: Run frontend tests/build**

Run: `cd frontend && npm test && npm run build`

Expected: all tests and production build pass.

- [x] **Step 5: Browser regression**

With Playwright, verify: a persisted `width=367` image loads; quick size updates remain functional; save retains the image; clicking the current signature remounts the editor; switching away and back displays the image without refresh; no `The editor view is not available` error appears.

- [x] **Step 6: Commit**

Commit title: `🐛 修复签名图片保存后需刷新才能显示`

### Task 3: Release 0.0.38 and deploy

**Files:**
- Modify: `README.md`
- Modify: `VERSION`
- Synchronize through `npm run sync-version`: `package.json`, `frontend/package.json`, `docker-compose.yml`
- Modify: this plan to mark completed steps.

**Interfaces:**
- Produces local image `benxianyu/flymail:0.0.38` and replaces the current `flymail` container with rollback protection.

- [x] **Step 1: Update README**

Document that legacy FlyMail mail-attachment images are copied into stable signature-image storage when safely resolvable, and that old mail/attachment data is retained.

- [x] **Step 2: Set and synchronize `0.0.38`**

Set `VERSION` to `0.0.38`, run `npm run sync-version`, and verify root/frontend/compose/README versions match.

- [x] **Step 3: Final code verification**

Run backend full tests, `npm install`, frontend full tests/build, `bash -n scripts/docker-entrypoint.sh`, safe Compose config using `.env.example`, `git diff --check`, `git status --short`, and full diff review.

- [x] **Step 4: Build image**

Run: `docker build -t benxianyu/flymail:0.0.38 .`

- [x] **Step 5: Validate isolated temporary container**

Use a unique `/Docker/flymail/` temporary data path, fixed free port, and database password containing quote, backslash, `@`, `:`, `/`, `%`. Verify health/version, MySQL 8.0 `/data/mysql/`, DB read/write and restart persistence, signature image upload/download, legacy promotion behavior, secret redaction, image metadata, and MySQL safe shutdown. Remove temporary resources afterward.

- [x] **Step 6: Replace production with rollback protection**

Capture runtime configuration without printing secret values, keep `/Docker/flymail/data:/data`, record the data fingerprint, replace `flymail` with `0.0.38`, verify health/image/MySQL/data fingerprint, migrate the existing legacy signature image without deleting its attachment, run a real production browser rehydration check, and verify a second restart. Delete the rollback container only after every check passes.

- [x] **Step 7: Final commit and push**

Commit title: `🚀 发布签名图片兼容修复 0.0.38`. Fetch origin, confirm no remote divergence, push `main`, then verify local and remote SHA match and workspace is clean.
