# Account Icon Customization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let every FlyMail account use its provider default icon, a controlled built-in preset, or a user-uploaded image cropped to `256×256 WebP`, with consistent display, user isolation, persistence, and safe cleanup.

**Architecture:** Extend the existing `accounts` row with icon mode and preset ID, while storing uploaded pixels deterministically under `/data/flymail/files/account-icons/<user_uid>/<account_id>.webp`. Add ownership-checked icon APIs, a shared Vue account-icon component, and a dependency-free crop dialog using Pointer Events and Canvas. Update Pinia and every account-identity surface immediately after changes. The final task releases both this plan and `2026-07-31-mail-body-theme-contrast.md` as FlyMail `0.0.24`.

**Tech Stack:** FastAPI, Pydantic, MySQL 8.0, Pillow, Vue 3, TypeScript, Pinia, Pointer Events, Canvas, Node test runner, Python unittest, Docker.

## Global Constraints

- Work only in `/home/chatgpt/flymail` on branch `main`.
- Keep current authentication, multi-user isolation, account connection behavior, IMAP/SMTP credentials, and `/data` persistence model.
- Do not add or upgrade production dependencies; use existing Pillow and browser APIs.
- Do not store original uploads, absolute host paths, SVG uploads, GIF animation, or external image URLs.
- Accepted upload formats are JPEG, PNG and WebP; maximum input is `10 MB`; maximum decoded pixels are `40,000,000`.
- Server output is always `256×256 WebP` after EXIF orientation correction and server-side normalization.
- Uploaded files live only under `/data/flymail/files/account-icons/<user_uid>/<account_id>.webp`.
- Cross-user read or write attempts return `404`.
- Unknown preset IDs and missing upload files fall back to the provider default icon.
- Switching to a preset or default deletes any no-longer-used uploaded file only after the database update succeeds.
- Failed upload normalization must leave the previously active icon intact.
- The crop UI must reuse existing dialog, button, input, spacing, focus, reduced-motion, dark-theme, light-theme and mobile conventions.
- Final release target is `0.0.24`.
- Do not modify or delete `/Docker/flymail/data`; temporary Docker validation uses an isolated directory and container name.

---

### Task 1: Add Account Icon Storage and Database Contracts

**Files:**
- Modify: `backend/data_paths.py`
- Modify: `backend/models/__init__.py`
- Modify: `backend/db/__init__.py`
- Modify: `backend/schemas.py`
- Create: `backend/services/account_icons.py`
- Create: `backend/tests/test_account_icons.py`

**Interfaces:**
- Produces: `ACCOUNT_ICONS_DIR`.
- Produces: `Account.icon_type: str = 'default'` and `Account.icon_value: str = ''`.
- Produces: `ACCOUNT_ICON_PRESET_IDS: frozenset[str]`.
- Produces: `save_account_icon(user_uid: str, account_id: str, data: bytes) -> Path`.
- Produces: `resolve_account_icon(user_uid: str, account_id: str) -> Path | None`.
- Produces: `delete_account_icon(user_uid: str, account_id: str) -> None`.
- Produces: `update_account_icon(account_id, user_uid, icon_type, icon_value='') -> bool`.
- Extends: `AccountInfo` with `icon_type`, `icon_value`, `icon_url`.

- [ ] **Step 1: Write failing storage and normalization tests**

Create `backend/tests/test_account_icons.py` with temporary-directory patches and representative Pillow images:

```python
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image


class AccountIconStorageTest(unittest.TestCase):
    def _image_bytes(self, fmt="PNG", size=(600, 300)):
        buffer = io.BytesIO()
        Image.new("RGB", size, (20, 120, 220)).save(buffer, format=fmt)
        return buffer.getvalue()

    def test_upload_is_normalized_to_256_webp(self):
        from services import account_icons
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            account_icons, "ACCOUNT_ICONS_DIR", Path(temp_dir)
        ):
            target = account_icons.save_account_icon("user-1", "account-1", self._image_bytes())
            self.assertEqual(target, Path(temp_dir) / "user-1" / "account-1.webp")
            with Image.open(target) as result:
                self.assertEqual(result.size, (256, 256))
                self.assertEqual(result.format, "WEBP")

    def test_invalid_or_unsupported_images_are_rejected(self):
        from services import account_icons
        with self.assertRaisesRegex(ValueError, "仅支持 JPG、PNG 或 WebP 图片"):
            account_icons.save_account_icon("user-1", "account-1", b"GIF89a")
        with self.assertRaisesRegex(ValueError, "无法读取该图片"):
            account_icons.save_account_icon("user-1", "account-1", b"not-an-image")

    def test_failed_replacement_keeps_existing_file(self):
        from services import account_icons
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            account_icons, "ACCOUNT_ICONS_DIR", Path(temp_dir)
        ):
            target = account_icons.save_account_icon("user-1", "account-1", self._image_bytes())
            before = target.read_bytes()
            with self.assertRaises(ValueError):
                account_icons.save_account_icon("user-1", "account-1", b"broken")
            self.assertEqual(target.read_bytes(), before)
```

