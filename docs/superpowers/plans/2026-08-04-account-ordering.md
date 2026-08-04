# 邮箱全局排序实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在同步管理页通过“置顶 / 上移 / 下移”调整邮箱顺序，并把该顺序作为当前用户所有页面共享的全局账号顺序持久化。

**Architecture:** 复用 `accounts.sort_order`。后端数据库层负责新账号尾部排序、按排序字段查询和事务化批量重排；账号路由只接收当前用户完整账号 ID 序列。前端 Pinia 邮件仓库负责乐观更新与失败回滚，同步管理页只负责生成目标顺序并调用仓库动作。

**Tech Stack:** Python 3.12、FastAPI、Pydantic、aiomysql/MySQL 8.0、Vue 3、Pinia、TypeScript、Node test runner、Docker。

## Global Constraints

- 排序入口只在同步管理页现有“···”菜单中。
- 操作只有“置顶 / 上移 / 下移”，不实现拖拽和独立排序模式。
- 排序按用户隔离，不能修改其他用户账号。
- 当前已选择邮箱不因排序自动切换；只有无有效选择时使用第一项。
- 新邮箱默认排在当前用户最后。
- 不新增生产依赖，不新增数据库表，不删除或迁移 `/Docker/flymail/data`。
- 失败时前端恢复原顺序，数据库验证失败时不写入部分顺序。
- 版本从 `0.0.32` 升级到 `0.0.33`。

---

### Task 1: 数据库账号顺序与新账号尾部插入

**Files:**
- Modify: `backend/db/__init__.py`
- Create: `backend/tests/test_account_ordering.py`

**Interfaces:**
- Produces: `async def reorder_accounts(user_uid: str, account_ids: list[str]) -> bool`
- Changes: `get_accounts(user_uid)` 按 `sort_order ASC, created_at ASC, id ASC` 返回。
- Changes: `create_account(account)` 在事务中分配当前用户 `MAX(sort_order) + 1`，并更新传入 `Account.sort_order`。

- [ ] **Step 1: 写失败测试验证查询排序 SQL**

在 `backend/tests/test_account_ordering.py` 加载真实 `db` 模块并替换 `get_db()` 为记录 SQL 的假数据库。调用：

```python
accounts = await db_module.get_accounts("user-1")
```

断言执行 SQL 包含：

```text
WHERE user_uid = ? ORDER BY sort_order ASC, created_at ASC, id ASC
```

并验证返回列表保持假游标提供的排序。

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
cd backend
python -m unittest tests.test_account_ordering.AccountOrderingRepositoryTest.test_get_accounts_orders_by_saved_position -v
```

Expected: FAIL，因为当前查询只按 `created_at ASC` 排序。

- [ ] **Step 3: 最小修改 `get_accounts()`**

修改最终生效的第二个 `get_accounts()`：

```python
if user_uid:
    cursor = await db.execute(
        "SELECT * FROM accounts WHERE user_uid = ? "
        "ORDER BY sort_order ASC, created_at ASC, id ASC",
        (user_uid,),
    )
else:
    cursor = await db.execute(
        "SELECT * FROM accounts "
        "ORDER BY user_uid ASC, sort_order ASC, created_at ASC, id ASC"
    )
