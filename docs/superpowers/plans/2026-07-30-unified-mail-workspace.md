# FlyMail 高密度统一邮件工作台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 FlyMail 重构为统一的高密度桌面邮件工作台，修复页面滚动条消失、页面半截、侧栏高度不一致和页面模板割裂，同时保留现有邮件业务逻辑与 `/data` 数据。

**Architecture:** 先建立唯一的视口高度链路和滚动责任，再提供可复用的页面布局组件与四类页面模板。各业务页面只保留业务状态和局部内容结构，不再自行决定全局 `height`、`padding` 和 `overflow`；旧 `page-system.css` 从覆盖层收敛为模板样式入口。

**Tech Stack:** Vue 3、TypeScript、Pinia、CSS Custom Properties、Node.js test runner、Vite、FastAPI、Docker。

## Global Constraints

- 工作区固定为 `/home/chatgpt/flymail`，分支固定为 `main`。
- 不修改认证方式、数据库结构、用户隔离和邮件业务接口。
- 不修改 `/Docker/flymail/data:/data` 持久化挂载。
- 不新增或升级生产依赖。
- 不上传 Docker Hub。
- 应用壳使用 `100dvh`，保留 `100vh` 回退；外层不滚动。
- 普通页面只有一个主滚动区域；工作台和分栏页只允许明确的面板滚动。
- 主侧栏展开 248px、折叠 72px，始终贯穿完整视口。
- 桌面控件采用高密度尺寸：按钮 36–38px、输入框 36px、工具栏 48px、列表行 44–52px。
- 页面标准内边距 20px，紧凑页面 16px，面板间距 12px。
- 所有新增样式使用语义 token；不在业务页面新增十六进制颜色。
- 支持 `prefers-reduced-motion`、`prefers-reduced-transparency` 和 `prefers-contrast`。
- 版本从 `0.0.13` 更新到 `0.0.14`。

---

### Task 1: 建立唯一的视口高度链路和滚动契约

**Files:**
- Modify: `frontend/src/styles/base.css`
- Modify: `frontend/src/styles/app-shell.css`
- Modify: `frontend/src/App.vue`
- Modify: `frontend/tests/ui-layout.test.mjs`
- Create: `frontend/tests/page-templates.test.mjs`

**Interfaces:**
- Produces: `.app-page-viewport`，作为所有认证后页面的唯一挂载视口。
- Produces: 全局高度链 `html > body > #app > .app-shell > .main > .content > .app-page-viewport`。
- Consumes: 现有 `AppSidebar`、动态页面组件和移动导航触发器。

- [ ] **Step 1: Write the failing viewport and scroll contract tests**

在 `frontend/tests/page-templates.test.mjs` 写入：

```js
import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (file) => readFile(path.join(root, file), 'utf8');

test('the authenticated shell owns one complete viewport height chain', async () => {
  const base = await read('src/styles/base.css');
  const shell = await read('src/styles/app-shell.css');
  const app = await read('src/App.vue');

  assert.match(base, /html[\s\S]*body[\s\S]*#app[\s\S]*height:\s*100%/);
  assert.match(base, /body\s*\{[^}]*overflow:\s*hidden/s);
  assert.match(shell, /\.app-shell\s*\{[^}]*height:\s*100dvh/s);
  assert.match(shell, /\.main[\s\S]*\.content[\s\S]*height:\s*100%/);
  assert.match(app, /class="app-page-viewport"/);
});

test('the page viewport clips outer overflow and delegates scrolling to templates', async () => {
  const shell = await read('src/styles/app-shell.css');
  assert.match(shell, /\.app-page-viewport\s*\{[^}]*min-height:\s*0/s);
  assert.match(shell, /\.app-page-viewport\s*\{[^}]*overflow:\s*hidden/s);
});
```

- [ ] **Step 2: Run the new test and verify RED**

Run: `cd frontend && node --test tests/page-templates.test.mjs`

Expected: FAIL because `.app-page-viewport` and the complete height chain do not exist.

- [ ] **Step 3: Implement the complete height chain**

Update `frontend/src/styles/base.css` with:

```css
html,
body,
#app {
  width: 100%;
  height: 100%;
  min-height: 0;
}

body {
  margin: 0;
  overflow: hidden;
}
```

Update `.app-shell` in `frontend/src/styles/app-shell.css`:

```css
.app-shell {
  width: 100%;
  height: 100vh;
  height: 100dvh;
  min-height: 0;
  display: grid;
  overflow: hidden;
}
```

Update `.main` and `.content`:

```css
.main,
.content,
.app-page-viewport {
  min-width: 0;
  min-height: 0;
  height: 100%;
  overflow: hidden;
}
```

- [ ] **Step 4: Add the explicit page viewport wrapper**

In `frontend/src/App.vue`, wrap the current dynamic page content inside:

```vue
<div class="content">
  <div class="app-page-viewport">
    <!-- existing current-page component branches -->
  </div>
</div>
```

Do not change page selection, stores, API calls or emitted events.

- [ ] **Step 5: Run Task 1 tests and frontend build**

Run: `cd frontend && node --test tests/page-templates.test.mjs tests/ui-layout.test.mjs && npm run build`

Expected: all selected tests pass and Vite build succeeds.

- [ ] **Step 6: Commit Task 1**

```bash
git add frontend/src/styles/base.css frontend/src/styles/app-shell.css frontend/src/App.vue frontend/tests/ui-layout.test.mjs frontend/tests/page-templates.test.mjs
git commit -m "🐛 统一视口高度并恢复页面滚动基础"
```

---

### Task 2: 创建统一页面布局组件和模板样式

**Files:**
- Create: `frontend/src/components/layout/PageFrame.vue`
- Create: `frontend/src/components/layout/PageHeader.vue`
- Create: `frontend/src/components/layout/PageToolbar.vue`
- Create: `frontend/src/components/layout/UiPanel.vue`
- Create: `frontend/src/components/layout/UiScrollRegion.vue`
- Create: `frontend/src/components/ui/UiEmptyState.vue`
- Create: `frontend/src/styles/layout-system.css`
- Modify: `frontend/src/main.ts`
- Modify: `frontend/tests/design-system.test.mjs`
- Modify: `frontend/tests/page-templates.test.mjs`

**Interfaces:**
- Produces: `PageFrame` prop `template: 'workspace' | 'management' | 'split' | 'document'`。
- Produces: `PageFrame` slots `header`, `toolbar`, `default`。
- Produces: `UiPanel` prop `variant: 'plain' | 'subtle' | 'raised'`。
- Produces: `UiScrollRegion` prop `axis: 'y' | 'x' | 'both'`。
- Produces: `UiEmptyState` props `title`, `description`, `icon` and default action slot。

- [ ] **Step 1: Extend the design-system tests for layout primitives**

Add to `frontend/tests/design-system.test.mjs`:

```js
test('layout primitives expose the four approved page templates', async () => {
  const frame = await readSource('src/components/layout/PageFrame.vue');
  const layout = await readSource('src/styles/layout-system.css');
  const empty = await readSource('src/components/ui/UiEmptyState.vue');

  assert.match(frame, /'workspace'\s*\|\s*'management'\s*\|\s*'split'\s*\|\s*'document'/);
  assert.match(layout, /\.page-frame--workspace/);
  assert.match(layout, /\.page-frame--management/);
  assert.match(layout, /\.page-frame--split/);
  assert.match(layout, /\.page-frame--document/);
  assert.match(empty, /ui-empty-state/);
});
```

Add to `frontend/tests/page-templates.test.mjs`:

```js
test('each page template owns a deterministic scroll model', async () => {
  const layout = await read('src/styles/layout-system.css');
  assert.match(layout, /\.page-frame--management[^}]*overflow:\s*hidden/s);
  assert.match(layout, /\.page-frame--management[\s\S]*\.page-frame__body[^}]*overflow-y:\s*auto/s);
  assert.match(layout, /\.page-frame--workspace[^}]*overflow:\s*hidden/s);
  assert.match(layout, /\.page-frame--split[^}]*overflow:\s*hidden/s);
  assert.match(layout, /\.page-frame--document[\s\S]*\.page-frame__body[^}]*overflow-y:\s*auto/s);
});
```

- [ ] **Step 2: Run layout primitive tests and verify RED**

Run: `cd frontend && node --test tests/design-system.test.mjs tests/page-templates.test.mjs`

Expected: FAIL because the layout components and stylesheet do not exist.

- [ ] **Step 3: Implement PageFrame**

Create `frontend/src/components/layout/PageFrame.vue`:

```vue
<template>
  <section class="page-frame" :class="`page-frame--${template}`">
    <div v-if="$slots.header" class="page-frame__header"><slot name="header" /></div>
    <div v-if="$slots.toolbar" class="page-frame__toolbar"><slot name="toolbar" /></div>
    <div class="page-frame__body"><slot /></div>
  </section>
</template>

<script setup lang="ts">
withDefaults(defineProps<{
  template: 'workspace' | 'management' | 'split' | 'document';
}>(), {
  template: 'management',
});
</script>
```

- [ ] **Step 4: Implement PageHeader and PageToolbar**

`PageHeader.vue` accepts `title`, optional `description`, and `actions` slot. It renders one dense header row and never owns scrolling.

`PageToolbar.vue` is a semantic wrapper with `start`, default and `end` slots and class `page-toolbar`.

- [ ] **Step 5: Implement UiPanel, UiScrollRegion and UiEmptyState**

`UiPanel.vue` renders `section.ui-panel` with the selected variant.

`UiScrollRegion.vue` maps axis to:

```ts
const classes = computed(() => `ui-scroll-region--${props.axis}`);
```

and renders `<div class="ui-scroll-region" :class="classes"><slot /></div>`.

`UiEmptyState.vue` renders a centered icon, title, description and optional action slot inside the current panel; it must not set viewport height itself.

- [ ] **Step 6: Implement layout-system.css**

Define the shared contract:

```css
.page-frame {
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  display: grid;
  color: var(--ui-text-1);
  background: var(--ui-canvas);
  overflow: hidden;
}

.page-frame--management {
  grid-template-rows: auto auto minmax(0, 1fr);
}

.page-frame--management .page-frame__body,
.page-frame--document .page-frame__body {
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
}

.page-frame--workspace,
.page-frame--split {
  grid-template-rows: auto minmax(0, 1fr);
  overflow: hidden;
}

.page-frame__header {
  padding: 16px 20px 12px;
}

.page-frame__toolbar {
  min-height: 48px;
  padding: 6px 20px;
  border-bottom: 1px solid var(--ui-border);
}

.page-frame__body {
  min-width: 0;
  min-height: 0;
}
```

Add dense panel, header, toolbar, scroll-region and empty-state styles using existing semantic tokens only.

- [ ] **Step 7: Import layout-system.css after component styles**

Update `frontend/src/main.ts` so import order is:

```ts
import './styles/tokens.css';
import './styles/base.css';
import './styles/macos.css';
import './styles/components.css';
import './styles/layout-system.css';
import './styles/page-system.css';
```

- [ ] **Step 8: Run Task 2 tests and build**

Run: `cd frontend && npm test && npm run build`

Expected: all tests pass and build succeeds.

- [ ] **Step 9: Commit Task 2**

```bash
git add frontend/src/components/layout frontend/src/components/ui/UiEmptyState.vue frontend/src/styles/layout-system.css frontend/src/main.ts frontend/tests/design-system.test.mjs frontend/tests/page-templates.test.mjs
git commit -m "🎨 新增统一页面模板与滚动组件"
```

---

### Task 3: 迁移邮件工作台页面

**Files:**
- Modify: `frontend/src/views/MailList.vue`
- Modify: `frontend/src/views/ComposeEmail.vue`
- Modify: `frontend/src/views/Backup.vue`
- Modify: `frontend/src/styles/page-system.css`
- Modify: `frontend/tests/ui-layout.test.mjs`
- Modify: `frontend/tests/page-templates.test.mjs`

**Interfaces:**
- Consumes: `PageFrame template="workspace"`、`PageToolbar`、`UiPanel`、`UiScrollRegion`、`UiEmptyState`。
- Produces: 邮件列表、写信和备份使用一致的固定工具栏与面板内滚动。

- [ ] **Step 1: Write failing page migration tests**

Add to `frontend/tests/page-templates.test.mjs`:

```js
test('mail workspace pages use the workspace template', async () => {
  for (const file of ['MailList.vue', 'ComposeEmail.vue', 'Backup.vue']) {
    const source = await read(`src/views/${file}`);
    assert.match(source, /<PageFrame[^>]*template="workspace"/);
  }
});

test('mail workspace roots do not declare page-level vertical scrolling', async () => {
  for (const file of ['MailList.vue', 'ComposeEmail.vue', 'Backup.vue']) {
    const source = await read(`src/views/${file}`);
    const styles = [...source.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/gi)].map((match) => match[1]).join('\n');
    assert.doesNotMatch(styles, /\.(mail-view|compose-page|backup-page)[^{]*\{[^}]*overflow-y:\s*auto/s);
  }
});
```