Add a migration test using a fake DB cursor that records SQL and verifies both `icon_type` and `icon_value` are added idempotently and NULL values are normalized.

- [ ] **Step 2: Run the backend test and confirm RED**

```bash
cd backend
python -m unittest tests.test_account_icons -v
```

Expected: FAIL because storage and DB interfaces do not exist. If the host lacks dependencies, use the current FlyMail image with the repository mounted read-only, as in the existing release workflow.

- [ ] **Step 3: Add data paths and model fields**

In `data_paths.py`:

```python
ACCOUNT_ICONS_DIR = FILES_DIR / "account-icons"
```

Include it in `ensure_data_dirs()`.

In `models/__init__.py`:

```python
icon_type: str = "default"
icon_value: str = ""
```

Add the columns to the initial `CREATE TABLE accounts` statement and to the migration loop:

```python
for column, declaration in (
    ("hide_email", "INTEGER DEFAULT 0"),
    ("poll_interval_seconds", "INTEGER DEFAULT 10"),
    ("icon_type", "VARCHAR(32) NOT NULL DEFAULT 'default'"),
    ("icon_value", "VARCHAR(255) NOT NULL DEFAULT ''"),
):
    try:
        await db.execute(f"ALTER TABLE accounts ADD COLUMN {column} {declaration}")
    except Exception as exc:
        logger.debug("migration add accounts.%s ignored: %s", column, exc)
await db.execute("UPDATE accounts SET icon_type = 'default' WHERE icon_type IS NULL OR icon_type = ''")
await db.execute("UPDATE accounts SET icon_value = '' WHERE icon_value IS NULL")
```

Add:

```python
async def update_account_icon(account_id: str, user_uid: str, icon_type: str, icon_value: str = "") -> bool:
    db = await get_db()
    cursor = await db.execute(
        """UPDATE accounts
           SET icon_type = ?, icon_value = ?, updated_at = ?
           WHERE id = ? AND user_uid = ?""",
        (icon_type, icon_value, time.time(), account_id, user_uid),
    )
    await db.commit()
    return cursor.rowcount > 0
```

The SQL must include both `id` and `user_uid`, update `updated_at`, and accept only values already validated by the route/service layer.

- [ ] **Step 4: Implement secure file normalization**

Create `backend/services/account_icons.py` with:

```python
MAX_ACCOUNT_ICON_BYTES = 10 * 1024 * 1024
MAX_ACCOUNT_ICON_PIXELS = 40_000_000
ACCOUNT_ICON_SIZE = 256
ACCOUNT_ICON_PRESET_IDS = frozenset({
    "mail-purple", "mail-blue", "mail-green", "work",
    "personal", "school", "team", "star",
})
```

Requirements:

- Validate `user_uid` and `account_id` with `^[A-Za-z0-9_.-]+$`.
- Read dimensions before full decode and reject `width * height > 40_000_000`.
- Accept only Pillow formats `JPEG`, `PNG`, `WEBP`; reject GIF even if Pillow can decode its first frame.
- Apply `ImageOps.exif_transpose()`.
- Convert alpha images to RGBA and others to RGB.
- Use `ImageOps.fit(normalized, (ACCOUNT_ICON_SIZE, ACCOUNT_ICON_SIZE), method=Image.Resampling.LANCZOS)`.
- Save to a unique temporary file in the target directory, then call `os.replace()`.
- Delete the temporary file on every failure.
- `resolve_account_icon()` must verify the resolved path remains under the expected user directory and has `.webp` suffix.
- `delete_account_icon()` removes the file and then removes an empty user directory, ignoring only safe `OSError` cleanup failures.

- [ ] **Step 5: Extend response schemas**

Add these three fields to the existing `AccountInfo` class in `schemas.py`, after `poll_interval_seconds` and before `created_at`:

```python
icon_type: str = Field(default="default", description="账号图标模式")
icon_value: str = Field(default="", description="内置图标 ID")
icon_url: str = Field(default="", description="上传图标的受保护地址")
```

Then add:

```python
class AccountIconPresetRequest(BaseModel):
    preset_id: str = Field(min_length=1, max_length=64)

class AccountIconResponse(BaseModel):
    success: bool = True
    icon_type: str
    icon_value: str = ""
    icon_url: str = ""
```

