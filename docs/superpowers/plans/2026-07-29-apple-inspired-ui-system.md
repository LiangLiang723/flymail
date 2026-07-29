# FlyMail Apple 风格统一 UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复登录错误无反馈和刷新闪登录页，重构稳定可逆的侧栏动效，并建立覆盖深浅主题和全部页面的可复用 UI 设计系统。

**Architecture:** 保留现有 FastAPI、Pinia 与邮件业务逻辑，把认证启动状态、应用壳和浮层从 `App.vue` 拆为边界清晰的组件。前端建立语义 token、公共组件与兼容样式层，先迁移应用壳和高频页面，再统一其余页面的表面、控件和深色主题。

**Tech Stack:** Vue 3、TypeScript、Pinia、CSS Custom Properties、Node.js test runner、Vite、FastAPI、Docker。

## Global Constraints

- 不修改数据库结构、邮箱认证方式或 `/data` 路径。
- 同一用户继续支持多个浏览器和设备同时登录，退出只清除当前浏览器 Cookie。
- 不新增或升级生产依赖。
- 侧栏桌面展开宽度为 248px、折叠宽度为 72px，图标轨道坐标始终不变。
- 960px 及以下继续使用移动抽屉。
- 所有新增视觉样式使用语义 token，不在业务组件新增十六进制颜色。
- 支持 `prefers-reduced-motion`、`prefers-reduced-transparency` 和 `prefers-contrast`。
- 版本从 `0.0.11` 更新到 `0.0.12`。

---

### Task 1: 认证启动状态与登录错误反馈

**Files:**
- Create: `frontend/src/utils/auth-state.ts`
- Create: `frontend/src/components/app/AuthGate.vue`
- Create: `frontend/src/components/app/AppBootScreen.vue`
- Create: `frontend/tests/auth-state.test.ts`
- Modify: `frontend/src/utils/api.ts`
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/views/LoginView.vue`
- Modify: `frontend/tests/ui-layout.test.mjs`

**Interfaces:**
- Produces: `AuthState = 'booting' | 'authenticated' | 'anonymous' | 'error'`。
- Produces: `normalizeApiError(error): ApiError`、`classifyAuthError(error): 'anonymous' | 'error'`、`getLoginErrorMessage(error): string`。
- Consumes: `/api/auth/me`、`/api/auth/login` 返回的 HTTP 状态和 `detail`。

- [ ] **Step 1: Write the failing auth utility tests**

在 `frontend/tests/auth-state.test.ts` 写入：

```ts
import test from 'node:test';
import assert from 'node:assert/strict';
import { classifyAuthError, getLoginErrorMessage } from '../src/utils/auth-state';

test('auth bootstrap treats 401 and 403 as anonymous', () => {
  assert.equal(classifyAuthError({ status: 401 }), 'anonymous');
  assert.equal(classifyAuthError({ status: 403 }), 'anonymous');
});

test('auth bootstrap keeps network failures out of the login view', () => {
  assert.equal(classifyAuthError({ status: 0, network: true }), 'error');
  assert.equal(classifyAuthError({ status: 500 }), 'error');
});

test('login errors use safe and actionable Chinese messages', () => {
  assert.equal(getLoginErrorMessage({ status: 401 }), '用户名或密码错误');
  assert.equal(getLoginErrorMessage({ status: 403 }), '此账号已被禁用，请联系管理员');
  assert.equal(getLoginErrorMessage({ network: true }), '暂时无法连接 FlyMail，请稍后重试');
});
```

- [ ] **Step 2: Run auth tests and verify RED**

Run: `cd frontend && node --test tests/auth-state.test.ts`

Expected: FAIL because `src/utils/auth-state.ts` does not exist.

- [ ] **Step 3: Implement normalized API errors and auth helpers**

`frontend/src/utils/auth-state.ts` 定义：

```ts
export interface ApiError {
  status?: number;
  detail?: string;
  message?: string;
  network?: boolean;
  code?: string;
}

export type AuthState = 'booting' | 'authenticated' | 'anonymous' | 'error';

export function classifyAuthError(error: ApiError): 'anonymous' | 'error' {
  return error.status === 401 || error.status === 403 ? 'anonymous' : 'error';
}