- [ ] **Step 2: Run the migration test and verify RED**

Run: `cd frontend && node --test tests/page-templates.test.mjs`

Expected: FAIL because the three views do not use `PageFrame`.

- [ ] **Step 3: Migrate MailList.vue**

Wrap the existing mail shell in:

```vue
<PageFrame template="workspace" class="mail-view ui-page ui-page--edge">
  <div class="mail-workspace-grid">
    <!-- existing folder sidebar -->
    <!-- existing mail list or detail -->
  </div>
</PageFrame>
```

Preserve all current event handlers and stores. Ensure:

```css
.mail-workspace-grid,
.mail-shell {
  height: 100%;
  min-height: 0;
  overflow: hidden;
}

.folder-sidebar,
.mail-list,
.mail-detail {
  min-height: 0;
}
```

The folder/account navigation, list and detail keep independent `overflow-y: auto` only where they own scroll.

- [ ] **Step 4: Migrate ComposeEmail.vue**

Use `PageFrame template="workspace"`; put the existing compose toolbar in the `toolbar` slot and the editor/form inside one `UiScrollRegion axis="y"`. The toolbar remains fixed while recipients, editor, attachments and signature settings scroll together.

- [ ] **Step 5: Migrate Backup.vue**

Use `PageFrame template="workspace"`; keep account tabs and filtering in the fixed toolbar region. Make archive list and detail the only scrolling panels. Remove page-root `overflow-y` and duplicate outer padding.

- [ ] **Step 6: Replace workspace page overrides in page-system.css**

Remove structural `height`, root `overflow` and outer border-radius overrides for `.mail-view`, `.compose-page` and `.backup-page`. Keep only semantic colors, panel surfaces and state styling that are not owned by `layout-system.css`.

- [ ] **Step 7: Run Task 3 tests and build**

Run: `cd frontend && npm test && npm run build`

Expected: all tests pass; no TypeScript errors.

- [ ] **Step 8: Commit Task 3**

```bash
git add frontend/src/views/MailList.vue frontend/src/views/ComposeEmail.vue frontend/src/views/Backup.vue frontend/src/styles/page-system.css frontend/tests/ui-layout.test.mjs frontend/tests/page-templates.test.mjs
git commit -m "🎨 统一邮件工作台页面布局与滚动"
```

---

### Task 4: 迁移管理型页面

**Files:**
- Modify: `frontend/src/views/UnifiedInbox.vue`
- Modify: `frontend/src/views/HistorySync.vue`
- Modify: `frontend/src/views/AccountList.vue`
- Modify: `frontend/src/views/UserManagement.vue`
- Modify: `frontend/src/styles/page-system.css`
- Modify: `frontend/tests/page-templates.test.mjs`

**Interfaces:**
- Consumes: `PageFrame template="management"`、`PageHeader`、`PageToolbar`、`UiPanel`、`UiEmptyState`。
- Produces: 页头和工具栏固定，页面主体作为唯一主滚动区域。

- [ ] **Step 1: Write failing management template tests**

Add:

```js
test('management pages use the management template and shared header', async () => {
  for (const file of ['UnifiedInbox.vue', 'HistorySync.vue', 'AccountList.vue', 'UserManagement.vue']) {
    const source = await read(`src/views/${file}`);
    assert.match(source, /<PageFrame[^>]*template="management"/);
    assert.match(source, /<PageHeader/);
  }
});
```

- [ ] **Step 2: Run test and verify RED**

Run: `cd frontend && node --test tests/page-templates.test.mjs`

Expected: FAIL on all four pages.

- [ ] **Step 3: Migrate UnifiedInbox.vue**

Use `PageHeader` for title/actions. Convert account selection to a compact `UiPanel`. Keep filter controls in `PageToolbar`. The message list or `UiEmptyState` fills the page body. Remove `.unified-page { height:100%; overflow:auto; padding:24px; }`.

- [ ] **Step 4: Migrate HistorySync.vue**

Use the management template. Keep “刷新进度” in the header action slot. Convert each account job card to a dense panel with:

```text
job header
progress metric grid
timestamps / error summary
```

