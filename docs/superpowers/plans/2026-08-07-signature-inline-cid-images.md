# Signature Inline CID Images Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Persist signature images as FlyMail-managed assets and send their bytes inside every outgoing email as CID inline MIME images instead of receiver-visible FlyMail URLs.

**Architecture:** Signature HTML stores an internal `flymail-signature-image:<image_id>` reference plus `data-flymail-signature-image`, while the browser NodeView resolves that ID to a FlyMail preview URL only for editing. Before immediate send, scheduled send, draft APPEND, or sent-folder APPEND, a backend preparation service validates image ownership, rewrites managed images to unique `cid:` references, and returns inline image parts consumed by a shared MIME-body builder used by all seven SMTP providers.

**Tech Stack:** FastAPI, Python stdlib email/MIME, Pillow-backed signature storage, APScheduler, Vue 3, Pinia, Tiptap 3, Node test runner, Playwright, Docker/MySQL 8.0.

## Global Constraints

- Work only in `/home/chatgpt/flymail` on `main`.
- Do not add production dependencies.
- Do not change MySQL schema.
- Do not delete or migrate `/Docker/flymail/data`, existing messages, attachments, or signature image files.
- Do not fetch arbitrary remote image URLs server-side.
- Signature asset ownership must remain isolated by `user_uid`.
- Existing image resize behavior (`25% / 50% / 75% / 100%` and drag) must remain intact.
- Release target is `0.0.39`.

---

### Task 1: Persist internal signature image IDs and upload clipboard images

**Files:**
- Modify: `backend/services/signature_images.py`
- Modify: `backend/routes/signatures.py`
- Modify: `backend/schemas.py`
- Modify: `backend/tests/test_signature_images.py`
- Create: `frontend/src/utils/signature-image.ts`
- Modify: `frontend/src/components/TiptapEditor.vue`
- Modify: `frontend/src/utils/resizable-image-node-view.ts`
- Modify: `frontend/tests/signature-image-upload.test.mjs`
- Modify: `frontend/tests/resizable-image-node-view.test.mjs`

**Interfaces:**
- Produces backend `signature_image_reference(image_id: str) -> str` returning `flymail-signature-image:<image_id>`.
- Produces backend `parse_signature_image_id(src: str) -> str | None` accepting internal scheme and FlyMail `/api/signature-images/<image_id>` URLs.
- Produces backend `signature_image_belongs_to_user(user_uid: str, image_id: str) -> bool`.
- `SignatureImageUploadResponse` returns both `image_id` and compatibility `url`.
- Produces frontend `managedSignatureImageSource(imageId: string): string` and `parseManagedSignatureImageId(src: string): string | null`.
- Tiptap image nodes persist `signatureImageId` as `data-flymail-signature-image` and render the internal scheme; NodeView receives a preview resolver so editor display still uses `/api/signature-images/<id>`.

- [x] **Step 1: Write failing backend tests for internal image references**

Extend `backend/tests/test_signature_images.py` with tests equivalent to:

```python
self.assertEqual(
    signature_images.signature_image_reference("a" * 24 + "." + "b" * 32),
    "flymail-signature-image:" + "a" * 24 + "." + "b" * 32,
)
self.assertEqual(
    signature_images.parse_signature_image_id(
        "https://mail.example/api/signature-images/" + "a" * 24 + "." + "b" * 32
    ),
    "a" * 24 + "." + "b" * 32,
)
self.assertTrue(signature_images.signature_image_belongs_to_user("user-1", owned_id))
self.assertFalse(signature_images.signature_image_belongs_to_user("user-2", owned_id))
```

Also assert the upload response schema exposes `image_id`, and legacy promotion stores the internal scheme while preserving `width="367"`.

- [x] **Step 2: Run backend signature tests and confirm RED**

Run: `cd backend && python -m unittest tests.test_signature_images -v`

Expected: FAIL because the reference/parser/ownership APIs and response field do not yet exist.

- [x] **Step 3: Implement internal reference helpers and normalize existing stable URLs**

In `backend/services/signature_images.py`, reuse the existing image-id regex and user bucket helper. Parse both `flymail-signature-image:<id>` and any URL path ending in `/api/signature-images/<id>`. In `backend/routes/signatures.py`, make legacy attachment promotion write the internal source plus `data-flymail-signature-image`; while listing signatures, normalize 0.0.38 stable signature-image URLs owned by the current user to the same internal representation and persist only changed HTML. Keep the public GET route for backward compatibility with already-sent historical mail.

- [x] **Step 4: Write failing frontend upload/paste tests**