- [ ] **Step 6: Run tests and commit**

```bash
cd backend
python -m unittest tests.test_account_icons -v
cd ..
git add backend/data_paths.py backend/models/__init__.py backend/db/__init__.py backend/schemas.py backend/services/account_icons.py backend/tests/test_account_icons.py
git diff --staged
git commit -m "✨ 新增邮箱账号图标存储与数据模型"
```

---

### Task 2: Add Ownership-Checked Account Icon APIs

**Files:**
- Modify: `backend/routes/accounts.py`
- Modify: `backend/tests/test_account_icons.py`
- Modify: `backend/tests/test_account_update.py`

**Interfaces:**
- Consumes: account icon service and DB methods from Task 1.
- Produces: `PUT /api/accounts/{account_id}/icon/preset`.
- Produces: `POST /api/accounts/{account_id}/icon/upload`.
- Produces: `DELETE /api/accounts/{account_id}/icon`.
- Produces: `GET /api/accounts/{account_id}/icon`.
- Produces: safe account-list icon fields.

- [ ] **Step 1: Write failing route tests**

Add isolated async tests that patch `get_accounts`, `update_account_icon`, file functions, and `get_uid`:

```python
async def test_preset_update_rejects_other_users_account(self):
    with patch.object(accounts, "get_uid", AsyncMock(return_value="user-1")), patch.object(
        accounts, "get_accounts", AsyncMock(return_value=[])
    ):
        with self.assertRaises(AppError) as raised:
            await accounts.set_account_icon_preset("account-2", request=object(), body=AccountIconPresetRequest(preset_id="work"))
    self.assertEqual(raised.exception.status_code, 404)

async def test_account_list_never_returns_absolute_icon_path(self):
    account = Account(
        id="account-1",
        user_uid="user-1",
        email="a@example.com",
        provider="custom",
        icon_type="upload",
        icon_value="",
        updated_at=1234,
    )
    with patch.object(accounts, "get_uid", AsyncMock(return_value="user-1")), patch.object(
        accounts, "get_accounts", AsyncMock(return_value=[account])
    ), patch.object(accounts, "resolve_account_icon", return_value=Path("/tmp/account-1.webp")), patch.object(
        Path, "is_file", return_value=True
    ):
        payload = await accounts.list_accounts(request=object())
    self.assertEqual(payload["accounts"][0]["icon_url"], "/api/accounts/account-1/icon?v=1234")
    self.assertNotIn("/data/", repr(payload))
```

Cover valid preset, invalid preset, upload success, upload failure preserving existing state, restore default, owned GET, cross-user GET and missing-file fallback.

- [ ] **Step 2: Run the route tests and confirm RED**

```bash
cd backend
python -m unittest tests.test_account_icons -v
```

Expected: new route tests FAIL.

- [ ] **Step 3: Add shared account serialization**

In `routes/accounts.py`, add:

```python
def _account_icon_url(account: Account) -> str:
    if account.icon_type != "upload":
        return ""
    path = resolve_account_icon(account.user_uid, account.id)
    if not path or not path.is_file():
        return ""
    return f"/api/accounts/{account.id}/icon?v={int(account.updated_at or 0)}"


def _safe_account_payload(account: Account) -> dict:
    icon_type = account.icon_type if account.icon_type in {"default", "preset", "upload"} else "default"
    icon_value = account.icon_value if icon_type == "preset" and account.icon_value in ACCOUNT_ICON_PRESET_IDS else ""
    if icon_type == "preset" and not icon_value:
        icon_type = "default"
    if icon_type == "upload" and not _account_icon_url(account):
        icon_type = "default"
    return {
        "id": account.id,
        "email": account.email,
        "provider": account.provider,
        "status": account.status,
        "remark": account.remark,
        "group_name": account.group_name,
        "hide_email": account.hide_email,
        "sort_order": account.sort_order,
        "poll_interval_seconds": account.poll_interval_seconds,
        "created_at": account.created_at,
        "reauth_needed": account.id in sync_service.reauth_account_ids,
        "icon_type": icon_type,
        "icon_value": icon_value,
        "icon_url": _account_icon_url(account) if icon_type == "upload" else "",
    }
```

Use this helper for `GET /api/accounts` and account icon mutation responses. Do not include `credentials_json`, absolute paths, or another user’s fields.

- [ ] **Step 4: Implement endpoints**

Add route imports for `File`, `UploadFile`, `FileResponse`, icon schemas and icon service methods.

Preset endpoint order:

1. Load current user’s accounts and find exact `account_id`; return 404 if absent.
2. Validate `preset_id` against `ACCOUNT_ICON_PRESET_IDS`.
3. Update DB to `preset`.
4. Delete old upload only after DB success.
5. Reload/patch the account and return `AccountIconResponse`.