export function getLoginErrorMessage(error: ApiError): string {
  if (error.status === 401) return '用户名或密码错误';
  if (error.status === 403) return '此账号已被禁用，请联系管理员';
  if (error.network || !error.status) return '暂时无法连接 FlyMail，请稍后重试';
  return error.detail || error.message || '登录失败，请稍后重试';
}
```

`frontend/src/utils/api.ts` 的响应拦截器必须保留 `status/detail/network/code`，同时继续兼容现有代码读取 `detail`。

- [ ] **Step 4: Implement AuthGate and boot screen**

`AuthGate.vue` 接收 `state`、`message`，在 `booting/error` 渲染 `AppBootScreen`，在 `anonymous` 渲染 `anonymous` slot，其余渲染 default slot。`AppBootScreen` 的错误状态包含“重新连接”按钮并发出 `retry`。

- [ ] **Step 5: Replace the App authentication boolean with the state machine**

`App.vue` 初始化 `authState = ref<AuthState>('booting')`；`checkAuth()` 成功设置 `authenticated`，401/403 设置 `anonymous`，网络或 5xx 设置 `error`；登录成功后设置 `authenticated`；退出设置 `anonymous`。

- [ ] **Step 6: Add inline login errors**

`LoginView.vue` 捕获登录异常，用 `getLoginErrorMessage()` 显示 `role="alert"` 的错误区域；用户名或密码发生输入时清除旧错误；提交期间显示 spinner 和“登录中”。

- [ ] **Step 7: Extend structural regression tests**

`ui-layout.test.mjs` 断言：`App.vue` 使用 `AuthGate`，不再包含 `!authReady || !currentUser`；`LoginView.vue` 包含 `role="alert"` 和 `getLoginErrorMessage`。

- [ ] **Step 8: Run Task 1 tests**

Run: `cd frontend && npm test`

Expected: all tests pass.

---

### Task 2: Design tokens and reusable primitives

**Files:**
- Create: `frontend/src/styles/tokens.css`
- Create: `frontend/src/styles/base.css`
- Create: `frontend/src/styles/components.css`
- Create: `frontend/src/components/ui/UiSpinner.vue`
- Create: `frontend/src/components/ui/UiButton.vue`
- Create: `frontend/src/components/ui/UiIconButton.vue`
- Create: `frontend/src/components/ui/UiAlert.vue`
- Create: `frontend/src/components/ui/UiCard.vue`
- Create: `frontend/tests/design-system.test.mjs`
- Modify: `frontend/src/main.ts`
- Modify: `frontend/src/styles/macos.css`

**Interfaces:**
- Produces semantic tokens beginning with `--ui-`.
- Produces `UiButton` props: `variant`, `size`, `loading`, `disabled`, `type`。
- Produces `UiIconButton` props: `label`, `size`, `loading`, `disabled`。
- Existing variables such as `--bg-primary` remain aliases to semantic tokens during migration.

- [ ] **Step 1: Write failing design-system source tests**

`design-system.test.mjs` reads source files and asserts:

```js
assert.match(tokens, /--ui-canvas:/);
assert.match(tokens, /--ui-surface-1:/);
assert.match(tokens, /--ui-text-1:/);
assert.match(tokens, /--ui-focus-ring:/);
assert.match(button, /variant.*primary.*secondary.*ghost.*danger/s);
assert.match(button, /loading/);
assert.match(components, /:focus-visible/);
assert.match(components, /prefers-reduced-motion/);
```

- [ ] **Step 2: Run design-system test and verify RED**

Run: `cd frontend && node --test tests/design-system.test.mjs`

Expected: FAIL because token and component files do not exist.

- [ ] **Step 3: Create semantic light and dark tokens**

`tokens.css` defines canvas, four surface/fill layers, three text levels, border, accent, success, warning, danger, shadows, radius, typography, spacing, side widths and motion tokens. `:root.dark` only changes token values. Add aliases for existing `--bg-*`、`--text-*`、`--color-*` variables.

- [ ] **Step 4: Create reset and global accessibility styles**

`base.css` owns box sizing, body, native form font inheritance, selection, scrollbar, focus-visible and accessibility media queries. It must not duplicate component-specific button styling.

- [ ] **Step 5: Create compatibility component styles**

`components.css` defines `.ui-button`、`.ui-icon-button`、`.ui-input`、`.ui-select`、`.ui-textarea`、`.ui-card`、`.ui-alert`、`.ui-badge`、`.ui-dialog-*` and maps legacy `.btn/.input/.card` classes to the same token-based appearance.

- [ ] **Step 6: Create reusable Vue primitives**

`UiButton` renders button content, optional leading/trailing slots and `UiSpinner`; `UiIconButton` enforces an accessible label; `UiAlert` renders semantic status; `UiCard` provides header/default/footer slots.

- [ ] **Step 7: Replace the old global stylesheet entry**

`main.ts` imports `tokens.css`、`base.css`、`components.css` and then the temporary `macos.css` compatibility layer. Remove duplicated root tokens/reset/component foundations from `macos.css`, leaving only legacy helpers not yet migrated.

- [ ] **Step 8: Run Task 2 tests and build**

Run: `cd frontend && npm test && npm run build`

Expected: tests pass; `vue-tsc` and Vite build succeed.

---

### Task 3: Stable Apple-style application shell and sidebar

**Files:**
- Create: `frontend/src/components/app/AppSidebar.vue`
- Create: `frontend/src/components/app/UserMenu.vue`
- Create: `frontend/src/components/app/NotificationDrawer.vue`
- Create: `frontend/src/styles/app-shell.css`
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/main.ts`
- Modify: `frontend/tests/ui-layout.test.mjs`

