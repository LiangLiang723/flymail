# 独立签名管理页面 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将当前写信页内不可用的嵌套签名编辑弹层替换为设置体系下的独立签名管理页面，同时保留写信页快速切换签名，并保证往返页面时未保存邮件内容不丢失。

**Architecture:** 新增签名类型、纯函数和独立 Pinia 仓库，统一承担签名加载、筛选、编辑草稿、CRUD、入口来源和未保存判断；`SignatureManagement.vue` 使用现有分栏页面模板编排完整管理界面。写信页通过 `mailStore` 保存当前浏览器会话内的写信工作区快照，再跳转管理页；`TiptapEditor` 改为单一显式点击下拉状态，从根因消除多个工具菜单同时展开的问题。

**Tech Stack:** Vue 3、Pinia、TypeScript、Tiptap 3、Node test runner、FastAPI、MySQL 8.0、Docker。

## Global Constraints

- 签名管理归入设置体系，视图键固定为 `signatures`，不加入主侧边栏一级导航。
- 设置页、用户菜单和写信页提供入口；从写信页进入时返回后必须恢复原写信工作区。
- 写信页只保留可用签名、当前选中状态、“无签名”和“管理签名”，不再嵌套富文本编辑器、模板网格、删除和默认规则设置。
- 富文本字体、字号、颜色、行距、表格和表情菜单任意时刻只能打开一个；点击外部、滚动或按 `Escape` 关闭。
- 继续复用现有 `/api/signatures` 接口、用户隔离、邮箱范围和两类默认规则；默认不改后端数据库结构。
- 不新增生产依赖，不删除或迁移现有签名，不修改 `/Docker/flymail/data` 的目录结构。
- 页面刷新不承诺保留尚未保存的写信内容；跨刷新持久化仍由“保存草稿”承担。
- 版本从 `0.0.34` 升级到 `0.0.35`。
- 当前工作区中的无关改动不得自动重置、覆盖、暂存或提交。

---

### Task 1: 建立签名类型与纯逻辑边界

**Files:**
- Create: `frontend/src/types/signature.ts`
- Create: `frontend/src/utils/signature-management.ts`
- Create: `frontend/tests/signature-management-utils.test.ts`
- Modify: `frontend/src/views/ComposeEmail.vue`（仅在本任务最后改为导入共享类型和默认解析函数）

**Interfaces:**
- Produces: `SignatureTemplate`
- Produces: `SignatureDraft`
- Produces: `SignatureEntrySource = 'compose' | 'settings' | 'menu'`
- Produces: `ComposeKind = 'new' | 'reply' | 'forward' | 'draft'`
- Produces: `createEmptySignatureDraft(accountId?: string): SignatureDraft`
- Produces: `createSignatureDraft(signature: SignatureTemplate): SignatureDraft`
- Produces: `duplicateSignatureDraft(signature: SignatureTemplate): SignatureDraft`
- Produces: `serializeSignatureDraft(draft: SignatureDraft): string`
- Produces: `filterSignatures(signatures, search, accountId): SignatureTemplate[]`
- Produces: `resolveDefaultSignature(signatures, accountId, composeKind): SignatureTemplate | null`

- [ ] **Step 1: 写失败测试覆盖默认规则、复制和筛选**

```ts
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  duplicateSignatureDraft,
  filterSignatures,
  resolveDefaultSignature,
  serializeSignatureDraft,
} from '../src/utils/signature-management.ts';

const signatures = [
  { id: 1, name: '全局', content_html: '<p>global</p>', account_id: '', is_default: true, is_reply_default: true },
  { id: 2, name: '工作', content_html: '<p>work</p>', account_id: 'account-1', is_default: true, is_reply_default: false },
];

test('account default overrides global default', () => {
  assert.equal(resolveDefaultSignature(signatures, 'account-1', 'new')?.id, 2);
  assert.equal(resolveDefaultSignature(signatures, 'account-2', 'new')?.id, 1);
  assert.equal(resolveDefaultSignature(signatures, 'account-1', 'draft'), null);
});

test('duplicate clears both default flags', () => {
  const draft = duplicateSignatureDraft(signatures[1]);
  assert.equal(draft.name, '工作 - 副本');
  assert.equal(draft.is_default, false);
  assert.equal(draft.is_reply_default, false);
  assert.equal(draft.id, null);
});

test('filter matches name and account scope', () => {
  assert.deepEqual(filterSignatures(signatures, '工作', 'account-1').map(item => item.id), [2]);
  assert.deepEqual(filterSignatures(signatures, '', '').map(item => item.id), [1]);
});

test('serialized draft changes when one form field changes', () => {
  const first = duplicateSignatureDraft(signatures[0]);
  const second = { ...first, content_html: '<p>changed</p>' };
  assert.notEqual(serializeSignatureDraft(first), serializeSignatureDraft(second));
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && node --test tests/signature-management-utils.test.ts`