Extend `frontend/tests/signature-image-upload.test.mjs` to require:

```text
upload response uses data.image_id
stored src uses flymail-signature-image:
data-flymail-signature-image is rendered
editorProps.handlePaste inspects clipboardData.items and getAsFile()
pasted image uses the same upload function
```

Extend the NodeView contract to require a preview resolver rather than using the persisted internal scheme as the DOM `<img src>`.

- [x] **Step 5: Run frontend focused tests and confirm RED**

Run: `cd frontend && node --test tests/signature-image-upload.test.mjs tests/resizable-image-node-view.test.mjs`

Expected: FAIL because upload still inserts `data.url` and no paste handler/internal image ID exists.

- [x] **Step 6: Implement frontend internal images and clipboard upload**

Create `frontend/src/utils/signature-image.ts` with strict image-id validation. Refactor `TiptapEditor.vue` into a shared `uploadEditorImage(file, insertPos?)` path used by file input and paste. Add `signatureImageId` to `ResizableImage`; render it as `data-flymail-signature-image` and internal `src`, and have the NodeView call a preview resolver for the actual browser image URL. `handlePaste` handles only clipboard image files and otherwise returns `false` so normal text/HTML paste behavior is unchanged.

- [x] **Step 7: Verify Task 1**

Run:

```bash
cd backend && python -m unittest tests.test_signature_images -v
cd ../frontend && node --test tests/signature-image-upload.test.mjs tests/resizable-image-node-view.test.mjs tests/editor-image-size.test.ts
npm run build
```

Expected: all focused tests and typechecked production build pass.

- [x] **Step 8: Commit Task 1**

Commit title: `🖼️ 将签名图片保存为内部资产引用`

### Task 2: Build CID preparation and shared MIME inline parts

**Files:**
- Create: `backend/services/inline_images.py`
- Create: `backend/services/mime_parts.py`
- Create: `backend/tests/test_inline_images.py`
- Modify: `backend/tests/test_outgoing_mail.py`

**Interfaces:**
- Produces immutable `InlineImagePart(content_id: str, data: bytes, content_type: str, filename: str)` in `services.mime_parts`.
- Produces `build_alternative_body(body_html: str, body_text: str = "", inline_images: list[InlineImagePart] | None = None) -> MIMEMultipart`.
- Produces `inline_cids_to_data_uris(body_html: str, inline_images: list[InlineImagePart]) -> str` for FlyMail-only fallback cache display.
- Produces async `prepare_inline_images(user_uid: str, body_html: str) -> PreparedInlineBody`, where `PreparedInlineBody.body_html` uses `cid:` and `PreparedInlineBody.inline_images` contains bytes.

- [x] **Step 1: Write failing inline preparation tests**

Create tests that make a temporary owned signature image and assert:

```python
prepared = asyncio.run(prepare_inline_images("user-1", html))
self.assertNotIn("/api/signature-images/", prepared.body_html)
self.assertNotIn("flymail-signature-image:", prepared.body_html)
self.assertIn("src=\"cid:", prepared.body_html)
self.assertEqual(len(prepared.inline_images), 1)
self.assertEqual(prepared.inline_images[0].data, image_bytes)
```

Add cases for repeated references deduplicating to one MIME part, wrong-user IDs failing, missing managed files failing, and `data:image/png;base64,...` becoming an inline part.

- [x] **Step 2: Run inline tests and confirm RED**

Run: `cd backend && python -m unittest tests.test_inline_images -v`

Expected: FAIL because the services do not exist.

- [x] **Step 3: Implement preparation service**

Use stdlib regex/html/base64/uuid only. Never download arbitrary `http(s)` sources. For managed assets, verify `signature_image_belongs_to_user` before `resolve_signature_image`; cap decoded data-URI images at the existing 5 MB image limit. Remove `data-flymail-signature-image` from outgoing HTML and preserve attributes such as `width`.

- [x] **Step 4: Write failing MIME tests**

Extend `backend/tests/test_outgoing_mail.py` so a message with one `InlineImagePart` must parse as HTML `src="cid:..."` plus an `image/webp` part whose `Content-ID` matches and whose content disposition is `inline`; a normal file remains `attachment`.

- [x] **Step 5: Implement shared MIME body builder**

Build `multipart/alternative`; plain text is the first alternative when present. When inline images exist, the HTML alternative is `multipart/related` containing the HTML part followed by image parts with base64 payload, `Content-ID: <...>`, and `Content-Disposition: inline`. With no inline images, preserve the current direct HTML alternative.