**Interfaces:**
- `AppSidebar` consumes `collapsed`, `mobile`, `mobileOpen`, `currentView`, `navItems`, account/folder data and user data.
- `AppSidebar` emits `toggle-collapse`、`navigate`、`open-notifications`、`change-password`、`logout`、`close-mobile`、`mail-navigation`。
- `NotificationDrawer` consumes notifications and emits close/read/clear/open events.

- [ ] **Step 1: Change sidebar tests to the stable-rail contract**

Update `ui-layout.test.mjs` to assert:

```js
assert.match(shellCss, /--app-sidebar-expanded:\s*248px/);
assert.match(shellCss, /--app-sidebar-collapsed:\s*72px/);
assert.match(shellCss, /grid-template-columns:\s*72px minmax\(0, 1fr\)/);
assert.doesNotMatch(shellCss, /sidebar-collapsed[\s\S]*flex-direction:\s*column/);
assert.match(shellCss, /\.sidebar-icon-rail/);
assert.match(shellCss, /prefers-reduced-transparency/);
```

- [ ] **Step 2: Run the sidebar test and verify RED**

Run: `cd frontend && node --test tests/ui-layout.test.mjs`

Expected: FAIL because the current shell still rearranges the header and has no fixed icon rail.

- [ ] **Step 3: Extract application shell components**

Move visual navigation, account menu and notification drawer templates to the new components while keeping route/view state and data loading in `App.vue`.

- [ ] **Step 4: Implement the stable two-column sidebar**

`AppSidebar.vue` uses a fixed 72px `.sidebar-icon-rail` and a clipped `.sidebar-label-pane`. Collapsing changes only sidebar width and label pane opacity/transform; icon buttons retain identical dimensions and coordinates. The collapse control stays in the same rail slot.

- [ ] **Step 5: Implement interruptible motion and accessibility**

`app-shell.css` uses 240ms `cubic-bezier(0.2, 0.8, 0.2, 1)` and avoids `display:none` during the text animation. `prefers-reduced-motion` removes translate/width animation; reduced transparency uses solid surfaces; high contrast adds borders.

- [ ] **Step 6: Preserve mobile mail navigation**

At 960px and below, the sidebar becomes a left drawer, retains account/folder navigation on the mail page, closes after navigation and keeps a 44px launcher hit area.

- [ ] **Step 7: Run shell tests and build**

Run: `cd frontend && npm test && npm run build`

Expected: all frontend checks pass.

---

### Task 4: High-frequency page migration

**Files:**
- Modify: `frontend/src/views/MailList.vue`
- Modify: `frontend/src/views/UnifiedInbox.vue`
- Modify: `frontend/src/views/ComposeEmail.vue`
- Modify: `frontend/src/views/AccountList.vue`
- Modify: `frontend/src/components/TiptapEditor.vue`
- Modify: `frontend/src/styles/components.css`
- Modify: `frontend/tests/design-system.test.mjs`

**Interfaces:**
- Consumes semantic tokens and reusable primitives from Task 2.
- Produces consistent page header, toolbar, segmented control, card, dialog, form and status appearances.

- [ ] **Step 1: Add high-frequency page audit assertions**

The design-system test scans these files and fails when a `<style>` block introduces new literal hex colors outside comments or SVG data. It also asserts each page uses at least one shared `ui-*` class or imported UI component.

- [ ] **Step 2: Run the audit and verify RED**

Run: `cd frontend && node --test tests/design-system.test.mjs`

Expected: FAIL with existing hardcoded colors and page-local control styles.

- [ ] **Step 3: Migrate MailList and UnifiedInbox**

Map list backgrounds, toolbars, search, filter chips, message rows, pagination, empty/loading states and mobile layout to semantic tokens. Keep all existing data loading and navigation behavior unchanged.

- [ ] **Step 4: Migrate ComposeEmail and editor**