Expected: FAIL，提示 `src/utils/signature-management.ts` 不存在。

- [ ] **Step 3: 创建共享类型**

```ts
export type SignatureEntrySource = 'compose' | 'settings' | 'menu';
export type ComposeKind = 'new' | 'reply' | 'forward' | 'draft';

export interface SignatureTemplate {
  id: number;
  name: string;
  content_html: string;
  account_id: string;
  is_default: boolean;
  is_reply_default: boolean;
}

export interface SignatureDraft {
  id: number | null;
  name: string;
  content_html: string;
  account_id: string;
  is_default: boolean;
  is_reply_default: boolean;
}
```

- [ ] **Step 4: 实现纯函数**

实现规则必须明确：

```ts
export function duplicateSignatureDraft(signature: SignatureTemplate): SignatureDraft {
  return {
    id: null,
    name: `${signature.name} - 副本`,
    content_html: signature.content_html,
    account_id: signature.account_id,
    is_default: false,
    is_reply_default: false,
  };
}
```

`filterSignatures()` 对名称执行不区分大小写包含匹配；`accountId === 'all'` 表示不过滤，空字符串表示只看“全部邮箱”。`resolveDefaultSignature()` 在草稿场景返回 `null`，其他场景先查账号专属默认，再查全局默认。

- [ ] **Step 5: 让 ComposeEmail 使用共享类型与默认解析函数**

删除页面内重复的 `UserSig` 和 `resolveDefaultSignature()` 实现，保留现有行为：

```ts
import type { ComposeKind, SignatureTemplate } from '../types/signature';
import { resolveDefaultSignature } from '../utils/signature-management';

const userSigs = ref<SignatureTemplate[]>([]);
const composeKind = ref<ComposeKind>('new');
```

- [ ] **Step 6: 运行测试与构建**

Run:

```bash
cd frontend
node --test tests/signature-management-utils.test.ts tests/compose-signature-contract.test.mjs
npm run build
```

Expected: 新测试通过，原签名契约测试通过，类型检查和构建通过。

- [ ] **Step 7: 提交**

```bash
git add frontend/src/types/signature.ts frontend/src/utils/signature-management.ts frontend/src/views/ComposeEmail.vue frontend/tests/signature-management-utils.test.ts
git commit -m "♻️ 提取签名管理共享逻辑"
```

---

### Task 2: 创建签名管理 Pinia 仓库

**Files:**
- Create: `frontend/src/stores/signatures.ts`
- Create: `frontend/tests/signature-store-contract.test.mjs`

**Interfaces:**
- Consumes: `SignatureTemplate`、`SignatureDraft`、`SignatureEntrySource` 与 Task 1 纯函数。
- Produces: `useSignatureStore()`。
- Produces state: `signatures`、`loaded`、`loading`、`saving`、`deleting`、`search`、`accountFilter`、`selectedId`、`draft`、`savedDraftSnapshot`、`entrySource`、`mobileEditing`。
- Produces computed: `filteredSignatures`、`selectedSignature`、`hasUnsavedChanges`、`signatureCount`。
- Produces actions: `loadSignatures()`、`ensureLoaded()`、`beginCreate(accountId?)`、`beginEdit(id)`、`beginDuplicate(id)`、`saveDraft()`、`deleteSelected()`、`discardDraft()`、`setEntrySource(source)`、`resetWorkspace()`。

- [ ] **Step 1: 写仓库失败契约测试**

测试必须检查：

```js
assert.match(source, /defineStore\('signatures'/);
assert.match(source, /api\.get\('\/signatures'\)/);
assert.match(source, /api\.post\('\/signatures'/);
assert.match(source, /api\.put\(`\/signatures\/\$\{draft\.value\.id\}`/);
assert.match(source, /api\.delete\(`\/signatures\/\$\{selectedId\.value\}`/);
assert.match(source, /const hasUnsavedChanges = computed/);
assert.match(source, /duplicateSignatureDraft/);
assert.match(source, /is_default:\s*false/);
assert.match(source, /is_reply_default:\s*false/);
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && node --test tests/signature-store-contract.test.mjs`

Expected: FAIL，仓库文件不存在。

- [ ] **Step 3: 实现加载、选中和编辑草稿状态**

`loadSignatures()` 成功后设置 `loaded = true`，保留仍存在的当前选中项；若不存在则选第一项；空列表时创建未持久化空白草稿但不自动打开移动编辑视图。`ensureLoaded()` 在 `loaded` 为 `false` 时调用 `loadSignatures()`，否则直接返回，供设置页和写信页共享计数与快速列表。

`beginEdit(id)` 必须先由页面处理未保存确认，仓库动作本身只切换状态：

