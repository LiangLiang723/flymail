# FlyMail 附件按需缓存、容量控制与 SHA-256 去重 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将普通附件改为按需缓存，为每个用户提供可设置的独立容量上限，并让普通附件和内嵌图片通过全局 SHA-256 内容寻址存储只保留一份物理文件。

**Architecture:** 在 `/data/flymail/files/objects/sha256` 建立共享对象层，`cached_attachments` 继续保存邮件级业务引用并新增哈希和最近访问时间。独立的附件缓存服务负责原子写入、引用绑定、每用户逻辑用量、LRU 淘汰和最后引用回收；独立维护服务负责旧数据迁移和每周垃圾回收，现有路由仍先校验用户、账号、邮件和附件归属后才读取共享对象。

**Tech Stack:** Python 3、FastAPI、Pydantic 2、aiomysql、MySQL 8.0、Vue 3、TypeScript、Node.js test runner、Docker。

## Global Constraints

- 工作区固定为 `/home/chatgpt/flymail`，分支固定为 `main`。
- 当前版本为 `0.0.17`，完成实现后发布为 `0.0.18`。
- 不新增或升级生产依赖。
- 不新增环境变量；附件容量保存在按 `user_uid` 隔离的 `user_settings` 表。
- 每用户普通附件缓存默认上限为 `2048 MB`；`0` 表示不限制；非零值不得小于 `100 MB`。
- 正文和内嵌图片不计入普通附件容量上限。
- 同一用户同一 SHA-256 只计一次逻辑用量；不同用户引用同一 SHA-256 时分别计入各自逻辑用量。
- 普通附件和内嵌图片都做 SHA-256 内容去重；全局全部引用删除后才删除唯一物理文件。
- 普通附件在用户点击时按需下载；历史同步、增量同步和详情预取不主动落盘普通附件。
- 内嵌图片继续自动缓存并保持 CID 替换和离线显示。
- 超限时按每用户 LRU 淘汰普通附件；降低设置上限后立即清理。
- 不新增通过 SHA-256 直接下载对象的公开接口。
- 所有持久文件只能写入 `/data/flymail`；测试只能使用独立临时目录，不得影响 `/Docker/flymail/data`。
- MySQL 仍仅监听容器内部 `127.0.0.1:3306`，二进制日志继续关闭。
- 不删除远端邮件、邮件正文、内嵌图片、`.eml` 备份、配置、日志或用户账号数据。
- 生产升级会回收现有普通附件本地缓存；执行生产迁移前必须展示只读统计并再次确认，不能自动跨过该门槛。
- 默认推送代码到 `origin/main`，默认不上传 Docker Hub。

## File Map

**Create:**

- `backend/services/attachment_cache.py`：对象写入、有效路径解析、配额、LRU、引用释放和删除包装器。
- `backend/services/attachment_cache_maintenance.py`：旧附件迁移、孤立对象垃圾回收、启动和停止后台维护任务。
- `backend/tests/test_attachment_cache.py`：对象存储、配额、LRU、引用回收和路径安全单元测试。
- `backend/tests/test_attachment_cache_routes.py`：设置接口、附件下载、本地命中、离线和用户隔离测试。
- `backend/tests/test_attachment_cache_migration.py`：旧普通附件回收、内嵌图片去重、幂等重跑和目录保护测试。
- `frontend/src/utils/attachment-cache.ts`：容量输入校验和字节格式化纯函数。
- `frontend/tests/attachment-cache.test.ts`：设置校验和格式化测试。

**Modify:**

- `backend/data_paths.py`：增加对象目录和临时目录常量及内容寻址路径函数。
- `backend/models/__init__.py`：为 `CachedAttachment` 增加 `content_sha256` 和 `last_accessed_at`。
- `backend/db/__init__.py`：数据库迁移、对象表、引用和配额查询接口。
- `backend/services/history_sync.py`：普通附件元数据化、内嵌图片共享对象缓存、清理流程改用引用安全包装器。
- `backend/services/mail_cache.py`：远端删除同步改用引用安全清理，适配新的 `_cache_message_assets` 返回值。
- `backend/routes/messages.py`：共享对象命中、访问时间更新、按需下载、超大单附件临时响应及删除引用回收。
- `backend/routes/settings.py`：读取和保存用户容量设置，返回用量并触发即时 LRU。
- `backend/schemas.py`：设置请求、响应和清理统计模型。
- `backend/main.py`：启动和停止附件迁移及每周垃圾回收任务。
- `frontend/src/views/Settings.vue`：普通附件缓存设置卡片、用量展示、校验和清理结果。
- `backend/tests/test_attachment_storage_paths.py`、`backend/tests/test_history_sync_folders.py`、`backend/tests/test_recent_mail_sync.py`、`backend/tests/test_message_folder_resolution.py`：适配新路径和同步契约。
- `README.md`：按需缓存、容量口径、对象目录、升级迁移和离线边界。
- `VERSION`、`package.json`、`frontend/package.json`、`docker-compose.yml`：同步 `0.0.18`。

---

### Task 1: 建立内容寻址路径、模型和数据库契约

**Files:**

- Modify: `backend/data_paths.py:18-22, 145-154`
- Modify: `backend/models/__init__.py:54-67`
- Modify: `backend/db/__init__.py:390-690, 1628-1730, 1979-2005`
- Create: `backend/tests/test_attachment_cache.py`
- Modify: `backend/tests/test_attachment_storage_paths.py`

**Interfaces:**

- Produces: `ATTACHMENT_OBJECTS_DIR: Path`
- Produces: `ATTACHMENT_SHA256_DIR: Path`
- Produces: `ATTACHMENT_CACHE_TMP_DIR: Path`
- Produces: `build_attachment_object_path(content_sha256: str) -> Path`
- Produces: `CachedAttachment.content_sha256: str`
- Produces: `CachedAttachment.last_accessed_at: float`
- Produces DB functions consumed by Tasks 2–7:
  - `upsert_attachment_cache_object(content_sha256: str, size: int, local_path: str, created_at: float | None = None) -> None`
  - `get_attachment_cache_object(content_sha256: str) -> dict | None`
  - `pop_unreferenced_attachment_cache_object(content_sha256: str) -> dict | None`
  - `restore_attachment_cache_object(record: dict) -> None`
  - `replace_cached_attachment_object(attachment: CachedAttachment) -> str`
  - `touch_cached_attachment_object(account_id: str, uid: int, folder: str, part_number: int, accessed_at: float) -> bool`
  - `clear_cached_attachment_storage(account_id: str, uid: int, folder: str, part_number: int) -> str`
  - `clear_user_attachment_hash_references(user_uid: str, content_sha256: str) -> int`
  - `get_user_attachment_cache_usage_bytes(user_uid: str) -> int`
  - `get_shared_attachment_cache_usage_bytes() -> int`
  - `list_user_attachment_cache_lru(user_uid: str) -> list[dict]`
  - `list_attachment_hashes_for_messages(account_id: str, folder: str = "", uids: list[int] | None = None) -> set[str]`
  - `list_all_cached_attachment_rows() -> list[dict]`
  - `list_all_attachment_cache_objects() -> list[dict]`
  - `list_cached_attachment_local_paths() -> set[str]`

- [ ] **Step 1: Write failing path and model tests**

Add these tests to `backend/tests/test_attachment_cache.py`:

```python
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from data_paths import build_attachment_object_path, ensure_data_dirs
from models import CachedAttachment


class AttachmentCachePathTest(unittest.TestCase):
    def test_sha256_path_uses_two_character_bucket(self):
        digest = "ab" + "1" * 62
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "objects" / "sha256"
            with patch("data_paths.ATTACHMENT_SHA256_DIR", root):
                self.assertEqual(build_attachment_object_path(digest), root / "ab" / digest)

    def test_invalid_sha256_is_rejected(self):
        with self.assertRaises(ValueError):
            build_attachment_object_path("../escape")

    def test_ensure_data_dirs_creates_object_and_tmp_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with (
                patch("data_paths.BASE_DATA_DIR", base),
                patch("data_paths.FILES_DIR", base / "files"),
                patch("data_paths.CONFIG_DIR", base / "config"),
                patch("data_paths.LOGS_DIR", base / "logs"),
                patch("data_paths.UPLOADS_DIR", base / "files" / "uploads"),
                patch("data_paths.DOWNLOADS_DIR", base / "files" / "download"),
                patch("data_paths.BACKUP_DIR", base / "backup"),
                patch("data_paths.ATTACHMENT_OBJECTS_DIR", base / "files" / "objects"),
                patch("data_paths.ATTACHMENT_SHA256_DIR", base / "files" / "objects" / "sha256"),
                patch("data_paths.ATTACHMENT_CACHE_TMP_DIR", base / "files" / "objects" / ".tmp"),
            ):
                ensure_data_dirs()
                self.assertTrue((base / "files" / "objects" / "sha256").is_dir())
                self.assertTrue((base / "files" / "objects" / ".tmp").is_dir())

    def test_cached_attachment_accepts_object_reference_fields(self):
        item = CachedAttachment(
            account_id="account-1",
            user_uid="user-1",
            uid=10,
            folder="INBOX",
            part_number=1,
            content_sha256="a" * 64,
            last_accessed_at=123.0,
        )
        self.assertEqual(item.content_sha256, "a" * 64)
        self.assertEqual(item.last_accessed_at, 123.0)
```