Upload endpoint order:

1. Verify ownership before reading file bytes.
2. Read at most `MAX_ACCOUNT_ICON_BYTES + 1`.
3. Normalize to a temporary/replacement WebP.
4. Update DB to `upload` only after file save succeeds.
5. If DB update fails, remove the newly written file only when there was no previous upload; otherwise preserve/restore the old file using a service-level backup path.
6. Return the versioned URL.

To make step 5 atomic, the service must expose these exact interfaces:

- `stage_account_icon(user_uid: str, account_id: str, data: bytes) -> StagedAccountIcon`
- `commit_staged_account_icon(staged: StagedAccountIcon) -> Path`
- `rollback_staged_account_icon(staged: StagedAccountIcon) -> None`

The data object is:

```python
@dataclass
class StagedAccountIcon:
    target: Path
    temporary: Path
    previous: Path | None
```

`stage_account_icon()` validates and writes only `temporary`; `commit_staged_account_icon()` atomically moves the old target to `previous`, replaces the target with `temporary`, then removes `previous`; `rollback_staged_account_icon()` removes `temporary` and restores `previous` when present. The route stages the file, updates DB, then commits the file. On DB failure it rolls back. On commit failure it restores DB to the previous icon mode before returning an error. Tests must exercise both failure boundaries.

Restore endpoint updates DB to `default`, then deletes old upload. GET verifies ownership and returns `FileResponse(path, media_type="image/webp", headers={"Cache-Control": "private, max-age=86400"})`.

- [ ] **Step 5: Verify route tests and existing account update tests**

```bash
cd backend
python -m unittest tests.test_account_icons tests.test_account_update -v
```

Expected: all tests PASS; existing account edit behavior remains unchanged.

- [ ] **Step 6: Commit the APIs**

```bash
git add backend/routes/accounts.py backend/services/account_icons.py backend/tests/test_account_icons.py backend/tests/test_account_update.py
git diff --staged
git commit -m "🔒 新增邮箱图标隔离上传与切换接口"
```

---

### Task 3: Clean Account Icons During Account Deletion

**Files:**
- Modify: `backend/services/history_sync.py`
- Modify: `backend/tests/test_history_sync_folders.py` or create `backend/tests/test_account_delete_icon.py`

**Interfaces:**
- Consumes: `delete_account_icon(user_uid, account_id)`.
- Preserves: existing account deletion job, attachment cleanup and mail cache cleanup order.

- [ ] **Step 1: Write the failing cleanup test**

Create a focused test around `run_delete_account()` that patches cache cleanup, account deletion and `delete_account_icon`, then asserts:

```python
delete_account_icon.assert_called_once_with("user-1", "account-1")
delete_account.assert_awaited_once_with("account-1", "user-1")
```

Also verify icon cleanup failure is logged but does not leave the account row undeleted after all core mail cleanup succeeds.

- [ ] **Step 2: Run and confirm RED**

```bash
cd backend
python -m unittest tests.test_account_delete_icon -v
```

- [ ] **Step 3: Add cleanup at the account-deletion boundary**

Call `delete_account_icon(account.user_uid, account.id)` inside `run_delete_account()` immediately before the final database account delete, after account-owned mail/cache cleanup. Catch only safe file cleanup errors and log account ID without credentials or file content.

- [ ] **Step 4: Verify and commit**

```bash
cd backend
python -m unittest tests.test_account_delete_icon tests.test_attachment_cache_routes -v
cd ..
git add backend/services/history_sync.py backend/tests/test_account_delete_icon.py
git diff --staged
git commit -m "🧹 清理已删除邮箱的自定义图标"
```

---

### Task 4: Build the Preset Registry and Shared Account Icon Component

**Files:**
- Create: `frontend/src/types/account.ts`
- Create: `frontend/src/utils/account-icon-presets.ts`
- Create: `frontend/src/components/account/AccountIcon.vue`
- Create: `frontend/tests/account-icon.test.ts`
- Modify: `frontend/src/utils/provider.ts`

**Interfaces:**
- Produces: `type AccountIconType = 'default' | 'preset' | 'upload'`.
- Produces: `interface MailAccount` containing existing account fields plus `icon_type`, `icon_value`, `icon_url`.
- Produces: `ACCOUNT_ICON_PRESETS`, `isAccountIconPreset(id)`, `accountIconPresetSvg(id)`.
- Produces: `<AccountIcon :account size="sm|md|lg" />`.
- Consumes: existing `providerIcon(provider)` only for provider-default fallback.

- [ ] **Step 1: Write failing registry and component contracts**

Create tests:

```ts
import test from 'node:test';
import assert from 'node:assert/strict';
import { ACCOUNT_ICON_PRESETS, isAccountIconPreset } from '../src/utils/account-icon-presets.ts';

test('preset IDs are stable and unknown values are rejected', () => {
  assert.deepEqual(ACCOUNT_ICON_PRESETS.map((item) => item.id), [
    'mail-purple', 'mail-blue', 'mail-green', 'work',
    'personal', 'school', 'team', 'star',
  ]);
  assert.equal(isAccountIconPreset('work'), true);
  assert.equal(isAccountIconPreset('missing'), false);
});
```

Add a source contract asserting `AccountIcon.vue` checks upload URL first, valid preset second and provider default last, and includes image-error fallback and `aria-label`.

- [ ] **Step 2: Run and confirm RED**

```bash
cd frontend
npm test -- --test-name-pattern="preset IDs|account icon component"
```

- [ ] **Step 3: Implement the typed registry**

Store controlled SVG strings in the registry; no user-provided SVG enters `v-html`. Export labels for the selector:

```ts
export const ACCOUNT_ICON_PRESETS = [
  { id: 'mail-purple', label: '紫色邮件', svg: MAIL_PURPLE_SVG },
  { id: 'mail-blue', label: '蓝色邮件', svg: MAIL_BLUE_SVG },
  { id: 'mail-green', label: '绿色邮件', svg: MAIL_GREEN_SVG },
  { id: 'work', label: '工作', svg: WORK_SVG },
  { id: 'personal', label: '个人', svg: PERSONAL_SVG },
  { id: 'school', label: '学校', svg: SCHOOL_SVG },
  { id: 'team', label: '团队', svg: TEAM_SVG },
  { id: 'star', label: '星标', svg: STAR_SVG },
] as const;
```

Use existing `AppIcon` paths where practical, but keep preset SVGs stable and independent of provider defaults.

- [ ] **Step 4: Implement `AccountIcon.vue`**

Rules:

- `upload` + nonempty `icon_url`: render `<img>`.
- On image error, set local failure state and render provider default.
- `preset` + valid ID: render controlled preset SVG.
- All other states: render `providerIcon(account.provider)`.
- Size classes: `sm=20`, `md=32`, `lg=48` pixels.
- Use one shared rounded-square shell, object-fit cover, theme-safe border and background.
- `aria-label` is `${account.remark || account.email} 的邮箱图标`; decorative contexts may pass `decorative` to use empty alt.

- [ ] **Step 5: Run tests/build and commit**

```bash
cd frontend
npm test -- --test-name-pattern="preset IDs|account icon component"
npm run build
cd ..
git add frontend/src/types/account.ts frontend/src/utils/account-icon-presets.ts frontend/src/components/account/AccountIcon.vue frontend/src/utils/provider.ts frontend/tests/account-icon.test.ts
git diff --staged
git commit -m "✨ 新增邮箱图标预设与统一展示组件"
```

---

### Task 5: Build the Dependency-Free Crop Engine and Dialog

**Files:**
- Create: `frontend/src/utils/account-icon-crop.ts`
- Create: `frontend/src/components/account/AccountIconCropDialog.vue`
- Create: `frontend/tests/account-icon-crop.test.ts`
- Modify: `frontend/src/styles/components.css`

**Interfaces:**
- Produces: `type CropState = { scale: number; offsetX: number; offsetY: number }`.
- Produces: `coverScale(imageWidth, imageHeight, viewportSize): number`.
- Produces: `clampCropState(state, imageWidth, imageHeight, viewportSize): CropState`.
- Produces: `pinchScale(startScale, startDistance, currentDistance): number`.
- Produces: `renderAccountIconBlob(image, state, viewportSize): Promise<Blob>`.
- Produces component events: `confirm(blob: Blob)` and `close()`.

- [ ] **Step 1: Write failing crop-math tests**

```ts
import test from 'node:test';
import assert from 'node:assert/strict';
import { clampCropState, coverScale, pinchScale } from '../src/utils/account-icon-crop.ts';

test('cover scale fills a square crop viewport', () => {
  assert.equal(coverScale(600, 300, 320), 320 / 300);
  assert.equal(coverScale(300, 600, 320), 320 / 300);
});

test('crop offsets never expose empty canvas', () => {
  const clamped = clampCropState({ scale: 2, offsetX: 999, offsetY: -999 }, 400, 300, 320);
  assert.ok(Number.isFinite(clamped.offsetX));
  assert.ok(Number.isFinite(clamped.offsetY));
  assert.ok(Math.abs(clamped.offsetX) <= (400 * 2 - 320) / 2);
  assert.ok(Math.abs(clamped.offsetY) <= (300 * 2 - 320) / 2);
});

test('pinch zoom is bounded by the crop limits', () => {
  assert.equal(pinchScale(1, 100, 200), 2);
  assert.equal(pinchScale(4, 100, 300), 5);
});
```