```ts
function beginEdit(id: number) {
  const signature = signatures.value.find(item => item.id === id);
  if (!signature) return false;
  selectedId.value = id;
  draft.value = createSignatureDraft(signature);
  savedDraftSnapshot.value = serializeSignatureDraft(draft.value);
  mobileEditing.value = true;
  return true;
}
```

- [ ] **Step 4: 实现 CRUD**

保存请求体固定为：

```ts
const payload = {
  name: draft.value.name.trim(),
  content_html: draft.value.content_html,
  account_id: draft.value.account_id,
  is_default: draft.value.is_default,
  is_reply_default: draft.value.is_reply_default,
};
```

名称为空时抛出可显示错误，不发送请求。保存前记录相同 `account_id` 范围内旧的新邮件默认和回复默认 ID；保存并重新加载后，若本次开启某默认且旧默认为其他 ID，调用 `uiStore.info('已替换该范围原有默认签名')`。

删除必须由页面先确认。API 失败时不得清空 `draft`、`selectedId` 或列表。

- [ ] **Step 5: 实现搜索与筛选计算属性**

```ts
const filteredSignatures = computed(() => filterSignatures(
  signatures.value,
  search.value,
  accountFilter.value,
));
```

`accountFilter` 初始值为 `'all'`。

- [ ] **Step 6: 运行测试和构建**

Run:

```bash
cd frontend
node --test tests/signature-store-contract.test.mjs tests/signature-management-utils.test.ts
npm run build
```

Expected: 全部通过。

- [ ] **Step 7: 提交**

```bash
git add frontend/src/stores/signatures.ts frontend/tests/signature-store-contract.test.mjs
git commit -m "✨ 新增签名管理状态仓库"
```

---

### Task 3: 修复 Tiptap 工具栏下拉状态

**Files:**
- Modify: `frontend/src/components/TiptapEditor.vue`
- Create: `frontend/tests/tiptap-dropdown-contract.test.mjs`

**Interfaces:**
- Produces: `type ToolbarDropdown = 'fontFamily' | 'fontSize' | 'lineHeight' | 'color' | 'table' | 'emoji' | null`。
- Produces: `activeDropdown: Ref<ToolbarDropdown>`。
- Produces: `toggleDropdown(name)`、`closeDropdown()`、`runDropdownAction(action)`。

- [ ] **Step 1: 写失败契约测试复现截图问题**

测试检查旧 CSS 驱动方式被删除、显式状态存在：

```js
assert.match(source, /const activeDropdown = ref<ToolbarDropdown>\(null\)/);
assert.match(source, /function toggleDropdown/);
assert.match(source, /function closeDropdown/);
assert.match(source, /aria-expanded/);
assert.match(source, /@keydown\.escape/);
assert.doesNotMatch(source, /\.toolbar-dropdown:hover \.dropdown-menu/);
assert.doesNotMatch(source, /\.toolbar-dropdown:focus-within \.dropdown-menu/);
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && node --test tests/tiptap-dropdown-contract.test.mjs`

Expected: FAIL，当前仍依赖 `hover/focus-within`。

- [ ] **Step 3: 改为单一点击状态**

下拉按钮使用：

```vue
<button
  class="toolbar-btn"
  type="button"
  :aria-expanded="activeDropdown === btn.dropdownType"
  :aria-controls="`tiptap-${btn.dropdownType}-menu`"
  @click="toggleDropdown(btn.dropdownType)"
>
```

菜单使用 `v-if="activeDropdown === btn.dropdownType"`，表情菜单纳入同一状态，不再使用独立 `showEmojiPicker`。

- [ ] **Step 4: 实现关闭行为**

- 根元素增加 `ref="editorRoot"` 和 `@keydown.escape="closeDropdown"`。
- `window` 注册 `pointerdown`，点击 `editorRoot` 外关闭。
- `window` 以捕获模式注册 `scroll`，页面或祖先滚动时关闭。
- `onBeforeUnmount` 删除监听。
- 每个菜单动作通过 `runDropdownAction(() => ...)` 执行，动作完成后关闭并恢复编辑器焦点。

- [ ] **Step 5: 更新 CSS**

`.dropdown-menu` 不再 `display: none`；由 `v-if` 控制存在。保持 `position: absolute`，增加：

```css
.dropdown-menu {
  max-width: min(320px, calc(100vw - 24px));
  max-height: min(360px, calc(100vh - 96px));
  overflow-y: auto;
}
```

移动端菜单左右不得超出视口；工具栏继续允许换行。

- [ ] **Step 6: 运行测试与构建**

Run:

```bash
cd frontend
node --test tests/tiptap-dropdown-contract.test.mjs tests/compose-signature-contract.test.mjs
npm run build
```

Expected: 契约测试和构建通过。

- [ ] **Step 7: 提交**