- [ ] **Step 2: Run the tests and verify the contract is absent**

Run:

```bash
cd backend
python -m unittest tests.test_attachment_cache -v
```

Expected: FAIL because `build_attachment_object_path`, object directory constants and model fields do not exist.

- [ ] **Step 3: Add content-addressed path constants and validation**

Implement in `backend/data_paths.py`:

```python
ATTACHMENT_OBJECTS_DIR = FILES_DIR / "objects"
ATTACHMENT_SHA256_DIR = ATTACHMENT_OBJECTS_DIR / "sha256"
ATTACHMENT_CACHE_TMP_DIR = ATTACHMENT_OBJECTS_DIR / ".tmp"


def build_attachment_object_path(content_sha256: str) -> Path:
    digest = str(content_sha256 or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("invalid SHA-256 digest")
    return ATTACHMENT_SHA256_DIR / digest[:2] / digest
```

Add all three directories to `ensure_data_dirs()` without removing `DOWNLOADS_DIR`, because the latter remains the legacy migration source.

- [ ] **Step 4: Extend `CachedAttachment`**

Add to `backend/models/__init__.py`:

```python
    content_sha256: str = ""
    last_accessed_at: float = 0.0
```

Keep existing fields and defaults unchanged.

- [ ] **Step 5: Add the object table, attachment columns and indexes**

Extend `init_db()` in `backend/db/__init__.py` with:

```sql
CREATE TABLE IF NOT EXISTS attachment_cache_objects (
    content_sha256 CHAR(64) PRIMARY KEY,
    size BIGINT NOT NULL,
    local_path LONGTEXT NOT NULL,
    created_at REAL DEFAULT 0
)
```

Add idempotent column migrations:

```python
for column, declaration in (
    ("content_sha256", "CHAR(64) DEFAULT ''"),
    ("last_accessed_at", "REAL DEFAULT 0"),
):
    try:
        await db.execute(f"ALTER TABLE cached_attachments ADD COLUMN {column} {declaration}")
    except Exception as exc:
        logger.debug("migration add cached_attachments.%s ignored: %s", column, exc)
```

Add indexes:

```python
await db.execute("CREATE INDEX IF NOT EXISTS idx_cached_attachments_sha256 ON cached_attachments(content_sha256)")
await db.execute("CREATE INDEX IF NOT EXISTS idx_cached_attachments_user_inline_access ON cached_attachments(user_uid, is_inline, last_accessed_at)")
```

- [ ] **Step 6: Implement the database repository functions**

Use the exact signatures listed in **Interfaces**. `replace_cached_attachment_object()` must run `BEGIN`/`COMMIT`, read the prior hash, explicitly replace `content_sha256`, `local_path`, `size` and `last_accessed_at`, and return the prior non-empty hash when it differs from the new hash.

Update `upsert_cached_attachments()` so metadata-only writes preserve an existing object reference:

```sql
content_sha256 = CASE
    WHEN VALUES(content_sha256) <> '' THEN VALUES(content_sha256)
    ELSE cached_attachments.content_sha256
END,
local_path = CASE
    WHEN VALUES(content_sha256) <> '' THEN VALUES(local_path)
    ELSE cached_attachments.local_path
END,
last_accessed_at = CASE
    WHEN VALUES(content_sha256) <> '' THEN VALUES(last_accessed_at)
    ELSE cached_attachments.last_accessed_at
END
```

The insert clause must include both new columns. `get_cached_attachment()`, `list_cached_attachments()` and `get_cached_attachment_rows()` must return both fields.

Implement logical usage with a distinct-hash subquery:

```sql
SELECT COALESCE(SUM(objects.size), 0)
FROM attachment_cache_objects objects
JOIN (
    SELECT DISTINCT content_sha256
    FROM cached_attachments
    WHERE user_uid = ? AND is_inline = 0 AND content_sha256 <> ''
) refs ON refs.content_sha256 = objects.content_sha256
```

Implement per-user LRU with `MAX(last_accessed_at)` grouped by hash and ordered ascending.

- [ ] **Step 7: Run focused tests and existing storage-path tests**

Run:

```bash
cd backend
python -m unittest tests.test_attachment_cache tests.test_attachment_storage_paths -v
```

Expected: PASS.

- [ ] **Step 8: Commit Task 1**

```bash
git add backend/data_paths.py backend/models/__init__.py backend/db/__init__.py backend/tests/test_attachment_cache.py backend/tests/test_attachment_storage_paths.py
git diff --staged
git commit -m "🗄️ 新增附件内容寻址存储数据契约"
```

---

### Task 2: 实现共享对象写入、引用绑定和最后引用回收

**Files:**

- Create: `backend/services/attachment_cache.py`
- Modify: `backend/tests/test_attachment_cache.py`

**Interfaces:**

- Consumes: Task 1 path, model and DB functions.
- Produces:

```python
@dataclass(frozen=True)
class StoredAttachmentObject:
    content_sha256: str
    size: int
    local_path: str
    created: bool

@dataclass
class AttachmentCacheCleanup:
    before_bytes: int = 0
    after_bytes: int = 0
    cleared_references: int = 0
    evicted_user_objects: int = 0
    deleted_shared_objects: int = 0
    freed_physical_bytes: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "before_bytes": self.before_bytes,
            "after_bytes": self.after_bytes,
            "cleared_references": self.cleared_references,
            "evicted_user_objects": self.evicted_user_objects,
            "deleted_shared_objects": self.deleted_shared_objects,
            "freed_physical_bytes": self.freed_physical_bytes,
        }
```

- Produces:
  - `store_attachment_bytes(data: bytes) -> StoredAttachmentObject`
  - `store_attachment_file(source_path: Path) -> StoredAttachmentObject`
  - `cache_attachment_bytes(attachment: CachedAttachment, data: bytes, *, enforce_quota: bool = True) -> StoredAttachmentObject`
  - `cache_attachment_file(attachment: CachedAttachment, source_path: Path, *, remove_source: bool = False, enforce_quota: bool = False) -> StoredAttachmentObject`
  - `resolve_cached_attachment_path(attachment: dict, *, touch: bool = True) -> Path | None`
  - `release_unreferenced_objects(content_hashes: Iterable[str]) -> AttachmentCacheCleanup`

- [ ] **Step 1: Add failing object-store and reference tests**

Append to `backend/tests/test_attachment_cache.py`:

```python
import asyncio
import hashlib
from unittest.mock import AsyncMock, patch

from services import attachment_cache


class AttachmentObjectStoreTest(unittest.IsolatedAsyncioTestCase):
    async def test_same_content_creates_one_physical_object(self):
        payload = b"same-content"
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sha256"
            temp_root = Path(tmp) / ".tmp"
            with (
                patch.object(attachment_cache, "ATTACHMENT_SHA256_DIR", root),
                patch.object(attachment_cache, "ATTACHMENT_CACHE_TMP_DIR", temp_root),
            ):
                first = await asyncio.to_thread(attachment_cache.store_attachment_bytes, payload)
                second = await asyncio.to_thread(attachment_cache.store_attachment_bytes, payload)

        self.assertEqual(first.content_sha256, digest)
        self.assertEqual(first.local_path, second.local_path)
        self.assertTrue(first.created)
        self.assertFalse(second.created)

    async def test_concurrent_same_content_keeps_one_physical_file(self):
        payload = b"concurrent-content"
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sha256"
            temp_root = Path(tmp) / ".tmp"
            with (
                patch.object(attachment_cache, "ATTACHMENT_SHA256_DIR", root),
                patch.object(attachment_cache, "ATTACHMENT_CACHE_TMP_DIR", temp_root),
            ):
                results = await asyncio.gather(*[
                    asyncio.to_thread(attachment_cache.store_attachment_bytes, payload)
                    for _ in range(4)
                ])
                files = [path for path in root.rglob("*") if path.is_file()]

        self.assertEqual({item.content_sha256 for item in results}, {digest})
        self.assertEqual(len(files), 1)
        self.assertEqual(sum(1 for item in results if item.created), 1)

    async def test_cache_bind_releases_replaced_object(self):
        attachment = CachedAttachment(
            account_id="account-1", user_uid="user-1", uid=10,
            folder="INBOX", part_number=1, filename="a.bin",
        )
        stored = attachment_cache.StoredAttachmentObject("b" * 64, 4, "/tmp/object", True)
        with (
            patch.object(attachment_cache, "store_attachment_bytes", return_value=stored),
            patch.object(attachment_cache, "upsert_attachment_cache_object", new=AsyncMock()),
            patch.object(attachment_cache, "replace_cached_attachment_object", new=AsyncMock(return_value="a" * 64)),
            patch.object(attachment_cache, "release_unreferenced_objects", new=AsyncMock()) as release,
            patch.object(attachment_cache, "enforce_user_attachment_cache_limit", new=AsyncMock()),
        ):
            await attachment_cache.cache_attachment_bytes(attachment, b"data")

        release.assert_awaited_once_with({"a" * 64})

    async def test_last_reference_deletes_object_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sha256"
            path = root / "cc" / ("c" * 64)
            path.parent.mkdir(parents=True)
            path.write_bytes(b"payload")
            record = {"content_sha256": "c" * 64, "size": 7, "local_path": str(path), "created_at": 1.0}
            with (
                patch.object(attachment_cache, "ATTACHMENT_SHA256_DIR", root),
                patch.object(
                    attachment_cache,
                    "pop_unreferenced_attachment_cache_object",
                    new=AsyncMock(return_value=record),
                ),
            ):
                result = await attachment_cache.release_unreferenced_objects({"c" * 64})

            self.assertFalse(path.exists())
            self.assertEqual(result.deleted_shared_objects, 1)
            self.assertEqual(result.freed_physical_bytes, 7)

    async def test_unlink_failure_restores_object_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sha256"
            path = root / "dd" / ("d" * 64)
            path.parent.mkdir(parents=True)
            path.write_bytes(b"payload")
            record = {"content_sha256": "d" * 64, "size": 7, "local_path": str(path), "created_at": 1.0}
            restore = AsyncMock()
            with (
                patch.object(attachment_cache, "ATTACHMENT_SHA256_DIR", root),
                patch.object(attachment_cache, "pop_unreferenced_attachment_cache_object", new=AsyncMock(return_value=record)),
                patch.object(attachment_cache, "restore_attachment_cache_object", restore),
                patch("pathlib.Path.unlink", side_effect=PermissionError("denied")),
            ):
                result = await attachment_cache.release_unreferenced_objects({"d" * 64})

            self.assertTrue(path.exists())
            restore.assert_awaited_once_with(record)
            self.assertEqual(result.deleted_shared_objects, 0)
            self.assertEqual(result.freed_physical_bytes, 0)

    async def test_release_refuses_object_path_outside_object_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sha256"
            outside = Path(tmp) / "outside.bin"
            outside.write_bytes(b"payload")
            record = {"content_sha256": "e" * 64, "size": 7, "local_path": str(outside), "created_at": 1.0}
            restore = AsyncMock()
            with (
                patch.object(attachment_cache, "ATTACHMENT_SHA256_DIR", root),
                patch.object(attachment_cache, "pop_unreferenced_attachment_cache_object", new=AsyncMock(return_value=record)),
                patch.object(attachment_cache, "restore_attachment_cache_object", restore),
            ):
                result = await attachment_cache.release_unreferenced_objects({"e" * 64})

            self.assertTrue(outside.exists())
            restore.assert_awaited_once_with(record)
            self.assertEqual(result.deleted_shared_objects, 0)
```

- [ ] **Step 2: Run tests and verify the service is missing**

Run:

```bash
cd backend
python -m unittest tests.test_attachment_cache -v
```

Expected: FAIL because `services.attachment_cache` and its interfaces do not exist.

- [ ] **Step 3: Implement atomic SHA-256 object creation**

In `backend/services/attachment_cache.py`, use 1 MiB chunks and a temporary file in `ATTACHMENT_CACHE_TMP_DIR`. Finalize with an atomic hard-link create so concurrent identical writes cannot overwrite an existing object:

```python
CHUNK_SIZE = 1024 * 1024


def _finalize_temp_object(temp_path: Path, digest: str, size: int) -> StoredAttachmentObject:
    target = build_attachment_object_path(digest)
    target.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        os.link(temp_path, target)
        created = True
    except FileExistsError:
        pass
    finally:
        temp_path.unlink(missing_ok=True)
    return StoredAttachmentObject(digest, size, str(target), created)
```

`store_attachment_bytes()` and `store_attachment_file()` must compute the digest while writing/copying, flush and `os.fsync()` the temporary file before finalization, and delete temporary files on every failure path.

- [ ] **Step 4: Implement object binding and rollback cleanup**

Declare one module-level `asyncio.Lock`:

```python
_OBJECT_MUTATION_LOCK = asyncio.Lock()
```

`cache_attachment_bytes()` must:

1. acquire `_OBJECT_MUTATION_LOCK`;
2. call `store_attachment_bytes()` in `asyncio.to_thread` while holding the lock, so an unreferenced-object deletion cannot remove a file between physical finalization and reference binding;
3. upsert the object row;
4. create a copy of `CachedAttachment` with hash, path, actual size, `cached_at` and `last_accessed_at`;
5. call `replace_cached_attachment_object()`;
6. release the lock;
7. call `release_unreferenced_objects({previous_hash})` when the previous hash changed;
8. enforce quota only for non-inline attachments when requested.

On database binding failure, release the lock, call `release_unreferenced_objects({new_hash})`, then re-raise. A concurrent writer that binds the same hash before cleanup is protected because the repository deletion checks the real remaining reference count.

`cache_attachment_file()` follows the same sequence and unlinks `source_path` only after the database reference is committed and only when `remove_source=True`.

- [ ] **Step 5: Implement valid cached-path resolution**

`resolve_cached_attachment_path()` must verify all of the following:

```python
object_record is not None
Path(object_record["local_path"]).resolve() == Path(attachment["local_path"]).resolve()
path.is_file()
path.resolve().is_relative_to(ATTACHMENT_SHA256_DIR.resolve())
not path.is_symlink()
```

When invalid, clear the attachment storage reference and release the old hash. When valid and `touch=True`, update `last_accessed_at` for the exact attachment row.

- [ ] **Step 6: Implement last-reference object deletion with restore-on-unlink-failure**

`release_unreferenced_objects()` must acquire `_OBJECT_MUTATION_LOCK`, then call `pop_unreferenced_attachment_cache_object()` for each normalized 64-character hash. This lock is shared with object finalization and reference binding, preventing a file from being deleted after a writer has observed it but before that writer binds its database reference.

Before unlinking, require the resolved path to be a non-symlink regular file inside `ATTACHMENT_SHA256_DIR`; otherwise restore the object row and leave the file untouched. If unlink fails, call `restore_attachment_cache_object(record)` and log only a short hash prefix, path basename and error type. Do not roll back an already completed business-reference deletion.

- [ ] **Step 7: Run object-store tests**

Run:

```bash
cd backend
python -m unittest tests.test_attachment_cache -v
```

Expected: PASS.

- [ ] **Step 8: Commit Task 2**

```bash
git add backend/services/attachment_cache.py backend/tests/test_attachment_cache.py
git diff --staged
git commit -m "♻️ 实现附件共享对象写入与引用回收"
```

---

### Task 3: 实现每用户逻辑用量和 LRU 容量控制

**Files:**

- Modify: `backend/services/attachment_cache.py`
- Modify: `backend/tests/test_attachment_cache.py`

**Interfaces:**

- Produces constants:

```python
ATTACHMENT_CACHE_LIMIT_KEY = "attachment_cache_limit_mb"
DEFAULT_ATTACHMENT_CACHE_LIMIT_MB = 2048
MIN_ATTACHMENT_CACHE_LIMIT_MB = 100
```

- Produces:
  - `get_user_attachment_cache_limit_mb(user_uid: str) -> int`
  - `get_user_attachment_cache_usage(user_uid: str) -> int`
  - `get_shared_attachment_cache_usage() -> int`
  - `enforce_user_attachment_cache_limit(user_uid: str, limit_mb: int | None = None, *, protected_sha256: str = "") -> AttachmentCacheCleanup`
  - `should_persist_normal_attachment(user_uid: str, size: int) -> bool`
  - `write_transient_download(data: bytes) -> Path`
  - `remove_transient_download(path: Path) -> None`

- [ ] **Step 1: Add failing validation, accounting and LRU tests**

Append:

```python
class AttachmentQuotaTest(unittest.IsolatedAsyncioTestCase):
    def test_limit_validation_accepts_zero_and_100_plus(self):
        self.assertEqual(attachment_cache.validate_attachment_cache_limit_mb(0), 0)
        self.assertEqual(attachment_cache.validate_attachment_cache_limit_mb(100), 100)
        self.assertEqual(attachment_cache.validate_attachment_cache_limit_mb(2048), 2048)
        with self.assertRaises(ValueError):
            attachment_cache.validate_attachment_cache_limit_mb(99)
        with self.assertRaises(ValueError):
            attachment_cache.validate_attachment_cache_limit_mb(-1)

    async def test_zero_limit_does_not_evict(self):
        with patch.object(attachment_cache, "get_user_attachment_cache_usage_bytes", new=AsyncMock(return_value=500)):
            result = await attachment_cache.enforce_user_attachment_cache_limit("user-1", 0)
        self.assertEqual(result.before_bytes, 500)
        self.assertEqual(result.after_bytes, 500)

    async def test_lru_clears_oldest_user_hash_first(self):
        mb = 1024 * 1024
        lru = [
            {"content_sha256": "a" * 64, "size": 80 * mb, "last_accessed_at": 10},
            {"content_sha256": "b" * 64, "size": 80 * mb, "last_accessed_at": 20},
        ]
        usage = AsyncMock(side_effect=[160 * mb, 80 * mb])
        clear = AsyncMock(return_value=3)
        release = AsyncMock(return_value=attachment_cache.AttachmentCacheCleanup(deleted_shared_objects=1, freed_physical_bytes=80))
        with (
            patch.object(attachment_cache, "get_user_attachment_cache_usage_bytes", usage),
            patch.object(attachment_cache, "list_user_attachment_cache_lru", new=AsyncMock(return_value=lru)),
            patch.object(attachment_cache, "clear_user_attachment_hash_references", clear),
            patch.object(attachment_cache, "release_unreferenced_objects", release),
        ):
            result = await attachment_cache.enforce_user_attachment_cache_limit("user-1", 100)

        clear.assert_awaited_once_with("user-1", "a" * 64)
        self.assertEqual(result.cleared_references, 3)
        self.assertEqual(result.evicted_user_objects, 1)
```

Use integer byte values in the implementation tests by patching the conversion helper or passing a limit whose converted bytes are deterministic; do not use floating-point limits in production code.

- [ ] **Step 2: Run tests and verify quota behavior is absent**

```bash
cd backend
python -m unittest tests.test_attachment_cache.AttachmentQuotaTest -v
```

Expected: FAIL.

- [ ] **Step 3: Implement setting lookup and validation**

```python
def validate_attachment_cache_limit_mb(value: int) -> int:
    normalized = int(value)
    if normalized < 0 or 0 < normalized < MIN_ATTACHMENT_CACHE_LIMIT_MB:
        raise ValueError("非零容量不能低于 100 MB")
    return normalized


async def get_user_attachment_cache_limit_mb(user_uid: str) -> int:
    value = await get_user_setting(user_uid, ATTACHMENT_CACHE_LIMIT_KEY, DEFAULT_ATTACHMENT_CACHE_LIMIT_MB)
    try:
        return validate_attachment_cache_limit_mb(int(value))
    except (TypeError, ValueError):
        return DEFAULT_ATTACHMENT_CACHE_LIMIT_MB
```

- [ ] **Step 4: Implement LRU eviction**

`enforce_user_attachment_cache_limit()` must:

1. read `before_bytes`;
2. return immediately for limit `0`;
3. convert MB with `limit_bytes = limit_mb * 1024 * 1024`;
4. iterate DB-provided LRU rows oldest first;
5. skip `protected_sha256`;
6. clear all of the current user’s non-inline references for a selected hash;
7. call `release_unreferenced_objects()`;
8. recalculate usage after each eviction;
9. stop at or below the limit;
10. return complete statistics.

The LRU query already groups by hash, so one eviction clears every ordinary attachment reference that the current user has to that object.

- [ ] **Step 5: Handle a single attachment larger than the user limit**

`should_persist_normal_attachment()` returns `False` only when the limit is nonzero and the single file is larger than the limit. In that case the route will write a temporary response file under `ATTACHMENT_CACHE_TMP_DIR` and remove it after `FileResponse` completes; it must not create a persistent object or database cache reference.

Implement `write_transient_download()` with a random `.download` filename, `fsync()`, and no client-controlled path. `remove_transient_download()` must verify the resolved path is inside `ATTACHMENT_CACHE_TMP_DIR` before unlinking.

- [ ] **Step 6: Run quota tests**

```bash
cd backend
python -m unittest tests.test_attachment_cache -v
```

Expected: PASS.

- [ ] **Step 7: Commit Task 3**

```bash
git add backend/services/attachment_cache.py backend/tests/test_attachment_cache.py
git diff --staged
git commit -m "📦 新增每用户附件缓存配额与 LRU 淘汰"
```

---

### Task 4: 将同步流程改为普通附件元数据化、内嵌图片去重缓存

**Files:**

- Modify: `backend/services/history_sync.py:597-765`
- Modify: `backend/services/mail_cache.py:540-590, 795-840`
- Modify: `backend/routes/messages.py:273-305`
- Modify: `backend/tests/test_history_sync_folders.py`
- Modify: `backend/tests/test_recent_mail_sync.py`

**Interfaces:**

- Consumes: `cache_attachment_bytes()`, `resolve_cached_attachment_path()` and metadata-preserving `upsert_cached_attachments()`.
- Changes `_cache_message_assets()` to:

```python
async def _cache_message_assets(
    receiver,
    account,
    folder_name: str,
    detail,
) -> tuple[str, str, int, int]
```

The function becomes the sole persistence point for attachment metadata and inline object content; callers no longer receive or separately upsert `attachment_records`.

- [ ] **Step 1: Add failing sync behavior tests**

Add to `backend/tests/test_history_sync_folders.py`:

```python
from providers.base import Attachment, Message


class AttachmentSyncPolicyTest(unittest.IsolatedAsyncioTestCase):
    async def test_normal_attachment_keeps_metadata_without_fetching_content(self):
        detail = Message(
            id="10", uid=10, subject="mail", from_addr="a@example.com",
            to_addr="b@example.com", date="2026-07-30T10:00:00Z",
            attachments=[Attachment(filename="report.zip", size=1024, part_number=2, data=b"embedded-binary")],
        )
        receiver = AsyncMock()
        account = types.SimpleNamespace(id="account-1", user_uid="user-1", email="user@example.com")
        upsert = AsyncMock()
        with (
            patch.object(history_sync, "get_cached_message_detail", AsyncMock(return_value=None)),
            patch.object(history_sync, "get_cached_attachment", AsyncMock(return_value=None)),
            patch.object(history_sync, "upsert_cached_attachments", upsert),
            patch.object(history_sync, "cache_attachment_bytes", AsyncMock()) as cache_bytes,
        ):
            await history_sync._cache_message_assets(receiver, account, "INBOX", detail)

        receiver.fetch_attachment_data.assert_not_awaited()
        cache_bytes.assert_not_awaited()
        saved = upsert.await_args.args[0][0]
        self.assertFalse(saved.is_inline)
        self.assertEqual(saved.local_path, "")
        self.assertEqual(saved.content_sha256, "")

    async def test_inline_image_is_cached_and_cid_is_rewritten(self):
        detail = Message(
            id="11", uid=11, subject="mail", from_addr="a@example.com",
            to_addr="b@example.com", date="2026-07-30T10:00:00Z",
            body_html='<img src="cid:image-1">',
            attachments=[Attachment(
                filename="logo.png", content_type="image/png", size=4,
                part_number=1, content_id="<image-1>", is_inline=True, data=b"logo",
            )],
        )
        receiver = AsyncMock()
        account = types.SimpleNamespace(id="account-1", user_uid="user-1", email="user@example.com")
        with (
            patch.object(history_sync, "get_cached_message_detail", AsyncMock(return_value=None)),
            patch.object(history_sync, "get_cached_attachment", AsyncMock(return_value=None)),
            patch.object(history_sync, "cache_attachment_bytes", AsyncMock()),
        ):
            body_html, _, normal_count, inline_count = await history_sync._cache_message_assets(receiver, account, "INBOX", detail)

        self.assertIn("/api/messages/11/attachments/1", body_html)
        self.assertEqual(normal_count, 0)
        self.assertEqual(inline_count, 1)
```

- [ ] **Step 2: Run focused sync tests and verify failure**

```bash
cd backend
python -m unittest tests.test_history_sync_folders tests.test_recent_mail_sync -v
```

Expected: FAIL because normal attachments are still written and `_cache_message_assets()` still returns five values.

- [ ] **Step 3: Refactor `_cache_message_assets()`**

For every attachment, construct a `CachedAttachment` metadata record. For ordinary attachments, call only `upsert_cached_attachments([record])`; do not inspect `attachment.data`, call `fetch_attachment_data()`, or create a legacy path.

For inline attachments:

1. check an existing row with `resolve_cached_attachment_path(existing, touch=False)`;
2. if valid, preserve its hash/path and rewrite the CID URL;
3. otherwise use `attachment.data` when present, falling back to `receiver.fetch_attachment_data()`;
4. call `cache_attachment_bytes(record, data, enforce_quota=False)`;
5. rewrite CID only after a valid shared object exists.

`downloaded_attachments` must remain `0` for ordinary metadata-only rows. `downloaded_inline_images` counts inline objects available after the function.

- [ ] **Step 4: Update all callers to the four-value return contract**

Change these call sites:

- `backend/services/history_sync.py:_cache_message_detail`
- `backend/services/mail_cache.py` two detail-caching branches
- `backend/routes/messages.py:_cache_remote_detail_with_assets`

Remove the four subsequent `upsert_cached_attachments(attachment_records)` blocks and obsolete imports.

- [ ] **Step 5: Update existing stubs and mocks**

Change every test stub from:

```python
("", "", 0, 0, [])
```

to:

```python
("", "", 0, 0)
```

Remove patches for caller-side `upsert_cached_attachments` where no longer used.

- [ ] **Step 6: Run sync tests**

```bash
cd backend
python -m unittest tests.test_history_sync_folders tests.test_recent_mail_sync tests.test_message_folder_resolution -v
```

Expected: PASS.

- [ ] **Step 7: Commit Task 4**

```bash
git add backend/services/history_sync.py backend/services/mail_cache.py backend/routes/messages.py backend/tests/test_history_sync_folders.py backend/tests/test_recent_mail_sync.py backend/tests/test_message_folder_resolution.py
git diff --staged
git commit -m "📨 改为普通附件按需缓存并保留内嵌图片"
```

---

### Task 5: 改造附件下载、本地命中和所有引用删除入口

**Files:**

- Modify: `backend/services/attachment_cache.py`
- Modify: `backend/routes/messages.py:507-551, 1078-1157, 1265-1350`
- Modify: `backend/services/mail_cache.py:430-520`
- Modify: `backend/services/history_sync.py:330-590`
- Create: `backend/tests/test_attachment_cache_routes.py`
- Modify: `backend/tests/test_message_folder_resolution.py`
- Modify: `backend/tests/test_mail_cache_folder_resolution.py`

**Interfaces:**

- Produces download result:

```python
@dataclass(frozen=True)
class AttachmentDownloadFile:
    path: str
    transient: bool = False
```

- Produces deletion wrappers:
  - `delete_cached_message_and_release(account_id: str, uid: int, folder: str) -> bool`
  - `batch_delete_cached_messages_and_release(account_id: str, uids: list[int], folder: str) -> int`
  - `purge_deleted_from_cache_and_release(account_id: str, folder: str, valid_uids: set[int]) -> int`
  - `clear_account_cache_and_release(account_id: str) -> tuple[int, int]`
- Attachment route returns `FileResponse` from either a shared object or an oversized transient file.

- [ ] **Step 1: Add failing route and deletion tests**

Create `backend/tests/test_attachment_cache_routes.py` with tests covering:

```python
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from errors import AppError
from routes import messages, settings
from schemas import SettingsUpdateRequest
from services import attachment_cache


class AttachmentDownloadPolicyTest(unittest.IsolatedAsyncioTestCase):
    async def test_local_object_hit_touches_and_returns_without_remote_connect(self):
        cached = {
            "filename": "report.pdf", "content_type": "application/pdf", "size": 4,
            "content_id": "", "is_inline": False, "local_path": "/objects/hash",
            "content_sha256": "a" * 64, "last_accessed_at": 1,
        }
        with (
            patch("routes.messages._get_account", new=AsyncMock(return_value=("user-1", types.SimpleNamespace(id="account-1", status="active")))),
            patch("routes.messages.get_cached_attachment", new=AsyncMock(return_value=cached)),
            patch("routes.messages.resolve_cached_attachment_path", new=AsyncMock(return_value=Path("/objects/hash"))),
            patch("routes.messages.ProviderFactory") as factory,
        ):
            response = await messages.download_attachment(object(), "10", 1, "INBOX", "account-1")

        self.assertEqual(response.path, "/objects/hash")
        factory.get_receiver.assert_not_called()

    async def test_offline_without_valid_cache_keeps_existing_error(self):
        account = types.SimpleNamespace(id="account-1", status="offline")
        with (
            patch("routes.messages._get_account", new=AsyncMock(return_value=("user-1", account))),
            patch("routes.messages.get_cached_attachment", new=AsyncMock(return_value=None)),
        ):
            with self.assertRaises(AppError) as raised:
                await messages.download_attachment(object(), "10", 1, "INBOX", "account-1")
        self.assertEqual(raised.exception.status_code, 404)

    async def test_delete_wrapper_releases_only_affected_hashes(self):
        hashes = {"a" * 64, "b" * 64}
        with (
            patch("services.attachment_cache.list_attachment_hashes_for_messages", new=AsyncMock(return_value=hashes)),
            patch("services.attachment_cache.delete_cached_message", new=AsyncMock(return_value=True)),
            patch("services.attachment_cache.release_unreferenced_objects", new=AsyncMock()) as release,
        ):
            deleted = await attachment_cache.delete_cached_message_and_release("account-1", 10, "INBOX")
        self.assertTrue(deleted)
        release.assert_awaited_once_with(hashes)

    async def test_other_users_account_is_rejected_before_object_resolution(self):
        own_account = types.SimpleNamespace(id="account-1", status="active")
        resolver = AsyncMock()
        with (
            patch("routes.messages.get_uid", new=AsyncMock(return_value="user-1")),
            patch("routes.messages.get_accounts", new=AsyncMock(return_value=[own_account])),
            patch("routes.messages.resolve_cached_attachment_path", resolver),
        ):
            with self.assertRaises(AppError):
                await messages.download_attachment(object(), "10", 1, "INBOX", "account-2")
        resolver.assert_not_awaited()
```

- [ ] **Step 2: Run route tests and verify failure**

```bash
cd backend
python -m unittest tests.test_attachment_cache_routes -v
```

Expected: FAIL because route integration and wrappers do not exist.

- [ ] **Step 3: Replace `_persist_attachment_locally()` with shared-object persistence**

Keep the function name to minimize caller churn and change its signature to:

```python
async def _persist_attachment_locally(
    *,
    account: Account,
    user_uid: str,
    folder: str,
    uid_num: int,
    message_date: str,
    attachment,
    data: bytes,
) -> AttachmentDownloadFile:
```

It must build a `CachedAttachment` metadata model and call `cache_attachment_bytes()`. A persistent cache returns `AttachmentDownloadFile(path=stored.local_path, transient=False)`.

Before persistent caching, call `should_persist_normal_attachment(user_uid, len(data))`. For an oversized normal attachment, call `write_transient_download(data)` and return `AttachmentDownloadFile(path=str(temp_path), transient=True)` without binding an object.

- [ ] **Step 4: Update `download_attachment()`**

Use this order:

```python
cached_attachment = await get_cached_attachment(account.id, uid_num, folder, part_number)
local_path = await resolve_cached_attachment_path(cached_attachment, touch=True) if cached_attachment else None
if local_path:
    filename = cached_attachment.get("filename") or local_path.name
    return FileResponse(str(local_path), filename=filename)
if account.status == "offline":
    raise AppError(404, "本地未找到附件，且账号当前处于离线状态")
# existing authenticated remote fetch
```

After remote fetch, use the exact result contract:

```python
from starlette.background import BackgroundTask

saved = await _persist_attachment_locally(
    account=account,
    user_uid=user_uid,
    folder=folder,
    uid_num=uid_num,
    message_date=detail.date or "",
    attachment=attachment,
    data=data,
)
response_path = Path(saved.path)
return FileResponse(
    saved.path,
    filename=attachment.filename or response_path.name,
    background=BackgroundTask(remove_transient_download, response_path) if saved.transient else None,
)
```

Inline images are always persisted through the shared object store and do not use the normal-attachment quota gate.

- [ ] **Step 5: Implement and use reference-safe deletion wrappers**

Each wrapper must list affected hashes before deleting rows, call the existing DB deletion function, and then call `release_unreferenced_objects()`.

Replace direct imports and calls in:

- `routes/messages.py` single and batch delete;
- `services/mail_cache.py` both `purge_deleted_from_cache` paths;
- `services/history_sync.py` account cache clear, account deletion and folder clear.

`clear_account_cache_and_release()` must delete cached messages and attachment rows, release affected hashes, and return `(deleted_messages, deleted_attachment_rows)`. It must not recursively delete the global object directory.

- [ ] **Step 6: Remove legacy per-account file deletion from active clear paths**