Map composer toolbar buttons, recipient chips, inputs, attachment area, signature menus and dialogs to shared button/form/dialog tokens. Keep Tiptap commands and attachment behavior unchanged.

- [ ] **Step 5: Migrate AccountList**

Map account cards, provider selection, toggles, dialogs, forms and danger actions to shared surfaces and states. Keep OAuth/custom provider flows unchanged.

- [ ] **Step 6: Run high-frequency tests and build**

Run: `cd frontend && npm test && npm run build`

Expected: all checks pass.

---

### Task 5: Remaining page and dark-theme migration

**Files:**
- Modify: `frontend/src/views/Settings.vue`
- Modify: `frontend/src/views/NotificationSettings.vue`
- Modify: `frontend/src/views/HistorySync.vue`
- Modify: `frontend/src/views/Backup.vue`
- Modify: `frontend/src/views/ContactList.vue`
- Modify: `frontend/src/views/UserManagement.vue`
- Modify: `frontend/src/views/About.vue`
- Modify: `frontend/src/components/NasPathPicker.vue`
- Modify: `frontend/src/styles/components.css`
- Modify: `frontend/tests/design-system.test.mjs`

**Interfaces:**
- Consumes tokens/components from Task 2.
- Produces full-page light/dark consistency and prevents new hardcoded control colors.

- [ ] **Step 1: Extend the audit to all remaining Vue files**

Fail on literal CSS colors for controls/surfaces and duplicate base declarations such as `.btn { ... }`, `.input { ... }`, or `.card { ... }` inside page scoped styles.

- [ ] **Step 2: Run the audit and verify RED**

Run: `cd frontend && node --test tests/design-system.test.mjs`

Expected: FAIL with the remaining legacy styles.

- [ ] **Step 3: Migrate settings and notification pages**

Unify section headers, disclosure rows, form fields, switches, save/test actions, modal preview and sensitive-value hints.

- [ ] **Step 4: Migrate operational pages**

Unify HistorySync and Backup progress, status chips, paths, job cards, warnings and destructive actions.

- [ ] **Step 5: Migrate contacts, users, about and NAS picker**

Unify list/detail layouts, dialogs, empty states, icon buttons, update status and directory picker surfaces.

- [ ] **Step 6: Run the complete frontend verification**

Run: `cd frontend && npm test && npm run build`

Expected: all frontend tests and production build pass.

---

### Task 6: Backend session regression, documentation, version and deployment

**Files:**
- Create: `backend/tests/test_local_auth_sessions.py`
- Modify: `README.md`
- Modify: `VERSION`
- Modify: `package.json`
- Modify: `frontend/package.json`
- Modify: `docker-compose.yml`

**Interfaces:**
- Verifies multiple signed cookies for the same user remain valid independently.
- Produces version `0.0.12` metadata and deployment documentation.

- [ ] **Step 1: Write the multi-session regression test**

Test two separately created session cookies for the same user ID, assert both parse successfully, then verify clearing one response does not alter the other cookie value.

- [ ] **Step 2: Run the backend test and verify behavior**

Run: `cd backend && python -m unittest tests.test_local_auth_sessions -v`

Expected: PASS because the existing signed-cookie design already supports concurrent sessions. This test locks in the confirmed behavior; it does not change authentication architecture.

- [ ] **Step 3: Update README and version**

Set `VERSION` to `0.0.12`, run `npm run sync-version`, and document login error feedback, no-flash startup, stable icon-rail sidebar, semantic themes and reusable UI components.

- [ ] **Step 4: Run full repository verification**

Run:

```bash
cd backend && python -m unittest discover -s tests -v
cd ../frontend && npm test && npm run build
cd ..
bash -n scripts/docker-entrypoint.sh
docker compose config
git diff --check
```

Expected: all checks pass.

- [ ] **Step 5: Build and verify isolated Docker deployment**

Build `benxianyu/flymail:0.0.12`; start an isolated temporary container and data directory; verify health/version, MySQL 8.0 and `/data/mysql/`, `/data/flymail`, database read/write, restart persistence, password redaction, clean image metadata and safe MySQL shutdown.

- [ ] **Step 6: Replace the current container without changing data mount**

Recreate `flymail` using `benxianyu/flymail:0.0.12` while retaining `/Docker/flymail/data:/data`; verify healthy status, health version, MySQL binding and existing database readability.

- [ ] **Step 7: Commit and push**

Stage only task files, commit with `🎨 重构统一界面并修复登录体验`, and push `main` to `origin/main`. Do not upload Docker Hub.