```bash
git add frontend/src/components/TiptapEditor.vue frontend/tests/tiptap-dropdown-contract.test.mjs
git commit -m "🐛 修复富文本工具菜单重叠"
```

---

### Task 4: 实现独立签名管理页面

**Files:**
- Create: `frontend/src/views/SignatureManagement.vue`
- Create: `frontend/tests/signature-management-page.test.mjs`
- Modify: `frontend/src/styles/page-system.css`（只补充签名页共享页面规则无法覆盖的响应式样式）

**Interfaces:**
- Consumes: `useSignatureStore()`、`useMailStore().accounts`、`useUIStore().showConfirm()`、`TiptapEditor`。
- Emits: `back`，由 `App.vue` 根据 `entrySource` 决定返回目标。

- [ ] **Step 1: 写页面失败契约测试**

测试检查：

```js
assert.match(source, /<PageFrame[^>]*template="split"[^>]*width="fluid"/);
assert.match(source, /签名管理/);
assert.match(source, /新建签名/);
assert.match(source, /搜索签名/);
assert.match(source, /全部邮箱/);
assert.match(source, /新邮件默认/);
assert.match(source, /回复\/转发默认/);
assert.match(source, /<TiptapEditor/);
assert.match(source, /beginDuplicate/);
assert.match(source, /beforeunload/);
assert.match(source, /showConfirm/);
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && node --test tests/signature-management-page.test.mjs`

Expected: FAIL，页面不存在。

- [ ] **Step 3: 创建桌面分栏骨架**

页面结构固定为：

```vue
<PageFrame template="split" width="fluid" class="signature-management-page ui-page">
  <template #header>
    <PageHeader title="签名管理" description="管理不同邮箱的新邮件和回复签名。">
      <template #actions>
        <UiButton variant="primary" @click="requestCreate">新建签名</UiButton>
      </template>
    </PageHeader>
  </template>
  <div class="signature-workspace split-grid">
    <aside class="signature-list-pane ui-scroll-region ui-scroll-region--y">...</aside>
    <section class="signature-editor-pane ui-scroll-region ui-scroll-region--y">...</section>
  </div>
</PageFrame>
```

顶部另提供明确的返回按钮，不能依赖浏览器后退。

- [ ] **Step 4: 实现左栏**

左栏包含搜索框、邮箱筛选和稳定顺序列表。每项显示名称、邮箱范围和 `UiBadge`：

- 新邮件默认：`tone="accent"`。
- 回复/转发默认：`tone="success"`。
- 空正文：`tone="warning"`，文字“无正文”。

无数据时使用 `UiEmptyState` 和“创建第一个签名”。加载时使用 `UiLoadingState`。

- [ ] **Step 5: 实现新建模板起点**

模板常量只位于页面文件中，键固定为：`blank`、`business`、`contact`、`brand`、`minimal`。使用以下实际起始内容：

```ts
const signatureTemplates = [
  { key: 'blank', name: '空白签名', content_html: '<p><br></p>' },
  { key: 'business', name: '简洁商务', content_html: '<p><strong>姓名</strong></p><p>职位 · 公司</p><p>name@example.com · 138 0000 0000</p>' },
  { key: 'contact', name: '联系方式', content_html: '<p>姓名</p><p><a href="mailto:name@example.com">name@example.com</a> · 138 0000 0000</p>' },
  { key: 'brand', name: '品牌卡片', content_html: '<div style="border-left:3px solid #6c63ff;padding-left:12px"><p><strong>姓名</strong></p><p>公司名称 · 职位</p><p>品牌标语</p></div>' },
  { key: 'minimal', name: '极简落款', content_html: '<p>— 姓名</p>' },
];
```

点击模板调用 `signatureStore.beginCreate()` 后填充 `draft.content_html`；模板不会直接进入写信菜单。

- [ ] **Step 6: 实现编辑表单与操作条**

字段：名称、适用邮箱、两类默认复选框、富文本正文。底部操作：删除、复制、取消更改、保存。

- 保存使用 `signatureStore.saveDraft()`。
- 复制使用 `signatureStore.beginDuplicate(selectedId)`，不立即请求后端，用户确认名称后保存。
- 删除先调用：

```ts
const confirmed = await uiStore.showConfirm({
  title: '删除签名',
  message: `确定删除“${signatureStore.selectedSignature?.name}”吗？`,
  confirmText: '删除',
  danger: true,
});
```

- [ ] **Step 7: 实现未保存保护**

封装 `confirmDiscardChanges(): Promise<boolean>`。切换列表项、新建、页面返回和移动端返回列表都先调用。注册：

```ts
function handleBeforeUnload(event: BeforeUnloadEvent) {
  if (!signatureStore.hasUnsavedChanges) return;
  event.preventDefault();
  event.returnValue = '';
}
```

挂载时添加，卸载时移除。