- [ ] **Step 2: Run and confirm RED**

```bash
cd frontend
npm test -- --test-name-pattern="cover scale|crop offsets|pinch zoom"
```

- [ ] **Step 3: Implement crop math and Canvas output**

Use `MIN_CROP_SCALE` as the calculated cover scale and `MAX_CROP_SCALE = coverScale * 5`. Clamp offsets so every viewport pixel maps inside the image. `renderAccountIconBlob()` creates a `256×256` canvas, applies the inverse transform, draws the visible crop, and serializes with:

```ts
const blob = await new Promise<Blob | null>((resolve) => {
  canvas.toBlob(resolve, 'image/webp', 0.9);
});
if (!blob) throw new Error('无法生成裁剪图片');
return blob;
```

Reject with `无法生成裁剪图片` if Canvas returns null.

- [ ] **Step 4: Implement the dialog**

`AccountIconCropDialog.vue` must:

- Accept an object URL and decoded natural dimensions.
- Start centered at cover scale.
- Use Pointer Events with `setPointerCapture()` for mouse, pen and touch.
- One pointer pans; two pointers calculate distance and midpoint for pinch zoom.
- Wheel zoom uses `preventDefault()` only over the crop viewport.
- Slider exposes the same min/max scale.
- Show 32px and 48px previews using the same transform, not a separate crop.
- Revoke object URLs in the parent after close.
- Trap focus within the modal using the project’s current dialog pattern; Escape closes unless upload is in progress.
- Use shared `.dialog-overlay`, `.dialog`, `.ui-button`, spacing and tokens; add only crop-specific geometry to `components.css`.
- Respect `prefers-reduced-motion` by disabling nonessential transform transitions.

- [ ] **Step 5: Verify and commit**

```bash
cd frontend
npm test -- --test-name-pattern="cover scale|crop offsets|pinch zoom|crop dialog"
npm run build
cd ..
git add frontend/src/utils/account-icon-crop.ts frontend/src/components/account/AccountIconCropDialog.vue frontend/src/styles/components.css frontend/tests/account-icon-crop.test.ts
git diff --staged
git commit -m "✨ 新增邮箱图标裁剪与移动端手势"
```

---

### Task 6: Integrate Icon Editing and Immediate Store Updates

**Files:**
- Modify: `frontend/src/stores/mail.ts`
- Modify: `frontend/src/views/AccountList.vue`
- Modify: `frontend/tests/mail-store.test.mjs`
- Modify: `frontend/tests/product-ui-redesign.test.mjs`

**Interfaces:**
- Consumes: `MailAccount`, `AccountIcon`, `AccountIconCropDialog`, preset registry and icon APIs.
- Produces: `patchAccount(accountId: string, patch: Partial<MailAccount>): void` in Pinia.
- Produces: account-edit icon selector, upload flow, preset flow and restore-default flow.

- [ ] **Step 1: Write failing Pinia/session contract**

Extend `mail-store.test.mjs` to assert `patchAccount()` updates the in-memory object and writes `flymail_accounts` to sessionStorage. Extend the UI contract to assert `AccountList.vue` contains `AccountIcon`, `AccountIconCropDialog`, preset selection, upload input and restore-default action.

- [ ] **Step 2: Run and confirm RED**

```bash
cd frontend
npm test -- --test-name-pattern="patches account icon|account edit offers icon"
```

- [ ] **Step 3: Type and update the account store**

Change account storage to `ref<MailAccount[]>`. Add:

```ts
function patchAccount(accountId: string, patch: Partial<MailAccount>) {
  const index = accounts.value.findIndex((account) => account.id === accountId);
  if (index < 0) return;
  accounts.value[index] = { ...accounts.value[index], ...patch };
  sessionStorage.setItem('flymail_accounts', JSON.stringify(accounts.value));
}
```

Export it. Keep loading and current-account behavior unchanged.

- [ ] **Step 4: Add the icon section to the edit dialog**

The section contains:

- `<AccountIcon :account="editingAccount" size="lg" />`.
- Preset grid built from `ACCOUNT_ICON_PRESETS`.
- Hidden file input accepting `image/jpeg,image/png,image/webp`.
- “上传图片”, “选择内置图标”, and conditional “恢复默认图标”.
- File prechecks for MIME and 10 MB size before object URL creation.
- Crop dialog shown after `Image.decode()` succeeds.

API calls:

```ts
await api.put(`/accounts/${id}/icon/preset`, { preset_id: presetId });
await api.post(`/accounts/${id}/icon/upload`, formData);
await api.delete(`/accounts/${id}/icon`);
```

Use the existing `api` multipart behavior; do not manually set a boundary. On success call `mailStore.patchAccount(id, responseFields)`, update `editingAccount`, close only the relevant preset/crop surface, and show a toast. On failure keep crop/preset state open for retry.

- [ ] **Step 5: Verify existing account editing remains intact**

```bash
cd frontend
npm test -- --test-name-pattern="patches account icon|account edit offers icon|management consoles"
npm run build
```

- [ ] **Step 6: Commit account editing**

```bash
git add frontend/src/stores/mail.ts frontend/src/views/AccountList.vue frontend/tests/mail-store.test.mjs frontend/tests/product-ui-redesign.test.mjs
git diff --staged
git commit -m "✨ 增加邮箱图标选择上传与恢复功能"
```

---

### Task 7: Replace Every Account-Identity Icon Surface

**Files:**
- Create: `backend/services/account_presenter.py`
- Create: `backend/tests/test_backup_account_icons.py`
- Modify: `backend/routes/accounts.py`
- Modify: `backend/routes/backup.py`
- Modify: `frontend/src/views/MailList.vue`
- Modify: `frontend/src/components/app/AppSidebar.vue`
- Modify: `frontend/src/views/Backup.vue`
- Modify: `frontend/src/types/mail.ts`
- Modify: `frontend/src/views/UnifiedInbox.vue`
- Modify: `frontend/tests/product-ui-redesign.test.mjs`

**Interfaces:**
- Consumes: shared `AccountIcon` component and account icon response fields.
- Preserves: provider icons in platform headings and add-account provider picker.

- [ ] **Step 1: Write the failing cross-page contract**

Assert these account identity surfaces import and render `AccountIcon`:

- `AccountList.vue` account cards;
- `MailList.vue` account switcher;
- `AppSidebar.vue` mobile account rows;
- `Backup.vue` account rows;
- `UnifiedInbox.vue` account selection and message account label.

Assert direct `providerIcon(account.provider)` remains only in platform-level UI, not user-account rows.

- [ ] **Step 2: Run and confirm RED**

```bash
cd frontend
npm test -- --test-name-pattern="all account identity surfaces"
```

- [ ] **Step 3: Update mail and mobile account lists**

Replace account row SVG/initial markup with:

```vue
<AccountIcon :account="account" size="md" decorative />
```

Keep existing labels, reauthorization actions, active state and click targets. Remove only obsolete account-avatar CSS created by the replaced markup.

- [ ] **Step 4: Extend backup account payload**

In `backend/routes/backup.py`, serialize `icon_type`, `icon_value`, and a protected `icon_url` using the same safe helper as accounts routes. Move the helper into `backend/services/account_presenter.py` rather than importing one route from another:

```python
def account_icon_fields(account: Account) -> dict[str, str]:
    icon_type = account.icon_type if account.icon_type in {"default", "preset", "upload"} else "default"
    icon_value = account.icon_value if icon_type == "preset" and account.icon_value in ACCOUNT_ICON_PRESET_IDS else ""
    if icon_type == "preset" and not icon_value:
        icon_type = "default"
    icon_url = ""
    if icon_type == "upload":
        path = resolve_account_icon(account.user_uid, account.id)
        if path and path.is_file():
            icon_url = f"/api/accounts/{account.id}/icon?v={int(account.updated_at or 0)}"
        else:
            icon_type = "default"
    return {"icon_type": icon_type, "icon_value": icon_value, "icon_url": icon_url}
```

Both `routes/accounts.py` and `routes/backup.py` consume this helper. Update `BackupAccount` in `frontend/src/types/mail.ts` and use `AccountIcon` in `Backup.vue`.

- [ ] **Step 5: Add icons to the unified inbox**

Use `mailStore.accounts` to resolve the account for each message. In account-selection rows show the icon before account copy. In message rows show a small account icon beside `accountLabel(message)`. Do not add icon binary fields to every message response; use the already-loaded account list.

- [ ] **Step 6: Verify backend and frontend contracts**

```bash
cd backend
python -m unittest tests.test_backup_account_icons -v
cd ../frontend
npm test -- --test-name-pattern="all account identity surfaces|account icon"
npm run build
```

- [ ] **Step 7: Commit the unified display migration**

```bash
git add backend/routes/backup.py backend/services/account_presenter.py backend/tests/test_backup_account_icons.py frontend/src/views/MailList.vue frontend/src/components/app/AppSidebar.vue frontend/src/views/Backup.vue frontend/src/views/UnifiedInbox.vue frontend/src/types/mail.ts frontend/tests/product-ui-redesign.test.mjs
git diff --staged
git commit -m "🎨 统一全部邮箱账号图标展示"
```