The page body owns the only vertical scroll. Retain pause/resume/retry/clear handlers unchanged.

- [ ] **Step 5: Migrate AccountList.vue**

Move title, add-account and sort actions into shared header/toolbar. Keep provider and account cards inside the management body. Remove root `height:100%` and `overflow-y:auto` declarations.

- [ ] **Step 6: Migrate UserManagement.vue**

Put filters in `PageToolbar`; keep the table inside `UiPanel` with horizontal overflow only on the table wrapper. The page body owns vertical scroll; modal behavior remains unchanged.

- [ ] **Step 7: Remove management structural overrides from page-system.css**

Delete root-level overflow declarations for `.unified-page`, `.history-sync-page`, `.account-page` and `.user-page`. Preserve semantic theme rules only.

- [ ] **Step 8: Run Task 4 tests and build**

Run: `cd frontend && npm test && npm run build`

Expected: all frontend checks pass.

- [ ] **Step 9: Commit Task 4**

```bash
git add frontend/src/views/UnifiedInbox.vue frontend/src/views/HistorySync.vue frontend/src/views/AccountList.vue frontend/src/views/UserManagement.vue frontend/src/styles/page-system.css frontend/tests/page-templates.test.mjs
git commit -m "🎨 统一管理页面页头与滚动结构"
```

---

### Task 5: 迁移联系人分栏页面

**Files:**
- Modify: `frontend/src/views/ContactList.vue`
- Modify: `frontend/src/styles/page-system.css`
- Modify: `frontend/tests/page-templates.test.mjs`

**Interfaces:**
- Consumes: `PageFrame template="split"`、`PageToolbar`、`UiEmptyState`。
- Produces: 桌面双栏全高分栏；左栏和右栏分别拥有滚动，页面根不滚动。

- [ ] **Step 1: Write failing split-view tests**

Add:

```js
test('contacts use a full-height split template with two scroll owners', async () => {
  const source = await read('src/views/ContactList.vue');
  assert.match(source, /<PageFrame[^>]*template="split"/);
  assert.match(source, /class="contact-split"/);
  assert.match(source, /<UiEmptyState/);
});
```

- [ ] **Step 2: Run test and verify RED**

Run: `cd frontend && node --test tests/page-templates.test.mjs`

Expected: FAIL because ContactList still owns its whole layout.

- [ ] **Step 3: Implement the full-height split structure**

Restructure the page root:

```vue
<PageFrame template="split" class="contact-page ui-page ui-page--edge">
  <div class="contact-split">
    <aside class="contact-split__sidebar">...</aside>
    <section class="contact-split__detail">...</section>
  </div>
</PageFrame>
```

Use:

```css
.contact-split {
  height: 100%;
  min-height: 0;
  display: grid;
  grid-template-columns: 320px minmax(0, 1fr);
  overflow: hidden;
}

.contact-split__sidebar,
.contact-split__detail {
  min-height: 0;
  overflow-y: auto;
}
```

- [ ] **Step 4: Replace the floating empty content**

When no contact is selected, render `UiEmptyState` inside the right detail panel so the panel still fills all remaining height and retains its border/background.

- [ ] **Step 5: Preserve mobile behavior**

At `max-width: 768px`, switch the split to one column and show either list or detail. Keep one vertical scroll owner on the visible pane; preserve the current back behavior.

- [ ] **Step 6: Remove contact root structural overrides from page-system.css**

Remove global background and height assumptions that conflict with the split template. Keep semantic colors and list item states.

- [ ] **Step 7: Run Task 5 tests and build**

Run: `cd frontend && npm test && npm run build`

Expected: all checks pass.

- [ ] **Step 8: Commit Task 5**

```bash
git add frontend/src/views/ContactList.vue frontend/src/styles/page-system.css frontend/tests/page-templates.test.mjs
git commit -m "🎨 重构联系人全高分栏工作区"
```

---

### Task 6: 迁移设置文档型页面

**Files:**
- Modify: `frontend/src/views/Settings.vue`
- Modify: `frontend/src/views/NotificationSettings.vue`
- Modify: `frontend/src/views/About.vue`
- Modify: `frontend/src/styles/page-system.css`
- Modify: `frontend/tests/page-templates.test.mjs`

**Interfaces:**
- Consumes: `PageFrame template="document"`、`PageHeader`、`UiPanel`。
- Produces: 单一页面滚动、统一最大阅读宽度和一致的区块间距。