After wrappers are in place, `run_clear_cache()` and `run_delete_account()` must not enumerate or unlink shared object paths as if they belonged exclusively to one account. They may remove the old legacy account directory under `DOWNLOADS_DIR`, but only after reference-safe database cleanup and only through the existing safe account slug path.

- [ ] **Step 7: Run route, deletion and sync tests**

```bash
cd backend
python -m unittest \
  tests.test_attachment_cache_routes \
  tests.test_message_folder_resolution \
  tests.test_mail_cache_folder_resolution \
  tests.test_history_sync_folders \
  -v
```

Expected: PASS.

- [ ] **Step 8: Commit Task 5**

```bash
git add backend/services/attachment_cache.py backend/routes/messages.py backend/services/mail_cache.py backend/services/history_sync.py backend/tests/test_attachment_cache_routes.py backend/tests/test_message_folder_resolution.py backend/tests/test_mail_cache_folder_resolution.py backend/tests/test_history_sync_folders.py
git diff --staged
git commit -m "🔗 统一附件下载与缓存引用安全回收"
```

---

### Task 6: 增加用户容量设置 API 和设置页

**Files:**

- Modify: `backend/schemas.py:17-54`
- Modify: `backend/routes/settings.py:284-375`
- Modify: `backend/tests/test_attachment_cache_routes.py`
- Modify: `backend/tests/test_history_sync_progress.py`
- Create: `frontend/src/utils/attachment-cache.ts`
- Create: `frontend/tests/attachment-cache.test.ts`
- Modify: `frontend/src/views/Settings.vue:45-108, 720-850, 1074-1120`

**Interfaces:**

- `GET /api/settings` adds:
  - `attachment_cache_limit_mb: int`
  - `attachment_cache_usage_bytes: int`
  - `attachment_cache_shared_physical_bytes: int`
- `PUT /api/settings` accepts `attachment_cache_limit_mb: int | None`.
- `PUT /api/settings` optionally returns `attachment_cache_cleanup` with all six cleanup counters.
- Frontend utility exports:
  - `isValidAttachmentCacheLimit(value: number) -> boolean`
  - `formatStorageBytes(value: number) -> string`

- [ ] **Step 1: Add failing backend setting tests**

Add to `backend/tests/test_attachment_cache_routes.py`:

```python
class AttachmentCacheSettingsTest(unittest.IsolatedAsyncioTestCase):
    async def test_get_settings_uses_current_user_limit_and_usage(self):
        with (
            patch("routes.settings.get_uid", new=AsyncMock(return_value="user-1")),
            patch("routes.settings.async_load_settings", new=AsyncMock(return_value={})),
            patch("routes.settings.get_user_settings", new=AsyncMock(return_value={"attachment_cache_limit_mb": 512})),
            patch("routes.settings.get_user_attachment_cache_usage", new=AsyncMock(return_value=1234)),
            patch("routes.settings.get_shared_attachment_cache_usage", new=AsyncMock(return_value=5678)),
        ):
            result = await settings.get_settings(object())
        self.assertEqual(result["attachment_cache_limit_mb"], 512)
        self.assertEqual(result["attachment_cache_usage_bytes"], 1234)
        self.assertEqual(result["attachment_cache_shared_physical_bytes"], 5678)

    async def test_lowering_limit_saves_current_user_and_returns_cleanup(self):
        cleanup = attachment_cache.AttachmentCacheCleanup(before_bytes=200, after_bytes=100, cleared_references=2)
        body = SettingsUpdateRequest(attachment_cache_limit_mb=100)
        with (
            patch("routes.settings.get_uid", new=AsyncMock(return_value="user-1")),
            patch("routes.settings.set_user_settings", new=AsyncMock()) as save_user,
            patch("routes.settings.enforce_user_attachment_cache_limit", new=AsyncMock(return_value=cleanup)),
            patch("routes.settings.async_save_settings", new=AsyncMock(return_value={})),
        ):
            result = await settings.update_settings(object(), body)
        save_user.assert_awaited_once_with("user-1", {"attachment_cache_limit_mb": 100})
        self.assertEqual(result["attachment_cache_cleanup"]["after_bytes"], 100)
```

Add schema tests asserting `0`, `100`, `2048` are accepted and `-1`, `1`, `99` raise `ValidationError`.

- [ ] **Step 2: Add failing frontend utility tests**

Create `frontend/tests/attachment-cache.test.ts`:

```typescript
import test from 'node:test';
import assert from 'node:assert/strict';
import { formatStorageBytes, isValidAttachmentCacheLimit } from '../src/utils/attachment-cache.ts';

test('validates zero or values of at least 100 MB', () => {
  assert.equal(isValidAttachmentCacheLimit(0), true);
  assert.equal(isValidAttachmentCacheLimit(99), false);
  assert.equal(isValidAttachmentCacheLimit(100), true);
  assert.equal(isValidAttachmentCacheLimit(2048), true);
});

test('formats byte values for storage display', () => {
  assert.equal(formatStorageBytes(0), '0 B');
  assert.equal(formatStorageBytes(1024), '1 KB');
  assert.equal(formatStorageBytes(1024 * 1024), '1 MB');
  assert.equal(formatStorageBytes(1.5 * 1024 * 1024 * 1024), '1.5 GB');
});
```

- [ ] **Step 3: Run backend and frontend tests and verify failure**

```bash
cd backend
python -m unittest tests.test_attachment_cache_routes -v
cd ../frontend
node --test tests/attachment-cache.test.ts
```

Expected: FAIL because schemas, routes and utility module are absent.

- [ ] **Step 4: Extend Pydantic schemas**

Import `field_validator` and add:

```python
class AttachmentCacheCleanupResponse(BaseModel):
    before_bytes: int = 0
    after_bytes: int = 0
    cleared_references: int = 0
    evicted_user_objects: int = 0
    deleted_shared_objects: int = 0
    freed_physical_bytes: int = 0
```

Add the GET fields to `SettingsResponse`, the optional cleanup field to `SettingsUpdateResponse`, and this request field:

```python
attachment_cache_limit_mb: Optional[int] = Field(default=None, ge=0)

@field_validator("attachment_cache_limit_mb")
@classmethod
def validate_attachment_cache_limit(cls, value: Optional[int]) -> Optional[int]:
    if value is not None and 0 < value < 100:
        raise ValueError("非零容量不能低于 100 MB")
    return value
```

- [ ] **Step 5: Extend settings routes with user-isolated values**

`get_settings()` must request `attachment_cache_limit_mb` together with existing Gmail proxy keys, normalize through `validate_attachment_cache_limit_mb()`, and return current logical and shared physical usage.

`update_settings()` must remove `attachment_cache_limit_mb` from the global JSON update payload, save it with `set_user_settings(uid, {"attachment_cache_limit_mb": limit})`, then await `enforce_user_attachment_cache_limit(uid, limit)` and include `cleanup.as_dict()` in the response. Global OAuth and upload-cleanup behavior must remain unchanged.

- [ ] **Step 6: Implement frontend utility and settings card**

Place a new “普通附件缓存” card after the upload cleanup card. It must show:

- current user logical usage;
- shared object physical total with wording that it is informational and not the user quota;
- integer MB input;
- “默认 2048 MB；0 表示不限制；非零最小 100 MB”；
- “正文和内嵌图片不计入上限”；
- save button using the existing `saveSettings()` state.

Extend `SettingsForm` with:

```typescript
attachment_cache_limit_mb: number;
attachment_cache_usage_bytes: number;
attachment_cache_shared_physical_bytes: number;
```

Capture the PUT response and render cleanup text such as:

```typescript
attachmentCleanupMessage.value = cleanup
  ? `已从 ${formatStorageBytes(cleanup.before_bytes)} 清理到 ${formatStorageBytes(cleanup.after_bytes)}，实际释放 ${formatStorageBytes(cleanup.freed_physical_bytes)}`
  : '';
```

Reject invalid input before sending the API request and show “非零容量不能低于 100 MB”。

- [ ] **Step 7: Run settings tests and frontend build**

```bash
cd backend
python -m unittest tests.test_attachment_cache_routes tests.test_history_sync_progress -v
cd ../frontend
npm test
npm run build
```

Expected: PASS. Existing chunk-size warnings may remain, but no new TypeScript errors are allowed.

- [ ] **Step 8: Commit Task 6**

```bash
git add backend/schemas.py backend/routes/settings.py backend/tests/test_attachment_cache_routes.py backend/tests/test_history_sync_progress.py frontend/src/utils/attachment-cache.ts frontend/tests/attachment-cache.test.ts frontend/src/views/Settings.vue
git diff --staged
git commit -m "⚙️ 新增用户附件缓存容量设置与用量展示"
```

---

### Task 7: 实现旧数据迁移和每周垃圾回收

**Files:**