- [x] **Step 6: Verify Task 2**

Run: `cd backend && python -m unittest tests.test_inline_images tests.test_outgoing_mail -v`

Expected: all tests pass.

- [x] **Step 7: Commit Task 2**

Commit title: `✉️ 构建签名图片 CID 内嵌邮件结构`

### Task 3: Wire every send, schedule, draft, and sent-cache path

**Files:**
- Modify: `backend/providers/base.py`
- Modify: `backend/providers/gmail/sender.py`
- Modify: `backend/providers/outlook/sender.py`
- Modify: `backend/providers/qq/sender.py`
- Modify: `backend/providers/netease/sender.py`
- Modify: `backend/providers/icloud/sender.py`
- Modify: `backend/providers/sina/sender.py`
- Modify: `backend/providers/custom/sender.py`
- Modify: `backend/routes/compose.py`
- Modify: `backend/services/scheduler.py`
- Modify: `backend/services/draft.py`
- Modify: `backend/services/outgoing_mail.py`
- Create: `backend/tests/test_sender_inline_images.py`
- Modify: `backend/tests/test_draft_message.py`
- Modify: `backend/tests/test_scheduler_drafts.py`
- Modify: `backend/tests/test_outgoing_mail.py`

**Interfaces:**
- `MailSender.send_message(..., inline_images: list | None = None)` adds an optional final keyword argument and preserves all existing callers.
- Every provider `_send_sync` calls `build_alternative_body(body_html, body_text, inline_images)`.
- `save_draft_to_imap(..., inline_images=None)` and `_build_draft_message(..., inline_images=None)` use the same MIME body builder.
- `build_outgoing_message_bytes(..., inline_images=None)` and `ensure_sent_message_cached(..., inline_images=None)` preserve CID images for sent-folder APPEND and local fallback.
- Scheduled jobs keep internal HTML only; `_send_scheduled_email` calls `prepare_inline_images(user_uid, body_html)` at execution time.

- [x] **Step 1: Write failing provider contract and draft/scheduler tests**

Create `test_sender_inline_images.py` to iterate all seven sender files and require the optional `inline_images` parameter plus shared `build_alternative_body` call. Extend draft tests to parse `_build_draft_message` with one inline part and require `Content-ID`/`inline`. Extend scheduler tests so the scheduled callback prepares inline images at execution rather than persisting image bytes in job kwargs.

- [x] **Step 2: Run targeted tests and confirm RED**

Run:

```bash
cd backend
python -m unittest tests.test_sender_inline_images tests.test_draft_message tests.test_scheduler_drafts tests.test_outgoing_mail -v
```

Expected: FAIL because sender/draft/scheduler interfaces do not accept inline images.

- [x] **Step 3: Update all SMTP senders**

Add optional `inline_images` to the base interface and each provider. Keep connection/authentication, headers, recipient calculation, normal attachment behavior, and `sendmail` unchanged. Replace only the per-provider alternative-body construction with the shared helper.

- [x] **Step 4: Wire immediate and legacy send routes**

In `compose_message`, after `prepare_outgoing_body_html`, call `await prepare_inline_images(user_uid, body_html)` for `send` and `draft`; for `schedule`, store the original internal HTML so the image is resolved at execution time. Pass prepared HTML and inline parts into sender, draft APPEND, and sent-cache functions. Apply the same preparation to `/api/messages/send` when HTML content contains managed images.

- [x] **Step 5: Wire scheduled send**

Inside `_send_scheduled_email`, resolve internal/data-URI images immediately before `sender.send_message`. Pass prepared HTML/inline parts to both the provider and `ensure_sent_message_cached`. Keep scheduler job kwargs limited to strings/lists/paths already persisted today.

- [x] **Step 6: Wire draft and sent-folder MIME**

Use `build_alternative_body` in draft/outgoing MIME builders. On local sent-cache fallback, call `inline_cids_to_data_uris` so FlyMail can render the cached copy even when IMAP APPEND fails; do not count inline signature images as ordinary attachments.

- [x] **Step 7: Run targeted and full backend suites**

Run:

```bash
cd backend
python -m unittest tests.test_sender_inline_images tests.test_draft_message tests.test_scheduler_drafts tests.test_outgoing_mail tests.test_inline_images -v
python -m unittest discover -s tests -v
```

Expected: targeted tests and full backend suite pass.

- [x] **Step 8: Commit Task 3**

Commit title: `📨 让所有发送路径携带签名内嵌图片`

### Task 4: Browser and raw-MIME regression