- [ ] **Step 1: Write failing document template tests**

Add:

```js
test('settings documents use the document template', async () => {
  for (const file of ['Settings.vue', 'NotificationSettings.vue', 'About.vue']) {
    const source = await read(`src/views/${file}`);
    assert.match(source, /<PageFrame[^>]*template="document"/);
    assert.match(source, /<PageHeader/);
  }
});
```

- [ ] **Step 2: Run test and verify RED**

Run: `cd frontend && node --test tests/page-templates.test.mjs`

Expected: FAIL on the three pages.

- [ ] **Step 3: Migrate Settings.vue**

Use `PageFrame template="document"`. Put settings groups in a `.document-column` with maximum width `960px`, aligned to the left within the scroll body rather than centered with large outer margins. Keep current theme, Gmail proxy and guide interactions.

- [ ] **Step 4: Migrate NotificationSettings.vue**

Replace page-local header and `max-width:920px; margin:auto` cards with shared header and document column. Preserve all channel fields and test/save actions.

- [ ] **Step 5: Migrate About.vue**

Use the same document column and panel spacing. Remove root `height` and `overflow-y` declarations.

- [ ] **Step 6: Remove document root structural overrides from page-system.css**

Delete root-level scrolling for `.settings-page`, `.notify-page` and `.about-page`; keep modal and semantic styling.

- [ ] **Step 7: Run Task 6 tests and build**

Run: `cd frontend && npm test && npm run build`

Expected: all checks pass.

- [ ] **Step 8: Commit Task 6**

```bash
git add frontend/src/views/Settings.vue frontend/src/views/NotificationSettings.vue frontend/src/views/About.vue frontend/src/styles/page-system.css frontend/tests/page-templates.test.mjs
git commit -m "🎨 统一设置与说明页面文档布局"
```

---

### Task 7: 收敛主题、响应式和旧兼容样式

**Files:**
- Modify: `frontend/src/styles/tokens.css`
- Modify: `frontend/src/styles/base.css`
- Modify: `frontend/src/styles/components.css`
- Modify: `frontend/src/styles/layout-system.css`
- Modify: `frontend/src/styles/page-system.css`
- Modify: `frontend/src/styles/macos.css`
- Modify: `frontend/tests/design-system.test.mjs`
- Modify: `frontend/tests/page-templates.test.mjs`

**Interfaces:**
- Consumes: 完成迁移后的所有页面模板。
- Produces: 单一密度、圆角、间距、滚动条和响应式规则；减少 `!important` 结构覆盖。

- [ ] **Step 1: Add failing style governance tests**

Add:

```js
test('page-system no longer owns page root height or scrolling', async () => {
  const source = await readSource('src/styles/page-system.css');
  assert.doesNotMatch(source, /\.(unified-page|history-sync-page|account-page|settings-page|notify-page|contact-page|about-page)[^{]*\{[^}]*(height|overflow-y):/s);
});

test('layout system defines dense shared measurements', async () => {
  const source = await readSource('src/styles/layout-system.css');
  assert.match(source, /--page-padding:\s*20px/);
  assert.match(source, /--page-gap:\s*12px/);
  assert.match(source, /min-height:\s*48px/);
  assert.match(source, /scrollbar-gutter:\s*stable/);
});
```

- [ ] **Step 2: Run governance tests and verify RED**

Run: `cd frontend && node --test tests/design-system.test.mjs tests/page-templates.test.mjs`

Expected: FAIL until old structural rules are removed and dense measurements are centralized.

- [ ] **Step 3: Centralize density tokens**

Add to `tokens.css`:

```css
:root {
  --page-padding: 20px;
  --page-padding-compact: 16px;
  --page-gap: 12px;
  --panel-padding: 16px;
  --control-height-sm: 32px;
  --control-height-md: 36px;
  --control-height-lg: 38px;
  --toolbar-height: 48px;
  --list-row-height: 48px;
}
```

Use these variables in layout and component styles.

- [ ] **Step 4: Standardize scrollbars and overscroll**

In `base.css`, style scrollbars with semantic tokens and keep them visible when content overflows. In scroll owners use `overscroll-behavior: contain` and `scrollbar-gutter: stable` on desktop.

- [ ] **Step 5: Consolidate responsive rules**