---

### Task 8: Document, Release and Deploy FlyMail 0.0.24

**Files:**
- Modify: `README.md`
- Modify: `VERSION`
- Modify through `npm run sync-version`: `package.json`, `frontend/package.json`, `docker-compose.yml`, README image tags
- Verify: all frontend/backend tests, Docker build, isolated temporary container, production container

**Interfaces:**
- Consumes: completed mail contrast plan and Tasks 1–7 of this plan.
- Produces: `benxianyu/flymail:0.0.24` locally and deployed container `flymail`.

- [ ] **Step 1: Update README behavior and storage docs**

Document:

- WCAG `4.5:1` theme-aware reading contrast for HTML mail in light/dark themes.
- Original mail HTML remains unchanged; images are not inverted.
- Account icons support default, preset and cropped upload.
- Crop output is `256×256 WebP`.
- Add `/data/flymail/files/account-icons/` to data directories.
- No new environment variables; `.env.example` remains unchanged.

- [ ] **Step 2: Bump and synchronize version**

Set `VERSION` to `0.0.24`, then run:

```bash
npm run sync-version
cat VERSION
node -e "console.log(require('./package.json').version)"
node -e "console.log(require('./frontend/package.json').version)"
```

Expected: all three values are `0.0.24`; Compose and README image references use `benxianyu/flymail:0.0.24`.

- [ ] **Step 3: Run complete test/build checks**

```bash
cd backend
python -m unittest discover -s tests -v
cd ../frontend
npm test
npm run build
cd ..
bash -n scripts/docker-entrypoint.sh
docker compose --env-file .env.example config >/dev/null
git diff --check
git status --short
git diff
```

Use the image-mounted backend test method if the host Python lacks dependencies. Do not create `.env` in Git.

- [ ] **Step 4: Build the release image**

```bash
docker build -t benxianyu/flymail:0.0.24 .
docker image inspect benxianyu/flymail:0.0.24 --format '{{.Id}}'
```

Expected: build succeeds and the frontend bundle contains `flymail-mail-color-dark`, account icon presets and crop-dialog code.

- [ ] **Step 5: Run isolated temporary-container verification**

Use a unique temporary data directory and container name. Use a database password containing quote, backslash, `@`, `:`, `/` and `%`. Verify:

1. container reaches `healthy`;
2. `/api/health` returns `0.0.24`;
3. MySQL reports 8.0 and `/data/mysql/`;
4. `/data/flymail/files/account-icons/` is created;
5. database read/write succeeds;
6. create a test user/account through DB or API, upload a generated PNG, verify protected GET returns `image/webp` and Pillow reads `256×256`;
7. restart preserves the uploaded icon and test row;
8. cross-user GET returns 404;
9. preset and restore-default remove the uploaded file at the correct time;
10. logs do not contain DB password, image bytes, raw email HTML or full DB URL;
11. image metadata contains no admin password, DB password or session key;
12. SIGTERM safely shuts down MySQL.

Clean the temporary container and directory in a trap. Never mount `/Docker/flymail/data` for this verification.

- [ ] **Step 6: Safely replace the production container**

Before replacement record:

- current image and image ID;
- port mapping;
- restart policy;
- `/Docker/flymail/data:/data` mount;
- counts for users, accounts and cached messages;
- MySQL version and data directory.

Keep the old container available until the new `flymail` container is healthy. Start `benxianyu/flymail:0.0.24` with the exact existing environment, port, restart policy and mount. Verify health, account icon directory, static CSS/JS signatures, data counts and one production restart. Roll back to the old container on any failure. Do not delete, migrate or initialize `/Docker/flymail/data` manually.

- [ ] **Step 7: Final verification before commit/push**

Re-run fresh:

```bash
cd backend && python -m unittest discover -s tests -v
cd ../frontend && npm test && npm run build
cd ..
bash -n scripts/docker-entrypoint.sh
git diff --check
git status --short
git diff
```

Confirm production status is `running/healthy`, image is `benxianyu/flymail:0.0.24`, MySQL/data mount are unchanged, and no temporary resources remain.

- [ ] **Step 8: Commit and push the release**

```bash
git add README.md VERSION package.json frontend/package.json docker-compose.yml
git diff --staged
git commit -m "📦 发布 FlyMail 0.0.24 邮件对比度与邮箱图标"
git push origin main
```

If SSH port 22 fails, retry with the approved GitHub SSH 443 command. Do not force-push. Do not upload Docker Hub unless the user explicitly requests it.