- Create: `backend/services/attachment_cache_maintenance.py`
- Modify: `backend/services/attachment_cache.py`
- Modify: `backend/main.py:60-82`
- Create: `backend/tests/test_attachment_cache_migration.py`
- Modify: `backend/tests/test_static_files.py`：补充新生命周期服务的导入桩，确保 `main.py` 可在隔离测试中加载。

**Interfaces:**

- Produces:
  - `is_safe_legacy_download_file(path: Path) -> bool`
  - `migrate_legacy_attachment_cache() -> dict[str, int]`
  - `garbage_collect_attachment_cache(orphan_grace_seconds: int = 3600) -> dict[str, int]`
  - `remove_stale_untracked_cache_files(known_hashes: set[str], older_than: float) -> dict[str, int]`
  - `start_attachment_cache_maintenance() -> None`
  - `stop_attachment_cache_maintenance() -> Awaitable[None]`

- [ ] **Step 1: Add failing migration and safety tests**

Create `backend/tests/test_attachment_cache_migration.py` with:

```python
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from models import CachedAttachment
from services import attachment_cache
from services import attachment_cache_maintenance as maintenance


class AttachmentCacheMigrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_normal_legacy_attachment_is_uncached_but_metadata_survives(self):
        with tempfile.TemporaryDirectory() as tmp:
            downloads = Path(tmp) / "download"
            legacy = downloads / "user@example.com" / "2026" / "07" / "10" / "1_report.pdf"
            legacy.parent.mkdir(parents=True)
            legacy.write_bytes(b"report")
            row = {
                "account_id": "account-1", "user_uid": "user-1", "uid": 10,
                "folder": "INBOX", "part_number": 1, "filename": "report.pdf",
                "content_type": "application/pdf", "size": 6, "content_id": "",
                "is_inline": False, "local_path": str(legacy), "content_sha256": "",
                "last_accessed_at": 0,
            }
            with (
                patch.object(maintenance, "DOWNLOADS_DIR", downloads),
                patch.object(maintenance, "list_all_cached_attachment_rows", new=AsyncMock(return_value=[row])),
                patch.object(maintenance, "clear_cached_attachment_storage", new=AsyncMock(return_value="")) as clear,
                patch.object(maintenance, "list_cached_attachment_local_paths", new=AsyncMock(return_value=set())),
            ):
                stats = await maintenance.migrate_legacy_attachment_cache()

            clear.assert_awaited_once_with("account-1", 10, "INBOX", 1)
            self.assertFalse(legacy.exists())
            self.assertEqual(stats["normal_files_removed"], 1)

    async def test_duplicate_inline_images_share_one_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            downloads = Path(tmp) / "download"
            first = downloads / "a" / "1_logo.png"
            second = downloads / "b" / "1_logo.png"
            first.parent.mkdir(parents=True)
            second.parent.mkdir(parents=True)
            first.write_bytes(b"same-image")
            second.write_bytes(b"same-image")
            rows = [
                {
                    "account_id": "account-1", "user_uid": "user-1", "uid": 10,
                    "folder": "INBOX", "part_number": 1, "filename": "logo.png",
                    "content_type": "image/png", "size": 10, "content_id": "cid-1",
                    "is_inline": True, "local_path": str(first), "content_sha256": "",
                    "last_accessed_at": 0,
                },
                {
                    "account_id": "account-2", "user_uid": "user-2", "uid": 20,
                    "folder": "INBOX", "part_number": 1, "filename": "logo.png",
                    "content_type": "image/png", "size": 10, "content_id": "cid-2",
                    "is_inline": True, "local_path": str(second), "content_sha256": "",
                    "last_accessed_at": 0,
                },
            ]
            stored = attachment_cache.StoredAttachmentObject(
                content_sha256="d" * 64,
                size=10,
                local_path=str(Path(tmp) / "objects" / ("d" * 64)),
                created=True,
            )
            async def fake_cache_file(attachment, source_path, *, remove_source=False, enforce_quota=False):
                self.assertFalse(enforce_quota)
                if remove_source:
                    source_path.unlink(missing_ok=True)
                return stored

            cache_file = AsyncMock(side_effect=fake_cache_file)
            with (
                patch.object(maintenance, "DOWNLOADS_DIR", downloads),
                patch.object(maintenance, "list_all_cached_attachment_rows", new=AsyncMock(return_value=rows)),
                patch.object(maintenance, "cache_attachment_file", cache_file),
                patch.object(maintenance, "list_cached_attachment_local_paths", new=AsyncMock(return_value=set())),
            ):
                stats = await maintenance.migrate_legacy_attachment_cache()

            self.assertEqual(cache_file.await_count, 2)
            self.assertEqual(stats["inline_rows_migrated"], 2)
            self.assertEqual(
                [call.args[1] for call in cache_file.await_args_list],
                [first, second],
            )
            self.assertFalse(first.exists())
            self.assertFalse(second.exists())

    async def test_migration_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            downloads = Path(tmp) / "download"
            outside = Path(tmp) / "outside.bin"
            outside.write_bytes(b"secret")
            downloads.mkdir()
            link = downloads / "escape.bin"
            link.symlink_to(outside)
            with patch.object(maintenance, "DOWNLOADS_DIR", downloads):
                self.assertFalse(maintenance.is_safe_legacy_download_file(link))
```

- [ ] **Step 2: Run migration tests and verify failure**

```bash
cd backend
python -m unittest tests.test_attachment_cache_migration -v
```

Expected: FAIL because the maintenance module does not exist.

- [ ] **Step 3: Implement safe, idempotent legacy migration**

`is_safe_legacy_download_file()` must reject missing files, directories, symlinks and resolved paths outside `DOWNLOADS_DIR`.

`migrate_legacy_attachment_cache()` uses an `asyncio.Lock` and processes each row independently:

- valid shared hash + valid object path: skip;
- ordinary legacy local path: clear DB storage reference, then safely unlink legacy file;
- inline legacy local path: construct `CachedAttachment` from the row, then call `cache_attachment_file(attachment, Path(row["local_path"]), remove_source=True, enforce_quota=False)`;
- empty local path: skip;
- unsafe path: log warning and leave untouched.

After row processing, scan `DOWNLOADS_DIR` and delete only files absent from `list_cached_attachment_local_paths()`. Remove empty subdirectories bottom-up but retain `DOWNLOADS_DIR` itself.

Return counters for scanned rows, migrated inline rows, removed normal files, removed orphan files, skipped rows and failures.

- [ ] **Step 4: Implement shared object garbage collection**

`garbage_collect_attachment_cache()` must:

1. call `release_unreferenced_objects()` for every object-table row;
2. build the set of object-table SHA-256 values;
3. call `remove_stale_untracked_cache_files(known_hashes, time.time() - orphan_grace_seconds)`;
4. never traverse or delete `BACKUP_DIR`, `UPLOADS_DIR`, `CONFIG_DIR` or `LOGS_DIR`.

Implement `remove_stale_untracked_cache_files()` in `backend/services/attachment_cache.py`. It must acquire the same `_OBJECT_MUTATION_LOCK` used by writers and reference reclamation, then:

- scan only `ATTACHMENT_SHA256_DIR` for regular non-symlink hash files absent from `known_hashes` and older than `older_than`;
- scan only `ATTACHMENT_CACHE_TMP_DIR` for regular non-symlink `.tmp` and `.download` files older than `older_than`;
- reject resolved paths outside the respective roots;
- return `{"orphan_object_files": count, "orphan_object_bytes": bytes, "stale_temp_files": count}`.

- [ ] **Step 5: Implement lifecycle task**

Use one module-level task:

```python
async def _maintenance_loop():
    await migrate_legacy_attachment_cache()
    await garbage_collect_attachment_cache()
    while True:
        await asyncio.sleep(7 * 24 * 60 * 60)
        await garbage_collect_attachment_cache()
```

`start_attachment_cache_maintenance()` creates it only once. `stop_attachment_cache_maintenance()` cancels and awaits it, matching the project’s upload-cleanup lifecycle pattern.

- [ ] **Step 6: Wire the lifecycle into FastAPI startup and shutdown**

In `backend/main.py`, import the start/stop functions, start after `init_db()` and other lightweight services, and stop during lifespan shutdown. The migration itself remains a background task so `/api/health` is not blocked by hashing existing files.

- [ ] **Step 7: Run migration and lifecycle tests**

```bash
cd backend
python -m unittest tests.test_attachment_cache_migration tests.test_static_files -v
```

Expected: PASS.

- [ ] **Step 8: Commit Task 7**

```bash
git add backend/services/attachment_cache_maintenance.py backend/main.py backend/tests/test_attachment_cache_migration.py backend/tests/test_static_files.py
git diff --staged
git commit -m "🧹 新增附件旧缓存迁移与定期垃圾回收"
```

---

### Task 8: 完整回归、文档、版本、Docker 与生产迁移门槛