In `layout-system.css`:

```css
@media (max-width: 960px) {
  .page-frame__header,
  .page-frame__toolbar { padding-inline: 16px; }
}

@media (max-width: 768px) {
  .page-frame__header { padding-top: 14px; }
  .page-frame__toolbar { min-height: 44px; }
  .page-frame--management .page-frame__body,
  .page-frame--document .page-frame__body { scrollbar-gutter: auto; }
}
```

- [ ] **Step 6: Remove superseded compatibility rules**

Delete only rules in `macos.css` and `page-system.css` that duplicate migrated page root layout, toolbar height, outer padding and scroll ownership. Do not delete unrelated legacy helpers.

- [ ] **Step 7: Run complete frontend tests and build**

Run: `cd frontend && npm test && npm run build`

Expected: all tests pass; build succeeds.

- [ ] **Step 8: Commit Task 7**

```bash
git add frontend/src/styles frontend/tests/design-system.test.mjs frontend/tests/page-templates.test.mjs
git commit -m "🎨 收敛高密度主题与响应式规范"
```

---

### Task 8: 版本、文档和完整容器验证

**Files:**
- Modify: `VERSION`
- Modify: `package.json`
- Modify: `frontend/package.json`
- Modify: `docker-compose.yml`
- Modify: `README.md`

**Interfaces:**
- Consumes: 完成的前端工作台系统。
- Produces: `benxianyu/flymail:0.0.14` 本地镜像和正式 `flymail` 容器。

- [ ] **Step 1: Update the version source and synchronize files**

Set `VERSION` to:

```text
0.0.14
```

Run: `npm run sync-version`

Expected: root package, frontend package, Compose image and README image examples all use `0.0.14`.

- [ ] **Step 2: Update README**

Document:

- 高密度统一页面模板。
- 页面滚动模型。
- 邮件工作台、管理页、分栏页和设置文档页的统一行为。
- 侧栏始终全高、页面主体负责滚动。

No environment variable changes are required.

- [ ] **Step 3: Run the full verification suite**

Run:

```bash
cd backend && python -m unittest discover -s tests -v
cd ../frontend && npm test && npm run build
cd ..
bash -n scripts/docker-entrypoint.sh
docker compose --env-file .env.example config --no-interpolate
git diff --check
git status --short
git diff
```

Expected: backend and frontend tests pass; build and static checks succeed.

- [ ] **Step 4: Build the final image**

Run:

```bash
docker build -t benxianyu/flymail:0.0.14 .
```

Expected: successful build with frontend production assets.

- [ ] **Step 5: Start an isolated temporary container**

Use a unique container name and an empty host directory such as `/Docker/flymail/test-data-0.0.14`. Reuse required environment values without printing them. Do not mount `/Docker/flymail/data`.

Verify:

```text
/api/health returns 0.0.14
container health is healthy
MySQL is 8.0 and datadir is /data/mysql/
bind address is 127.0.0.1
/data/flymail exists
database test row survives restart
logs show mysql+pymysql://flymail:***@
image metadata contains no secret keys
MySQL logs show Shutdown complete
```

- [ ] **Step 6: Perform browser-visible layout verification**

If Chromium or Playwright is available, verify at 1920×1080, 1440×900, 1024×768 and 390×844:

```text
sidebar fills viewport on every page
Unified Inbox body scrolls when content exceeds height
History Sync body scrolls and header remains stable
Mail list, folder navigation and message detail scroll independently
Contacts list and detail fill full height
Settings and notifications use one document scroll
no horizontal overflow at desktop widths
mobile screen has one primary vertical scroll
```

If browser tooling is unavailable, verify the final compiled CSS contains the template selectors and report the missing visual evidence explicitly.

- [ ] **Step 7: Replace the production container with rollback protection**

Record current image, user count, mount, port, restart policy and network. Rename the current container as rollback, launch `flymail` with `benxianyu/flymail:0.0.14` and `/Docker/flymail/data:/data`, wait for healthy, then verify health, user count and restart persistence. Restore the previous container if health fails.

- [ ] **Step 8: Final review, commit and push**

Run:

```bash
git status --short
git diff --check
git diff
git add <only task files>
git diff --staged
git commit -m "🎨 统一高密度邮件工作台与页面滚动"
git push origin main
```

Expected: clean `main`, synchronized with `origin/main`; Docker Hub remains untouched.