- [ ] **Step 8: 实现移动端列表/编辑视图**

宽度 `<= 768px`：

- `mobileEditing === false` 只显示列表。
- 选中或新建后只显示编辑区。
- 编辑区顶部显示“返回列表”、保存和更多操作。
- 不使用 `.modal-overlay`、居中弹窗或底部抽屉。

- [ ] **Step 9: 运行测试和构建**

Run:

```bash
cd frontend
node --test tests/signature-management-page.test.mjs tests/signature-store-contract.test.mjs
npm run build
```

Expected: 通过。

- [ ] **Step 10: 提交**

```bash
git add frontend/src/views/SignatureManagement.vue frontend/src/styles/page-system.css frontend/tests/signature-management-page.test.mjs
git commit -m "✨ 新增独立签名管理页面"
```

---

### Task 5: 注册视图、入口与全局离开保护

**Files:**
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/components/app/UserMenu.vue`
- Modify: `frontend/src/components/AppIcon.vue`
- Modify: `frontend/src/views/Settings.vue`
- Create: `frontend/tests/signature-navigation-contract.test.mjs`

**Interfaces:**
- Consumes: `SignatureEntrySource`、`useSignatureStore()`。
- Produces: `requestNavigation(target: string, source?: SignatureEntrySource): Promise<boolean>`。
- Produces: `SignatureManagement @back="returnFromSignatureManagement"`。

- [ ] **Step 1: 写导航失败契约测试**

测试必须确认：

- `App.vue` 导入并渲染 `SignatureManagement`。
- `menuViews` 包含 `signatures`。
- `navItems` 不包含 `signatures`。
- `UserMenu.vue` 有“签名管理”。
- `Settings.vue` 有“邮件签名”入口卡片。
- 离开签名页时读取 `signatureStore.hasUnsavedChanges` 并调用 `uiStore.showConfirm()`。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && node --test tests/signature-navigation-contract.test.mjs`

Expected: FAIL。

- [ ] **Step 3: 在 App.vue 注册页面**

```vue
<SignatureManagement
  v-else-if="currentView === 'signatures'"
  @back="returnFromSignatureManagement"
/>
```

`menuViews` 加入 `signatures`，但 `navItems` 保持原样。

- [ ] **Step 4: 统一导航入口**

所有用户触发的页面切换都进入 `requestNavigation()`，包括 `navigateFromSidebar()`、用户菜单、设置入口、`handleNavigate()` 和 `openNotification()`；不能直接赋值绕过签名页未保存保护。`requestNavigation()` 在完成切换时返回 `true`，用户取消时返回 `false`。`openNotification()` 必须先获得 `true`，再修改当前邮箱、文件夹和待打开邮件状态。`currentView` 的现有 `watch` 只保留合法性校验，不承担异步离开确认。离开签名页时提示：

```ts
const confirmed = await uiStore.showConfirm({
  title: '放弃未保存的签名更改？',
  message: '当前签名尚未保存，离开后这些更改将丢失。',
  confirmText: '放弃更改',
  danger: true,
});
```

确认后调用 `signatureStore.discardDraft()` 再切换。

- [ ] **Step 5: 增加入口来源**

- 设置页入口先 `signatureStore.setEntrySource('settings')`，再派发 `flymail-navigate`。
- 用户菜单进入时来源为 `menu`。
- `returnFromSignatureManagement()`：来源为 `compose` 返回 `compose`，其他来源返回 `settings`。

- [ ] **Step 6: 添加签名图标**

在 `AppIcon.vue` 增加 `name === 'signature'` 的钢笔路径；用户菜单和设置入口统一使用该图标，避免复制内联 SVG。

- [ ] **Step 7: 设置页增加入口卡片**

卡片位于外观和聚合收件箱之后，内容：

- 标题“邮件签名”。
- 说明“按邮箱管理新邮件和回复/转发默认签名”。
- 状态“已创建 {{ signatureStore.signatureCount }} 个签名”。
- 按钮“管理签名”。

`Settings.vue` 挂载时调用 `signatureStore.ensureLoaded()`，只读取共享仓库中的计数，不维护签名列表副本，也不嵌入编辑器。

- [ ] **Step 8: 运行相关测试与构建**

Run:

```bash
cd frontend
node --test tests/signature-navigation-contract.test.mjs tests/product-ui-redesign.test.mjs tests/ui-layout.test.mjs
npm run build
```

Expected: 通过。

- [ ] **Step 9: 提交**

```bash
git add frontend/src/App.vue frontend/src/components/app/UserMenu.vue frontend/src/components/AppIcon.vue frontend/src/views/Settings.vue frontend/tests/signature-navigation-contract.test.mjs
git commit -m "✨ 接入签名管理页面入口"
```

---

### Task 6: 保存和恢复写信工作区快照