**Files:**

- Modify: `README.md`
- Modify: `VERSION`
- Modify: `package.json`, `frontend/package.json`, `docker-compose.yml`, README image tags；由于 DevSpace Shell 禁止脚本写入项目文件，使用 `DevSpace.edit` 按 `VERSION` 手动同步
- Review: `.env.example` remains unchanged
- Review: `scripts/docker-entrypoint.sh`
- Test: all backend and frontend tests

**Interfaces:**

- Final version: `0.0.18`
- Final image: `benxianyu/flymail:0.0.18`
- Production container remains `flymail`
- Production data remains `/Docker/flymail/data:/data`

- [ ] **Step 1: Run the complete backend suite before documentation and version changes**

```bash
cd backend
python -m unittest discover -s tests -v
```

Expected: all tests PASS. Fix root causes before proceeding; do not skip or weaken tests.

- [ ] **Step 2: Run the complete frontend suite and build**

```bash
cd frontend
npm install
npm test
npm run build
```

Expected: tests and build PASS. Record but do not opportunistically fix unrelated npm audit notices or existing bundle-size warnings.

- [ ] **Step 3: Update README behavior and storage documentation**

Document exactly:

- ordinary attachments are downloaded and cached on first click;
- inline images continue to download during body synchronization;
- default per-user ordinary attachment cache limit is 2 GB;
- `0` means unlimited and nonzero minimum is 100 MB;
- quota uses per-user distinct SHA-256 logical size;
- physical files are globally deduplicated under `/data/flymail/files/objects/sha256`;
- lowering the limit triggers immediate LRU cleanup;
- upgrade removes legacy ordinary attachment cache files but preserves metadata;
- offline accounts cannot fetch ordinary attachments that are not currently cached;
- `.env.example` has no new variable.

Update outdated README claims that history sync automatically downloads all attachments.

- [ ] **Step 4: Bump and synchronize version**

Use `DevSpace.edit` for exact replacements; do not run `npm run sync-version`, because DevSpace Shell commands may not write project files.

Set `VERSION` to:

```text
0.0.18
```

Synchronize these exact values with `DevSpace.edit`:

- root `package.json`: `"version": "0.0.18"`
- `frontend/package.json`: `"version": "0.0.18"`
- `docker-compose.yml`: `image: benxianyu/flymail:0.0.18`
- every README image reference: `benxianyu/flymail:0.0.18`

Then verify read-only:

```bash
cat VERSION
node -e "console.log(require('./package.json').version)"
node -e "console.log(require('./frontend/package.json').version)"
rg -n "benxianyu/flymail:0.0.18" docker-compose.yml README.md
```

Expected: all three version outputs are `0.0.18`; compose and README image tags use `benxianyu/flymail:0.0.18`.

- [ ] **Step 5: Run static project checks**

```bash
bash -n scripts/docker-entrypoint.sh
docker compose config
git diff --check
git status --short
git diff
```

Expected: all checks PASS. Confirm `.env.example` is unchanged and no secret, attachment payload, database backup or runtime log is staged.

- [ ] **Step 6: Build the Docker image**

```bash
docker build -t benxianyu/flymail:0.0.18 .
```

Expected: build succeeds without embedding passwords or session secrets in image metadata.

- [ ] **Step 7: Run an isolated temporary-container migration test**

Use a new directory under `/Docker/flymail/data/.verify-attachment-cache-<timestamp>` and a unique container name. Seed:

- two users;
- duplicate ordinary attachments;
- duplicate inline images;
- one unique ordinary attachment;
- one legacy orphan file;
- independent user limits.

Verify after startup migration:

1. container becomes healthy and `/api/health` reports `0.0.18`;
2. MySQL is 8.0, datadir is `/data/mysql/`, `log_bin=0`;
3. object directory exists;
4. ordinary legacy files are removed while metadata rows remain;
5. duplicate inline images point to one SHA-256 object;
6. same hash counts once for one user and separately for two users;
7. lowering one user’s limit evicts only that user’s ordinary references;
8. other users and inline references keep the shared object alive;
9. last-reference deletion removes the object;
10. restart preserves settings, hashes, references and object files;
11. logs contain no database password, mailbox credential, OAuth token or session secret;
12. stopping the container logs a safe MySQL shutdown.

Clean the temporary container and temporary data directory after successful verification.

- [ ] **Step 8: Collect production dry-run statistics without deleting data**

Before rebuilding production, collect and report:

```text
/data/flymail/files/download total bytes and file count
ordinary attachment referenced bytes and row count
inline image referenced bytes and row count
duplicate inline SHA-256 groups and estimated reclaimable bytes
cached_messages count
cached_attachments count
backup directory bytes and file count
```

Do not modify `/Docker/flymail/data` in this step. Present the expected ordinary-cache deletion and inline migration totals to the user and obtain the production migration confirmation required by the approved design.

- [ ] **Step 9: After explicit confirmation, rebuild production with rollback protection**

Preserve the current environment, port mapping, restart policy and `/Docker/flymail/data:/data` mount. Stop the old container safely, keep it under a rollback name, start `benxianyu/flymail:0.0.18`, and wait for health plus migration completion.

Do not manually delete ordinary attachments before the application migration. Let the tested migration service clear references and safely remove legacy files.

Verify counts for accounts, cached messages and attachment metadata before removing the rollback container. Restart once and recheck health, object references and settings.

- [ ] **Step 10: Final verification and commit**

```bash
cd backend && python -m unittest discover -s tests -v
cd ../frontend && npm test && npm run build
cd ..
bash -n scripts/docker-entrypoint.sh
docker compose config
git diff --check
git status --short
git diff
git add \
  README.md VERSION package.json frontend/package.json docker-compose.yml \
  backend/data_paths.py backend/models/__init__.py backend/db/__init__.py \
  backend/services/attachment_cache.py backend/services/attachment_cache_maintenance.py \
  backend/services/history_sync.py backend/services/mail_cache.py \
  backend/routes/messages.py backend/routes/settings.py backend/schemas.py backend/main.py \
  backend/tests/test_attachment_cache.py backend/tests/test_attachment_cache_routes.py \
  backend/tests/test_attachment_cache_migration.py backend/tests/test_attachment_storage_paths.py \
  backend/tests/test_history_sync_folders.py backend/tests/test_recent_mail_sync.py \
  backend/tests/test_message_folder_resolution.py backend/tests/test_mail_cache_folder_resolution.py \
  backend/tests/test_history_sync_progress.py backend/tests/test_static_files.py \
  frontend/src/utils/attachment-cache.ts frontend/tests/attachment-cache.test.ts \
  frontend/src/views/Settings.vue
git diff --staged
git commit -m "📦 发布附件按需缓存与全局去重版本"
```

Only stage files belonging to this feature. If earlier task commits already include implementation files, the final commit should contain only documentation, version and final integration adjustments.

- [ ] **Step 11: Push and verify remote state**

```bash
GIT_SSH_COMMAND='ssh -p 443 -o HostName=ssh.github.com' git push origin main
git status --short --branch
git rev-parse HEAD
GIT_SSH_COMMAND='ssh -p 443 -o HostName=ssh.github.com' git ls-remote origin refs/heads/main
```

Expected: working tree clean, local SHA equals remote `main` SHA. Do not run `docker login` or `docker push`.

## Final Acceptance Checklist

- [ ] Historical, recent and prefetched mail cache only normal attachment metadata; ordinary content is fetched on click.
- [ ] Inline images remain cached and CID URLs render through authenticated attachment routes.
- [ ] Shared object paths are SHA-256-derived and reject invalid or client-controlled paths.
- [ ] Identical normal attachments and inline images occupy one physical object globally.
- [ ] Objects remain until every normal and inline reference across all users is removed.
- [ ] Each user has a separate default 2048 MB ordinary attachment quota.
- [ ] `0` is unlimited; values 1–99 are rejected; values 100+ are accepted.
- [ ] Same-user duplicate hashes count once; cross-user references count separately.
- [ ] LRU clears all of one user’s ordinary references to an evicted hash without touching another user.
- [ ] Body and inline image data are excluded from quota.
- [ ] Existing ordinary legacy cache is removed only during confirmed production migration; metadata remains.
- [ ] Existing inline images migrate idempotently and deduplicate.
- [ ] Backup, upload, config, log, MySQL and remote mail data are untouched.
- [ ] Backend suite, frontend tests/build, shell syntax, Compose config, image build, isolated container and restart persistence all pass.
- [ ] MySQL remains 8.0 with `/data/mysql/` and `log_bin=0`.
- [ ] README matches actual behavior; `.env.example` remains unchanged.
- [ ] Final version is `0.0.18`, image is `benxianyu/flymail:0.0.18`, container is `flymail` and Docker Hub is not uploaded.