**Files:**
- Modify tests only if a real regression discovered requires a new automated case.
- Use `/tmp` for Playwright scripts and generated test images; do not add Playwright as a production dependency.

**Interfaces:**
- User-visible signature HTML stores internal image IDs.
- Browser preview still loads from FlyMail only inside the editor.
- Outgoing raw MIME uses CID and contains image bytes.

- [x] **Step 1: Run production-build browser preview**

Use the existing `webapp-testing` Playwright workflow with `frontend/dist`/Vite preview and mocked authenticated APIs. Upload a generated PNG, assert the editor DOM image loads, and assert the `v-model`/PUT signature payload contains `flymail-signature-image:` plus `data-flymail-signature-image` and does not contain an `http(s)` FlyMail image URL.

- [x] **Step 2: Test clipboard paste**

Set the clipboard/paste event with an image file, verify one `/api/signatures/images` upload occurs, and verify the pasted image becomes the same internal managed node. Confirm image resizing still updates persisted `width`.

- [x] **Step 3: Test backend raw MIME end-to-end**

In an isolated backend/container fixture, create an owned signature image and body HTML, prepare it, build outgoing MIME, parse the bytes, and assert:

```text
HTML contains cid:
HTML contains no /api/signature-images/
image part payload equals stored image bytes
Content-ID matches HTML
Content-Disposition is inline
normal attachment remains attachment
```

- [x] **Step 4: Re-run frontend full suite/build**

Run: `cd frontend && npm test && npm run build`

Expected: all tests and production build pass.

- [x] **Step 5: Commit only if browser-discovered source changes were required**

Commit title when needed: `🐛 修复签名 CID 图片浏览器交互边界`

### Task 5: Release 0.0.39, Docker validation, deploy, and push

**Files:**
- Modify: `README.md`
- Modify: `VERSION`
- Synchronize: `package.json`, `frontend/package.json`, `docker-compose.yml`
- Modify: `docs/superpowers/plans/2026-08-07-signature-inline-cid-images.md` to mark completed steps.

**Interfaces:**
- Produces local image `benxianyu/flymail:0.0.39`.
- Production container remains `flymail` with `/Docker/flymail/data:/data` and the same host port/runtime environment.

- [x] **Step 1: Update documentation**

Replace the README statement that receiver clients load public FlyMail signature-image URLs. Document internal signature asset IDs, clipboard upload, MIME CID sending, draft/schedule behavior, and the fact that the existing public image route remains only for editor preview/backward compatibility with already-sent historical messages.

- [x] **Step 2: Synchronize version `0.0.39`**

Write `0.0.39` to `VERSION`, run `npm run sync-version`, then verify `VERSION`, root package, frontend package, Compose image tag, and README image examples are all `0.0.39`.

- [x] **Step 3: Fresh code-level verification**

Run:

```bash
cd backend && python -m unittest discover -s tests -v
cd ../frontend && npm install && npm test && npm run build
cd ..
bash -n scripts/docker-entrypoint.sh
# validate Compose with .env.example via a /tmp sanitized copy; do not create/read project .env
git diff --check
git status --short
git diff
```

Expected: all tests/build/static checks pass; only known npm audit/chunk-size warnings may remain.

- [x] **Step 4: Build local Docker image**

Run: `docker build -t benxianyu/flymail:0.0.39 .`

- [x] **Step 5: Validate an isolated temporary container**

Use a unique temporary `/Docker/flymail/` data directory, fixed free port, and database password containing quote, backslash, `@`, `:`, `/`, and `%`. Verify health/version, MySQL 8.0 and `/data/mysql/`, DB read/write and restart persistence, signature upload/internal representation, raw CID MIME generation, draft MIME, logs redaction, image metadata, and MySQL `Shutdown complete`. Clean all temporary resources afterward.

- [x] **Step 6: Replace production with rollback protection**

Record current image, port, network, restart policy, mount, and a non-sensitive data fingerprint. Clone existing runtime environment without printing secrets, keep the old container stopped/renamed for rollback, start `0.0.39`, verify `healthy`, `/api/health`, MySQL, data fingerprint, `/Docker/flymail/data:/data`, existing signature images, and a second restart. Delete the rollback container only after all checks pass.

- [x] **Step 7: Final review, commit, and push**

Run fresh verification on the final tree, stage only this task's files, inspect staged diff and secret scan, commit title `🚀 发布签名 CID 内嵌图片 0.0.39`, fetch origin, ensure no remote divergence, then push `origin main` without force. Verify local and remote SHA match and workspace is clean.