**Files:**
- Modify: `frontend/src/stores/mail.ts`
- Modify: `frontend/src/views/ComposeEmail.vue`
- Modify: `frontend/tests/compose-signature-contract.test.mjs`
- Create: `frontend/tests/compose-workspace.test.ts`

**Interfaces:**
- Produces: `ComposeAttachmentSnapshot`。
- Produces: `ComposeWorkspaceSnapshot`。
- Produces store state: `composeWorkspace`。
- Produces actions: `saveComposeWorkspace(snapshot)`、`clearComposeWorkspace()`。
- Consumes: `SignatureEntrySource = 'compose'` 和 App 的 `flymail-navigate` 事件。

- [ ] **Step 1: 写失败测试覆盖完整快照字段**

```ts
const snapshot = {
  account_id: 'account-1',
  to: ['to@example.com'],
  cc: ['cc@example.com'],
  bcc: ['bcc@example.com'],
  subject: 'subject',
  body_html: '<p>body</p>',
  attachments: [{ filename: 'a.pdf', size: 10, path: '/upload/a.pdf', source: 'local' as const }],
  draft_message_id: 'draft-1',
  draft_folder: 'Drafts',
  compose_kind: 'reply' as const,
  show_cc: true,
  show_bcc: true,
  active_signature_id: 2,
};
```