```

- [ ] **Step 4: 运行查询排序测试确认 GREEN**

Run the focused unittest from Step 2. Expected: PASS.

- [ ] **Step 5: 写失败测试验证新账号追加到最后**

假数据库让 `SELECT MAX(sort_order)` 返回 `4`。创建 `Account(sort_order=0)`，调用 `create_account(account)`，断言：

```python
self.assertEqual(account.sort_order, 5)
self.assertIn("sort_order", insert_sql)
self.assertEqual(insert_params[10], 5)
```

同时断言执行顺序包含 `BEGIN` 和 `COMMIT`。

- [ ] **Step 6: 运行新账号测试确认 RED**

Run:

```bash
cd backend
python -m unittest tests.test_account_ordering.AccountOrderingRepositoryTest.test_create_account_appends_after_existing_accounts -v
```

Expected: FAIL，因为当前 INSERT 不写 `sort_order`。

- [ ] **Step 7: 事务化实现新账号尾部插入**

在 `create_account()` 中先锁定当前用户行，使同一用户的并发添加串行分配顺序：

```python
await db.execute("BEGIN")
try:
    await db.execute(
        "SELECT id FROM users WHERE id = ? FOR UPDATE",
        (account.user_uid,),
    )
    cursor = await db.execute(
        "SELECT MAX(sort_order) FROM accounts WHERE user_uid = ? FOR UPDATE",
        (account.user_uid,),
    )
    row = await cursor.fetchone()
    current_max = int(row[0]) if row and row[0] is not None else -1
    account.sort_order = current_max + 1
    await db.execute(
        """INSERT INTO accounts
           (id, user_uid, email, provider, credentials_json, status,
            remark, group_name, hide_email, poll_interval_seconds, sort_order,
            created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (..., account.poll_interval_seconds, account.sort_order, account.created_at, account.updated_at),
    )
    await db.execute("COMMIT")
except Exception:
    await db.execute("ROLLBACK")
    raise
```

- [ ] **Step 8: 运行新账号测试确认 GREEN**

Run the focused unittest from Step 6. Expected: PASS.

- [ ] **Step 9: 写失败测试验证事务化批量重排与用户隔离**

添加两个测试：

```python
await reorder_accounts("user-1", ["b", "a", "c"])
```

应执行三个带 `WHERE id = ? AND user_uid = ?` 的更新并提交；提交重复、缺失、额外或其他用户 ID 时应返回 `False`，执行 `ROLLBACK`，且不执行 UPDATE。

- [ ] **Step 10: 运行批量重排测试确认 RED**

Run:

```bash
cd backend
python -m unittest \
  tests.test_account_ordering.AccountOrderingRepositoryTest.test_reorder_accounts_updates_complete_owned_sequence \
  tests.test_account_ordering.AccountOrderingRepositoryTest.test_reorder_accounts_rejects_invalid_sequence_without_updates -v
```

Expected: ERROR/FAIL，因为函数尚不存在。

- [ ] **Step 11: 实现 `reorder_accounts()`**

```python
async def reorder_accounts(user_uid: str, account_ids: list[str]) -> bool:
    db = await get_db()
    await db.execute("BEGIN")
    try:
        cursor = await db.execute(
            "SELECT id FROM accounts WHERE user_uid = ? "
            "ORDER BY sort_order ASC, created_at ASC, id ASC FOR UPDATE",
            (user_uid,),
        )
        owned_ids = [str(row[0]) for row in await cursor.fetchall()]
        if len(account_ids) != len(set(account_ids)) or set(account_ids) != set(owned_ids):
            await db.execute("ROLLBACK")
            return False
        now = time.time()
        await db.executemany(
            "UPDATE accounts SET sort_order = ?, updated_at = ? WHERE id = ? AND user_uid = ?",
            [(index, now, account_id, user_uid) for index, account_id in enumerate(account_ids)],
        )
        await db.execute("COMMIT")
        return True
    except Exception:
        await db.execute("ROLLBACK")
        raise
```

- [ ] **Step 12: 运行账号排序仓库测试**

Run:

```bash
cd backend
python -m unittest tests.test_account_ordering -v
```

Expected: PASS.

### Task 2: 当前用户批量排序 API

**Files:**
- Modify: `backend/schemas.py`
- Modify: `backend/routes/accounts.py`
- Modify: `backend/tests/test_account_update.py`

**Interfaces:**
- Consumes: `reorder_accounts(user_uid, account_ids)` from Task 1.
- Produces: `AccountOrderRequest(account_ids: list[str])`.
- Produces: `PUT /api/accounts/order` returning `{"success": true}`.

- [ ] **Step 1: 扩展账号路由测试桩并写失败测试**

在 `_load_accounts_route_module()` 的 DB stub 添加 `reorder_accounts`，schemas stub 添加 `AccountOrderRequest`。测试：

```python
body = types.SimpleNamespace(account_ids=["b", "a"])
result = await accounts.reorder_account_list(object(), body)
```

断言：

```python
accounts.reorder_accounts.assert_awaited_once_with("user-1", ["b", "a"])
self.assertEqual(result, {"success": True})
```

另测 DB 返回 `False` 时抛出 `AppError`，不返回成功。

- [ ] **Step 2: 运行路由测试确认 RED**

Run:

```bash
cd backend
python -m unittest \
  tests.test_account_update.AccountUpdateTest.test_reorder_accounts_saves_current_users_complete_order \
  tests.test_account_update.AccountUpdateTest.test_reorder_accounts_rejects_invalid_order -v
```

Expected: FAIL，因为 schema、import 和路由不存在。

- [ ] **Step 3: 增加请求 schema**

在 `AccountUpdateRequest` 前增加：

```python
class AccountOrderRequest(BaseModel):
    account_ids: List[str] = Field(min_length=1, description="当前用户完整邮箱账号 ID 顺序")
```

- [ ] **Step 4: 增加路由和数据库 import**

在 `backend/routes/accounts.py`：

```python
from db import (..., reorder_accounts, ...)
from schemas import (..., AccountOrderRequest, ...)

@router.put("/order", summary="保存邮箱账号顺序")
async def reorder_account_list(request: Request, body: AccountOrderRequest):
    uid = await get_uid(request)
    if not await reorder_accounts(uid, body.account_ids):
        raise AppError(400, "邮箱账号顺序无效，请刷新后重试")
    return {"success": True}
```

该静态 `/order` 路由必须放在 `/{account_id}/...` 路由之前，避免路径歧义。

- [ ] **Step 5: 运行路由测试确认 GREEN**

Run the focused tests from Step 2. Expected: PASS.

### Task 3: Pinia 乐观排序与失败回滚

**Files:**
- Modify: `frontend/src/stores/mail.ts`
- Modify: `frontend/tests/mail-store.test.mjs`

**Interfaces:**
- Produces: `async function saveAccountOrder(accountIds: string[]): Promise<boolean>`.
- Preserves: `currentAccountId` 不因顺序变化而自动切换。

- [ ] **Step 1: 写失败测试验证保存成功**

构造三个账号并替换：

```javascript
apiModule.default.put = async (url, body) => {
  assert.equal(url, '/accounts/order');
  assert.deepEqual(body, { account_ids: ['account-3', 'account-1', 'account-2'] });
  return { success: true };
};
```

调用 `await store.saveAccountOrder([...])`，断言内存、`sort_order` 和 `sessionStorage` 都为新顺序，且原 `currentAccountId` 不变。

- [ ] **Step 2: 运行成功路径测试确认 RED**

Run:

```bash
cd frontend
node --test tests/mail-store.test.mjs --test-name-pattern="saves account order"
```

Expected: FAIL，因为 `saveAccountOrder` 尚不存在。

- [ ] **Step 3: 写失败测试验证网络失败回滚**

让 `api.put` 抛出 `Error("offline")`，断言返回 `false`，账号数组和 `sessionStorage` 恢复原顺序，并触发 UI 错误提示。

- [ ] **Step 4: 实现 `saveAccountOrder()`**

在 store 内增加持久化 helper：

```typescript
function persistAccounts() {
  sessionStorage.setItem('flymail_accounts', JSON.stringify(accounts.value));
}
```

实现：

```typescript
async function saveAccountOrder(accountIds: string[]): Promise<boolean> {
  const original = accounts.value.map((account) => ({ ...account }));
  const byId = new Map(original.map((account) => [account.id, account]));
  if (accountIds.length !== original.length || new Set(accountIds).size !== original.length) return false;
  const reordered = accountIds.map((id, index) => {
    const account = byId.get(id);
    if (!account) return null;
    return normalizeAccount({ ...account, sort_order: index });
  });
  if (reordered.some((account) => account === null)) return false;
  accounts.value = reordered as MailAccount[];
  persistAccounts();
  try {
    await api.put('/accounts/order', { account_ids: accountIds });
    return true;
  } catch (error) {
    accounts.value = original.map(normalizeAccount);
    persistAccounts();
    uiStore.error('保存邮箱顺序失败');
    return false;
  }
}
```

把 `patchAccount()` 和 `loadAccounts()` 的直接 `sessionStorage.setItem` 改为复用 helper，并在 store return 中导出 `saveAccountOrder`。

- [ ] **Step 5: 运行 Pinia 排序测试确认 GREEN**

Run:

```bash
cd frontend
node --test tests/mail-store.test.mjs
```

Expected: PASS.

### Task 4: 同步管理菜单排序操作

**Files:**
- Modify: `frontend/src/views/HistorySync.vue`
- Modify: `frontend/tests/sync-card-actions.test.mjs`

**Interfaces:**
- Consumes: `mailStore.saveAccountOrder(accountIds)` from Task 3.
- Produces: menu actions `置顶`, `上移`, `下移`.

- [ ] **Step 1: 写失败的源代码契约测试**

扩展 `sync-card-actions.test.mjs`，断言：

```javascript
assert.match(source, />\s*置顶\s*</);
assert.match(source, />\s*上移\s*</);
assert.match(source, />\s*下移\s*</);
assert.match(source, /const mailStore = useMailStore\(\)/);
assert.match(source, /async function moveAccount\(accountId: string, direction: 'top' \| 'up' \| 'down'\)/);
assert.match(source, /await mailStore\.saveAccountOrder\(nextIds\)/);
```

并断言按钮禁用条件调用 `isFirstAccount(item.account_id)` 与 `isLastAccount(item.account_id)`。

- [ ] **Step 2: 运行契约测试确认 RED**

Run:

```bash
cd frontend
node --test tests/sync-card-actions.test.mjs --test-name-pattern="ordering"
```

Expected: FAIL，因为排序菜单尚不存在。

- [ ] **Step 3: 接入 mail store 和排序状态**

在 `HistorySync.vue`：

```typescript
import { useMailStore } from '../stores/mail';
const mailStore = useMailStore();
const orderSaving = ref(false);
```

`loadJobs()` 成功后按 `mailStore.accounts` 排序任务；若账号尚未加载则调用一次 `await mailStore.loadAccounts()`。实现：

```typescript
function orderJobs(items: HistorySyncItem[]) {
  const positions = new Map(mailStore.accounts.map((account, index) => [account.id, index]));
  return [...items].sort((a, b) => (positions.get(a.account_id) ?? Number.MAX_SAFE_INTEGER) - (positions.get(b.account_id) ?? Number.MAX_SAFE_INTEGER));
}
```

- [ ] **Step 4: 实现边界判断和移动函数**

```typescript
function accountIndex(accountId: string) {
  return mailStore.accounts.findIndex((account) => account.id === accountId);
}
function isFirstAccount(accountId: string) {
  return accountIndex(accountId) <= 0;
}
function isLastAccount(accountId: string) {
  const index = accountIndex(accountId);
  return index < 0 || index === mailStore.accounts.length - 1;
}
async function moveAccount(accountId: string, direction: 'top' | 'up' | 'down') {
  if (orderSaving.value) return;
  const ids = mailStore.accounts.map((account) => account.id);
  const index = ids.indexOf(accountId);
  if (index < 0) return;
  let target = direction === 'top' ? 0 : direction === 'up' ? index - 1 : index + 1;
  if (target < 0 || target >= ids.length || target === index) return;
  ids.splice(index, 1);
  ids.splice(target, 0, accountId);
  orderSaving.value = true;
  try {
    if (await mailStore.saveAccountOrder(ids)) jobs.value = orderJobs(jobs.value);
  } finally {
    orderSaving.value = false;
  }
}
```

- [ ] **Step 5: 在菜单中加入三个操作**

放在“刷新同步”之前：

```vue
<button type="button" role="menuitem" :disabled="orderSaving || isFirstAccount(item.account_id)" @click="runActionMenuCommand(() => moveAccount(item.account_id, 'top'))">置顶</button>
<button type="button" role="menuitem" :disabled="orderSaving || isFirstAccount(item.account_id)" @click="runActionMenuCommand(() => moveAccount(item.account_id, 'up'))">上移</button>
<button type="button" role="menuitem" :disabled="orderSaving || isLastAccount(item.account_id)" @click="runActionMenuCommand(() => moveAccount(item.account_id, 'down'))">下移</button>
<div class="job-action-separator" role="separator"></div>
```

增加语义化分隔线样式，不改变现有卡片布局。

- [ ] **Step 6: 排序成功后刷新任务**

`moveAccount()` 成功后调用 `await loadJobs()`，确保后端排序和卡片数据一致；失败时 store 已回滚，当前 jobs 顺序保持不变。

- [ ] **Step 7: 运行同步管理菜单测试确认 GREEN**

Run:

```bash
cd frontend
node --test tests/sync-card-actions.test.mjs
```

Expected: PASS.

### Task 5: 文档、版本与完整验证

**Files:**
- Modify: `README.md`
- Modify: `VERSION`
- Modify via script: `package.json`
- Modify via script: `frontend/package.json`
- Modify via script: `docker-compose.yml`

**Interfaces:**
- Produces: deployment version `0.0.33`.

- [ ] **Step 1: 更新 README**

在账号管理/同步管理相关说明中加入：同步管理邮箱卡片菜单可置顶、上移、下移；顺序按用户保存并用于邮件管理、默认发件邮箱、移动侧栏、备份和聚合收件箱；排序不改变当前已选择邮箱。

- [ ] **Step 2: 同步版本**

把 `VERSION` 改为 `0.0.33`，执行：

```bash
npm run sync-version
cat VERSION
node -e "console.log(require('./package.json').version)"
node -e "console.log(require('./frontend/package.json').version)"
```

Expected: 三处均为 `0.0.33`。

- [ ] **Step 3: 运行完整代码验证**

```bash
cd backend && python -m unittest discover -s tests -v
cd ../frontend && npm test && npm run build
cd ..
bash -n scripts/docker-entrypoint.sh
docker compose --env-file .env.example config
git diff --check
git status --short
git diff
```

Expected: 全部通过；只保留项目已有的大 chunk 和 npm audit 警告。

- [ ] **Step 4: 构建镜像**

```bash
docker build -t benxianyu/flymail:0.0.33 .
```

Expected: build exit 0.

- [ ] **Step 5: 临时容器验证**

使用 `/Docker/flymail/data/account-ordering-0.0.33-<timestamp>` 和独立容器名，配置包含引号、反斜杠、`@`、`:`、`/`、`%` 的测试数据库密码，验证：

1. 容器 healthy，`/api/health` 返回 `0.0.33`。
2. MySQL 8.0，数据目录 `/data/mysql/`。
3. `/data/flymail` 创建。
4. 数据库读写与重启持久化。
5. 测试用户三个账号的批量排序在容器重启后保持。
6. 日志密码脱敏，镜像元数据无密码和密钥。
7. SIGTERM 后 MySQL 安全关闭。
8. 清理临时容器与临时数据。

- [ ] **Step 6: 原位替换生产容器并真实排序回归**

保留当前 `flymail` 容器的端口、环境、restart policy 和 `/Docker/flymail/data:/data`。用 `benxianyu/flymail:0.0.33` 替换，失败时回滚旧镜像。达到 healthy 后使用真实登录会话：

1. 读取当前 `/api/accounts` 顺序。
2. 调用 `/api/accounts/order` 将一个非首位账号置顶。
3. 验证 `/api/accounts` 和 `/api/history-sync/jobs` 第一项一致。
4. 重启容器后再次验证顺序保持。
5. 把账号恢复为用户操作后的目标顺序，不做测试性随机排序遗留。

- [ ] **Step 7: 提交与推送**

确认 `.benchmarks/*.json` 仍不属于本次提交。仅暂存本次文件，执行：

```bash
git diff --check
git diff
git add <本次任务文件>
git diff --staged
git commit -m "✨ 新增同步管理邮箱全局排序"
git push origin main
```

Expected: `origin/main` 与本地新提交一致；不执行 Docker Hub 上传。