测试源码契约确认 store 暴露保存和清除动作；Compose 的恢复路径包含 `applyDefaultSignature: false` 或等价显式参数，避免返回后再次插入默认签名。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && node --test tests/compose-workspace.test.ts tests/compose-signature-contract.test.mjs`

Expected: FAIL，快照接口不存在。

- [ ] **Step 3: 在 mailStore 增加会话内快照**

```ts
export interface ComposeWorkspaceSnapshot {
  account_id: string;
  to: string[];
  cc: string[];
  bcc: string[];
  subject: string;
  body_html: string;
  attachments: ComposeAttachmentSnapshot[];
  draft_message_id: string;
  draft_folder: string;
  compose_kind: ComposeKind;
  show_cc: boolean;
  show_bcc: boolean;
  active_signature_id: number | null;
}
```

仅存 Pinia 内存，不写 `localStorage` 或 `sessionStorage`，符合“页面刷新不承诺保留”的边界。

- [ ] **Step 4: ComposeEmail 生成快照**

新增：

```ts
function buildComposeWorkspaceSnapshot(): ComposeWorkspaceSnapshot {
  commitRecipientInputs();
  return {
    account_id: fromAccountId.value,
    to: [...toList.value],
    cc: [...ccList.value],
    bcc: [...bccList.value],
    subject: subject.value,
    body_html: bodyHtml.value,
    attachments: attachments.value.map(item => ({ ...item })),
    draft_message_id: draftMessageId.value,
    draft_folder: draftFolder.value,
    compose_kind: composeKind.value,
    show_cc: showCc.value,
    show_bcc: showBcc.value,
    active_signature_id: activeSignatureId.value,
  };
}
```

- [ ] **Step 5: 进入签名页前保存快照**

`openSignatureManager()` 改为：

```ts
mailStore.saveComposeWorkspace(buildComposeWorkspaceSnapshot());
signatureStore.setEntrySource('compose');
showSignaturePanel.value = false;
window.dispatchEvent(new CustomEvent('flymail-navigate', { detail: 'signatures' }));
```

- [ ] **Step 6: 恢复快照且不重新应用默认签名**

`applyComposeDraft()` 增加第二参数：

```ts
async function applyComposeDraft(
  draft: Partial<ComposeWorkspaceSnapshot> | null = null,
  options: { applyDefaultSignature?: boolean } = {},
) {
```

挂载顺序：

1. `await loadUserSigs()`。
2. 若 `mailStore.composeWorkspace` 存在，恢复全部字段和附件，`applyDefaultSignature: false`。
3. 否则消费现有 `composeDraft`，保持新建、回复、转发和草稿原逻辑。

恢复后不清除 `composeWorkspace`；再次进入签名页时覆盖为最新快照。

- [ ] **Step 7: 在正确时机清除快照**

以下路径调用 `clearComposeWorkspace()`：

- 发送成功。
- 用户明确确认放弃邮件。
- 加载新的 `composeDraft`（回复、转发、打开另一草稿或新建另一封邮件）之前。

保存草稿成功不清除，因为用户仍可能继续编辑当前邮件。

- [ ] **Step 8: 运行测试和构建**

Run:

```bash
cd frontend
node --test tests/compose-workspace.test.ts tests/compose-signature-contract.test.mjs
npm run build
```

Expected: 通过。

- [ ] **Step 9: 提交**

```bash
git add frontend/src/stores/mail.ts frontend/src/views/ComposeEmail.vue frontend/tests/compose-workspace.test.ts frontend/tests/compose-signature-contract.test.mjs
git commit -m "✨ 保留签名管理往返写信状态"
```

---

### Task 7: 精简写信页签名快速菜单

**Files:**
- Modify: `frontend/src/views/ComposeEmail.vue`
- Modify: `frontend/src/styles/page-system.css`
- Modify: `frontend/tests/compose-signature-contract.test.mjs`
- Modify: `frontend/tests/mail-reading-layout.test.mjs`

**Interfaces:**
- Consumes: `useSignatureStore().signatures` 与共享 `resolveDefaultSignature()`。
- Produces: 快速签名菜单，不产生 CRUD 或编辑表单状态。

- [ ] **Step 1: 更新失败契约测试**

新增断言：

```js
assert.match(source, /当前签名/);
assert.match(source, /无签名/);
assert.match(source, /管理签名/);
assert.doesNotMatch(source, /内置模板/);
assert.doesNotMatch(source, /showCustomizeDialog/);
assert.doesNotMatch(source, /showEditUserSigDialog/);
assert.doesNotMatch(source, /editingUserSigHtml/);
assert.doesNotMatch(source, /<TiptapEditor[^>]*class="signature-editor"/);
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && node --test tests/compose-signature-contract.test.mjs`

Expected: FAIL，当前仍含内嵌编辑器和模板。

- [ ] **Step 3: 删除写信页签名管理状态和函数**

删除：内置模板常量、自定义模板弹窗、用户签名编辑弹窗、页面内创建/修改/删除 API、相关 CSS。保留：加载签名、可用签名过滤、默认解析、`selectSignature()` 和 `insertSigToEditor()`。

签名加载优先复用 `signatureStore.loadSignatures()`；写信页不再维护第二份独立 CRUD 状态。

- [ ] **Step 4: 实现快速菜单**

菜单顺序：

1. 当前签名摘要。
2. “无签名”。
3. 当前发件邮箱可用签名列表。
4. 分隔线。
5. “管理签名”。

列表项显示名称、邮箱范围和默认徽标；空正文签名可显示但点击后只移除已有托管签名块，并提示“该签名没有正文”。

- [ ] **Step 5: 维护当前选中状态**

`TiptapEditor.setManagedSignature()` 之外，Compose 维护 `activeSignatureId: number | null`。应用默认、手动选择和无签名时同步更新；切换发件邮箱应用新默认时也更新。

- [ ] **Step 6: 清理旧样式并保持菜单可用**

签名下拉只需要单列列表，宽度约 `300px`；移除卡片网格、预览缩放、内嵌 modal 和移动端编辑弹窗样式。移动端快速菜单可保留底部抽屉，但不承载编辑器。

- [ ] **Step 7: 运行前端完整测试和构建**

Run:

```bash
cd frontend
npm test
npm run build
```

Expected: 现有全部前端测试通过，生产构建通过。

- [ ] **Step 8: 提交**

```bash
git add frontend/src/views/ComposeEmail.vue frontend/src/styles/page-system.css frontend/tests/compose-signature-contract.test.mjs frontend/tests/mail-reading-layout.test.mjs
git commit -m "♻️ 精简写信页签名快速选择"
```

---

### Task 8: 浏览器回归验证并修复真实交互问题

**Files:**
- Modify only if browser verification exposes a reproducible issue: `frontend/src/views/SignatureManagement.vue`、`frontend/src/components/TiptapEditor.vue`、`frontend/src/views/ComposeEmail.vue` and their focused tests.

**Interfaces:**
- Uses: local `flymail` container or an isolated test container and browser automation.

- [ ] **Step 1: 构建当前前端并启动可访问环境**

Run:

```bash
cd frontend
npm run build
```

若使用 Docker，必须挂载独立临时数据目录，不得使用 `/Docker/flymail/data` 做测试数据。

- [ ] **Step 2: 使用桌面视口复现原截图路径**

验证步骤：

1. 进入“签名管理”。
2. 新建签名并打开富文本编辑器。
3. 依次点击字体、字号、颜色、行距、表格和表情。
4. 每次只允许当前菜单可见。
5. 点击另一菜单时前一菜单立即关闭。
6. 点击编辑器外部、滚动页面和按 `Escape` 均关闭菜单。
7. 无菜单被裁剪，没有水平页面溢出。

- [ ] **Step 3: 验证 CRUD 和默认规则**

创建两个同邮箱范围签名，先后设为新邮件默认，确认列表最终只有后保存项显示该徽标；回复默认同理。验证复制后默认标识均关闭；删除失败模拟下编辑内容和选中状态不丢失。

- [ ] **Step 4: 验证写信往返**

填写发件邮箱、收件人、抄送、密送、主题、正文并添加一个测试附件；进入签名管理后返回，逐项确认内容、附件、草稿标识和展开状态保持，且正文中签名只出现一次。

- [ ] **Step 5: 验证移动端**

视口宽度设为 `390px`：列表和编辑视图分离，富文本编辑器占满页面，不出现居中弹窗或底部编辑抽屉，菜单不超出视口。

- [ ] **Step 6: 若发现问题，先补失败测试再最小修复**

不得只调整截图表现；每个问题必须有可复现测试或浏览器步骤，并重新执行 Task 3、4、6、7 的相关测试。

- [ ] **Step 7: 提交浏览器回归修复（仅有代码变更时）**

```bash
git add frontend/src/views/SignatureManagement.vue frontend/src/components/TiptapEditor.vue frontend/src/views/ComposeEmail.vue frontend/tests/signature-management-page.test.mjs frontend/tests/tiptap-dropdown-contract.test.mjs frontend/tests/compose-signature-contract.test.mjs
git commit -m "🐛 修复签名管理页面交互细节"
```

---

### Task 9: 文档、版本与完整交付验证

**Files:**
- Modify: `VERSION`
- Modify via sync script: `package.json`
- Modify via sync script: `frontend/package.json`
- Modify via sync script: `docker-compose.yml`
- Modify: `README.md`

- [ ] **Step 1: 更新版本并同步**

将 `VERSION` 改为：

```text
0.0.35
```

Run: `npm run sync-version`

检查：

```bash
cat VERSION
node -e "console.log(require('./package.json').version)"
node -e "console.log(require('./frontend/package.json').version)"
rg -n "benxianyu/flymail:0.0.35" docker-compose.yml README.md
```

Expected: 三个版本均为 `0.0.35`，Compose 和 README 镜像标签一致。

- [ ] **Step 2: 更新 README**

记录：

- 签名管理入口位于设置体系的独立页面。
- 写信页只做快速切换。
- 从写信页进入管理页会保留当前浏览器会话内编辑状态。
- 页面刷新仍需依靠保存草稿。
- 默认规则按用户和邮箱范围隔离。

明确本次无新增环境变量，`.env.example` 无需改动。

- [ ] **Step 3: 执行完整测试与静态检查**

Run:

```bash
cd backend
python -m unittest discover -s tests -v
cd ../frontend
npm test
npm run build
cd ..
bash -n scripts/docker-entrypoint.sh
if [[ -f .env ]]; then
  docker compose config
else
  (cp .env.example .env; trap 'rm -f .env' EXIT; docker compose config)
fi
git diff --check
git status --short
git diff
```

Expected: 后端全部通过；前端全部通过；构建、Shell、Compose 和 diff 检查通过。

- [ ] **Step 4: 构建本地镜像**

Run:

```bash
docker build -t benxianyu/flymail:0.0.35 .
```

Expected: 构建成功。默认不执行 `docker login` 或 `docker push`。

- [ ] **Step 5: 使用独立临时容器验证**

至少验证：

1. 容器达到 `healthy`。
2. `/api/health` 返回 `0.0.35`。
3. MySQL 为 8.0，数据目录为 `/data/mysql/`。
4. `/data/flymail` 正常创建。
5. 数据库可读写。
6. 重启后测试数据存在。
7. 日志中的数据库密码脱敏。
8. 镜像元数据无密码或会话密钥。
9. SIGTERM 停止时 MySQL 安全关闭。

临时数据目录和容器名必须独立，结束后清理，不得触碰 `/Docker/flymail/data`。

- [ ] **Step 6: 替换当前 flymail 容器**

替换前只读确认当前容器的端口、网络、重启策略、环境变量名称和 `/Docker/flymail/data:/data` 挂载。保留可回滚旧容器，启动新容器并验证健康、版本和数据计数；确认无误后删除回滚容器。

- [ ] **Step 7: 完成最终提交**

```bash
git status --short
git diff --check
git add VERSION package.json frontend/package.json docker-compose.yml README.md
git diff --staged
git commit -m "🚀 发布独立签名管理页面"
```

- [ ] **Step 8: 推送并确认远端**

```bash
git push origin main
git rev-parse HEAD
git ls-remote origin refs/heads/main
```

若 22 端口不可用，改用：

```bash
GIT_SSH_COMMAND='ssh -p 443 -o HostName=ssh.github.com' git push origin main
```

Expected: 本地和远端 SHA 一致。

- [ ] **Step 9: 最终运行状态复核**

确认输出包含：

```text
最终部署版本：0.0.35
镜像：benxianyu/flymail:0.0.35
容器：flymail
运行状态：运行正常
健康检查：已通过
```

同时报告后端测试数、前端测试数、构建结果、MySQL 版本、数据挂载与持久化结果、提交 SHA、推送结果、Docker Hub 未上传，以及仍需真实邮箱客户端验证富文本签名最终渲染的风险。
