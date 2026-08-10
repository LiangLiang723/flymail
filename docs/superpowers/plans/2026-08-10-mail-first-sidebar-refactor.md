# FlyMail Mail-First Sidebar Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace FlyMail’s desktop double-sidebar navigation with one persistent mail-first sidebar while moving low-frequency product areas into the user menu, without changing mail, sync, auth, database, or persistence behavior.

**Architecture:** `AppSidebar.vue` becomes the single app-shell owner for compose, unified inbox, account, folder, notification, and user entry points. `App.vue` owns guarded global navigation and updates `mailStore` directly; `MailList.vue` reacts to `currentAccountId/currentFolder` through its existing watcher and no longer owns account/folder navigation. Desktop and mobile reuse the same sidebar DOM; CSS changes only its presentation at the existing 960px breakpoint.

**Tech Stack:** Vue 3 `<script setup>`, TypeScript, Pinia, Node `node:test`, existing FlyMail CSS token/layout system, FastAPI/MySQL Docker image for regression validation.

## Global Constraints

- Work only in `/home/chatgpt/flymail` on branch `main`; protect unrelated user changes.
- Read and obey `AGENTS.md`, `DESIGN.md`, and the approved spec `docs/superpowers/specs/2026-08-10-mail-first-sidebar-refactor-design.md` before implementation.
- Follow test-driven development: every production behavior or refactor change starts with a focused failing test and the failure must be observed before implementation.
- Do not add or upgrade production dependencies.
- Do not modify backend APIs, authentication rules, database schema, sync behavior, environment variable semantics, or `/Docker/flymail/data` contents.
- Preserve the existing sidebar geometry: expanded `248px`, collapsed `72px`, mobile drawer breakpoint `960px`, mobile drawer width `min(88vw, 324px)`.
- Preserve `flymail_sidebar_collapsed` persistence and the current collapsed brand-slot logo/expand interaction.
- Keep the mobile MailList “current folder” control as the drawer launcher; remove only the duplicate mobile account/folder template from `AppSidebar.vue`.
- Folder strong-active state is allowed only while `currentView === 'mail'`; unified inbox strong-active state is allowed only while `currentView === 'unified'`.
- Account selection remains persistent context across non-mail pages, but must not make a non-mail page look like the mail list itself is active.
- Keep generic `flymail-navigate` and `flymail-toggle-sidebar` events because other views currently use them; remove only the obsolete `flymail-mail-navigation` event.
- Keep the existing single-account reauthorization banner in `MailList.vue` for compatibility, but delegate its OAuth action to the shared reauthorization composable.
- Version the implementation release as `0.0.46`; `VERSION` remains the source of truth and `npm run sync-version` must update package metadata, Compose, and README image references.
- Do not commit between implementation tasks. The project requires full verification before the single task commit. Each task ends with focused tests instead of an intermediate commit.
- After application changes, build `benxianyu/flymail:0.0.46`, validate it in an isolated temporary container/data directory, then safely recreate the current `flymail` container without modifying `/Docker/flymail/data`.
- Do not upload Docker Hub.

---

## File Map

**Create**

- `frontend/src/composables/useAccountReauthorization.ts` — shared account-level Gmail/Outlook OAuth reauthorization flow extracted from `MailList.vue`.
- `frontend/tests/mail-sidebar-refactor.test.mjs` — focused architecture and interaction contract for the mail-first sidebar/navigation state flow.

**Modify**

- `frontend/src/App.vue:14-28, 150-166, 209-248, 436-449` — sidebar props/events, guarded navigation primitives, account/folder/compose handlers, explicit authenticated view whitelist.
- `frontend/src/components/app/AppSidebar.vue:60-115, 145-208` — replace generic primary nav plus mobile-only mail navigation with the single compose/unified/account/folder navigation DOM.
- `frontend/src/components/app/UserMenu.vue:34-68` — move Contacts, Sync Management, Account Management, and Mail Backup into grouped user-menu entries.
- `frontend/src/views/MailList.vue:3-65, 394-440, 942-1066, 1097-1134, 1666-1731, 3121-3265, 3313-3318, 3606-3613` — remove desktop account/folder sidebar, remove local global compose entry and obsolete mail-navigation event, use shared OAuth reauthorization, make workspace single-column, and delete only styles tied to the removed live markup.
- `frontend/src/styles/app-shell.css:245-291, 397-478, 491-493, 752-919` — style fixed mail actions, shared account/folder scroll region, account/folder rows, collapsed behavior, user-menu viewport scrolling, and the same DOM in the mobile drawer.
- `frontend/tests/ui-layout.test.mjs:24-127` — update old global-nav/mobile-mail-navigation assertions to the new single-sidebar contract.
- `frontend/tests/product-ui-redesign.test.mjs:58-102` — keep page and shell design-system assertions aligned with the new navigation ownership.
- `frontend/tests/design-contract.test.mjs:19-39` — assert the permanent mail-navigation information-architecture rule after `DESIGN.md` is updated.
- `DESIGN.md` — add the stable information-architecture invariant for the mail-first permanent sidebar and user menu.
- `README.md` — replace the outdated description of the global function sidebar/user menu with the implemented mail-first navigation behavior.
- `VERSION`, `package.json`, `frontend/package.json`, `docker-compose.yml`, `README.md` — version synchronization to `0.0.46` via `npm run sync-version`.

**Read/verify but do not modify unless a failing regression directly requires it**

- `frontend/src/stores/mail.ts` — existing `setAccount`, `loadFolders`, `setFolder`, and account/folder state remain the source of navigation context.
- `frontend/src/views/ComposeEmail.vue` — existing compose-draft consumption and compose-workspace restoration must keep working.
- `frontend/src/utils/oauthWindow.ts` — shared popup helpers remain unchanged.

---

### Task 1: Lock the mail-first navigation contract with failing tests

**Files:**
- Create: `frontend/tests/mail-sidebar-refactor.test.mjs`
- Modify: `frontend/tests/ui-layout.test.mjs:24-127`
- Modify: `frontend/tests/product-ui-redesign.test.mjs:58-102`

**Interfaces:**
- Consumes: current source files as text, matching the project’s existing static design-contract testing style.
- Produces: failing tests that define the target ownership of compose/unified/account/folder navigation and removal of `flymail-mail-navigation`.

- [ ] **Step 1: Create focused source-contract tests for the new app-shell ownership**

Create `frontend/tests/mail-sidebar-refactor.test.mjs` with tests equivalent to:

```js
import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (file) => readFile(path.join(frontendRoot, file), 'utf8');
const readOptional = async (file) => {
  try {
    return await read(file);
  } catch (error) {
    if (error?.code === 'ENOENT') return '';
    throw error;
  }
};

test('app shell owns mail navigation without the legacy mail navigation event', async () => {
  const app = await read('src/App.vue');
  const sidebar = await read('src/components/app/AppSidebar.vue');
  const mail = await read('src/views/MailList.vue');

  assert.match(app, /@compose="openComposeFromSidebar"/);
  assert.match(app, /@select-account="openMailAccount"/);
  assert.match(app, /@select-folder="openMailFolder"/);
  assert.match(app, /@reauthorize-account="reauthorizeAccount"/);
  assert.match(sidebar, /class="sidebar-compose-action"/);
  assert.match(sidebar, /class="sidebar-mail-accounts"/);
  assert.match(sidebar, /class="sidebar-mail-folders"/);
  assert.doesNotMatch(app, /flymail-mail-navigation/);
  assert.doesNotMatch(mail, /flymail-mail-navigation/);
});

test('folder and unified active states are tied to the matching page', async () => {
  const sidebar = await read('src/components/app/AppSidebar.vue');

  assert.match(sidebar, /currentView === 'unified'/);
  assert.match(sidebar, /currentView === 'mail'[^\n]*mailStore\.currentFolder === folder\.path/);
});

test('desktop and mobile share one account and folder navigation tree', async () => {
  const sidebar = await read('src/components/app/AppSidebar.vue');
  const css = await read('src/styles/app-shell.css');

  assert.doesNotMatch(sidebar, /mobile-mail-navigation/);
  assert.doesNotMatch(css, /\.mobile-mail-navigation/);
  assert.match(sidebar, /v-for="account in mailStore\.accounts"/);
  assert.match(sidebar, /v-for="folder in mailStore\.folders"/);
  assert.match(css, /@media \(max-width:\s*960px\)[\s\S]*width:\s*min\(88vw,\s*324px\)/s);
});

test('mail list no longer owns the desktop account and folder sidebar', async () => {
  const mail = await read('src/views/MailList.vue');

  assert.doesNotMatch(mail, /class="folder-sidebar"/);
  assert.doesNotMatch(mail, /class="account-switcher"/);
  assert.doesNotMatch(mail, /class="compose-entry-btn"/);
  assert.match(mail, /grid-template-columns:\s*minmax\(0,\s*1fr\)/);
});
```

- [ ] **Step 2: Update existing layout assertions that currently require the old navigation**

In `frontend/tests/ui-layout.test.mjs`, replace assertions that require `nav-list`, `navItems`, `.folder-sidebar-header`, `.account-switcher`, `mobile-mail-navigation`, and `flymail-mail-navigation` with assertions for the new compose/unified/account/folder sections and the retained 248/72/960 geometry.

Keep unrelated auth, overlay, accessibility, refresh, attachment, and editor assertions unchanged.

- [ ] **Step 3: Keep the product-system test focused on a single-column MailList workspace**

In `frontend/tests/product-ui-redesign.test.mjs`, retain the `PageFrame workspace + fluid`, `workspace-grid`, `UiIconButton`, `UiBadge`, `UiEmptyState`, and `UiLoadingState` checks and add a negative assertion that `MailList.vue` no longer contains `folder-sidebar`.

- [ ] **Step 4: Run only the new/changed tests and verify they fail for the intended missing refactor**

Run:

```bash
cd frontend
node --test tests/mail-sidebar-refactor.test.mjs tests/ui-layout.test.mjs tests/product-ui-redesign.test.mjs
```

Expected result: FAIL because current `AppSidebar.vue` still renders `navItems` and `mobile-mail-navigation`, current `MailList.vue` still owns `.folder-sidebar`/`.account-switcher`/`.compose-entry-btn`, and `flymail-mail-navigation` still exists.

Do not proceed until the failure messages correspond to those old structures rather than syntax or path errors.

---

### Task 2: Extract account reauthorization into a shared composable

**Files:**
- Create: `frontend/src/composables/useAccountReauthorization.ts`
- Modify: `frontend/src/views/MailList.vue:394-420, 1097-1134`
- Test: `frontend/tests/mail-sidebar-refactor.test.mjs`

**Interfaces:**
- Consumes: `useMailStore()`, `useUIStore()`, `api`, and `authWindowBlockedMessage`, `closeAuthWindow`, `navigateAuthWindow`, `openAuthWindowSync` from `src/utils/oauthWindow.ts`.
- Produces: `useAccountReauthorization(): { reauthorizeAccount(accountId?: string): Promise<void> }`.

- [ ] **Step 1: Add a failing extraction contract**

Append a test that requires `MailList.vue` to import the composable instead of implementing OAuth flow locally:

```js
test('account reauthorization is shared outside the mail list', async () => {
  const mail = await read('src/views/MailList.vue');
  const composable = await readOptional('src/composables/useAccountReauthorization.ts');

  assert.match(mail, /useAccountReauthorization/);
  assert.doesNotMatch(mail, /async function reauthorize\(/);
  assert.match(composable, /export function useAccountReauthorization\(/);
  assert.match(composable, /async function reauthorizeAccount\(accountId\?: string\)/);
  assert.match(composable, /sessionStorage\.setItem\('flymail_oauth_reauth', '1'\)/);
});
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
cd frontend
node --test tests/mail-sidebar-refactor.test.mjs
```

Expected result: FAIL because `useAccountReauthorization.ts` does not exist and `MailList.vue` still owns `reauthorize()`.

- [ ] **Step 3: Create the minimal shared composable by moving the existing behavior without changing copy or OAuth ordering**

Create `frontend/src/composables/useAccountReauthorization.ts` with this shape:

```ts
import { useMailStore } from '../stores/mail';
import { useUIStore } from '../stores/ui';
import api from '../utils/api';
import {
  authWindowBlockedMessage,
  closeAuthWindow,
  navigateAuthWindow,
  openAuthWindowSync,
} from '../utils/oauthWindow';

export function useAccountReauthorization() {
  const mailStore = useMailStore();
  const uiStore = useUIStore();

  async function reauthorizeAccount(accountId?: string): Promise<void> {
    const targetId = accountId || mailStore.currentAccountId;
    const targetAccount = mailStore.accounts.find((account) => account.id === targetId);
    if (!targetAccount) return;

    const provider = targetAccount.provider;
    const providerLabel = provider === 'outlook' ? 'Microsoft' : 'Google';
    const { win: authWindow } = openAuthWindowSync(providerLabel);
    if (!authWindow) {
      uiStore.error(authWindowBlockedMessage(providerLabel));
      return;
    }

    try {
      const settingsData = await api.get('/settings') as any;
      const settings = settingsData.settings || {};
      const redirectUri = provider === 'outlook'
        ? settings.outlook_redirect_uri || ''
        : settings.gmail_redirect_uri || '';

      if (!redirectUri) {
        closeAuthWindow(authWindow);
        uiStore.error(provider === 'outlook'
          ? '请先在设置页面配置 Microsoft 重定向 URI'
          : '请先在设置页面配置 Gmail 重定向 URI');
        return;
      }

      sessionStorage.setItem('flymail_oauth_reauth', '1');
      const data = await api.post('/accounts/auth-url', { provider, redirect_uri: redirectUri }) as any;
      if (data.error) {
        closeAuthWindow(authWindow);
        uiStore.error('获取授权链接失败：' + data.error);
        return;
      }
      if (!data.auth_url) {
        closeAuthWindow(authWindow);
        uiStore.error('获取授权链接失败');
        return;
      }
      if (!navigateAuthWindow(authWindow, data.auth_url)) {
        uiStore.error(authWindowBlockedMessage(providerLabel));
      }
    } catch (error: any) {
      closeAuthWindow(authWindow);
      uiStore.error('重新授权失败：' + (error.response?.data?.error || error.message || '网络错误'));
    }
  }

  return { reauthorizeAccount };
}
```

The exact user-facing error messages and the critical “open popup before first `await`” ordering must remain unchanged from the current implementation.

- [ ] **Step 4: Replace MailList’s local OAuth implementation with the composable**

In `MailList.vue`:

- remove the direct `oauthWindow` helper import;
- import `useAccountReauthorization`;
- initialize `const { reauthorizeAccount } = useAccountReauthorization();`;
- change the single-account banner to `@click="reauthorizeAccount(mailStore.currentAccountId)"`;
- delete the local `reauthorize()` function.

- [ ] **Step 5: Re-run the focused extraction test and TypeScript build check**

Run:

```bash
cd frontend
node --test tests/mail-sidebar-refactor.test.mjs
npm run build
```

Expected result: the extraction contract passes and `npm run build` succeeds. Other mail-first source contracts may remain red until subsequent tasks, but this extraction itself must introduce no TypeScript or production-build error.

---

### Task 3: Move guarded account, folder, compose, and reauthorization actions into `App.vue`

**Files:**
- Modify: `frontend/src/App.vue:14-28, 150-166, 209-248, 436-449`
- Test: `frontend/tests/mail-sidebar-refactor.test.mjs`

**Interfaces:**
- Consumes: `requestNavigation` guard semantics, `mailStore.setAccount`, `mailStore.loadFolders`, `mailStore.setFolder`, `mailStore.setComposeDraft`, and `reauthorizeAccount` from Task 2.
- Produces: `openComposeFromSidebar(): Promise<void>`, `openMailAccount(accountId: string): Promise<void>`, `openMailFolder(path: string): Promise<void>`, and an explicit authenticated-view whitelist independent of sidebar rendering.

- [ ] **Step 1: Add failing tests for guard ordering and explicit view validation**

Append tests equivalent to:

```js
test('sidebar mail actions honor navigation protection before mutating mail context', async () => {
  const app = await read('src/App.vue');

  assert.match(app, /async function openMailAccount\(accountId: string\)[\s\S]*if \(!await requestNavigation\('mail'\)\) return;[\s\S]*mailStore\.setAccount\(accountId\)/s);
  assert.match(app, /async function openMailFolder\(path: string\)[\s\S]*if \(!await requestNavigation\('mail'\)\) return;[\s\S]*mailStore\.setFolder\(path\)/s);
  assert.match(app, /const authenticatedViews = new Set\(/);
  assert.doesNotMatch(app, /navItems\.value\.some/);
});
```

For compose, require this order in the source contract: `confirmNavigation('compose')` succeeds first, then `mailStore.clearComposeWorkspace()`, then `mailStore.setComposeDraft(...)`, then `commitNavigation('compose')`. This prevents a cancelled signature-navigation confirmation from leaking a new draft and prevents a previously saved compose workspace from overriding the explicit global “写邮件” action.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
cd frontend
node --test tests/mail-sidebar-refactor.test.mjs
```

Expected result: FAIL because `App.vue` still exposes `navItems`, still forwards `flymail-mail-navigation`, and lacks the app-level handlers.

- [ ] **Step 3: Split navigation guarding from navigation commit so compose draft preparation cannot leak when navigation is cancelled**

Refactor the existing `requestNavigation()` logic into the following minimal structure:

```ts
async function confirmNavigation(target: string): Promise<boolean> {
  if (
    currentView.value === 'signatures'
    && target !== 'signatures'
    && signatureStore.hasUnsavedChanges
  ) {
    const confirmed = await uiStore.showConfirm({
      title: '放弃未保存的签名更改？',
      message: '当前签名尚未保存，离开后这些更改将丢失。',
      confirmText: '放弃更改',
      danger: true,
    });
    if (!confirmed) return false;
    signatureStore.discardDraft();
  }
  return true;
}

function commitNavigation(target: string, source?: SignatureEntrySource) {
  if (target === 'signatures' && source) signatureStore.setEntrySource(source);
  currentView.value = target;
  mobileSidebarOpen.value = false;
}

async function requestNavigation(target: string, source?: SignatureEntrySource): Promise<boolean> {
  if (!await confirmNavigation(target)) return false;
  commitNavigation(target, source);
  return true;
}
```

This keeps every existing caller’s behavior while giving the new compose action a safe point to prepare state after confirmation and before the view is committed.

- [ ] **Step 4: Add the app-level sidebar actions**

Implement:

```ts
async function openComposeFromSidebar() {
  if (!mailStore.currentAccountId) {
    uiStore.error('请先在账号管理中添加邮箱');
    return;
  }
  if (!await confirmNavigation('compose')) return;
  mailStore.clearComposeWorkspace();
  mailStore.setComposeDraft({
    account_id: mailStore.currentAccountId,
    compose_kind: 'new',
  });
  commitNavigation('compose');
}

async function openMailAccount(accountId: string) {
  if (!await requestNavigation('mail')) return;
  if (accountId === mailStore.currentAccountId) return;
  mailStore.setAccount(accountId);
  await mailStore.loadFolders();
}

async function openMailFolder(path: string) {
  if (!await requestNavigation('mail')) return;
  if (path === mailStore.currentFolder) return;
  mailStore.setFolder(path);
}
```

Initialize `const { reauthorizeAccount } = useAccountReauthorization();` in `App.vue` and wire the new sidebar events:

```vue
<AppSidebar
  :collapsed="sidebarCollapsed"
  :mobile="isMobileLayout"
  :mobile-open="mobileSidebarOpen"
  :current-view="currentView"
  :unified-inbox-enabled="unifiedInboxEnabled"
  :user="currentUser"
  @compose="openComposeFromSidebar"
  @select-account="openMailAccount"
  @select-folder="openMailFolder"
  @reauthorize-account="reauthorizeAccount"
  @navigate="navigateFromSidebar"
  ...
/>
```

Remove the `MailNavigation` type and `selectMobileMailNavigation()` bridge.

- [ ] **Step 5: Replace `navItems`-dependent validation with an explicit page whitelist**

Use an explicit set containing:

```ts
const authenticatedViews = new Set([
  'mail',
  'unified',
  'contacts',
  'history-sync',
  'accounts',
  'backup',
  'profile',
  'signatures',
  'notifications',
  'settings',
  'about',
]);
```

In the `watch(currentView, ...)` validation, keep the existing admin-only `users` redirect and special `compose` persistence behavior, but replace the `navItems.value.some(...)` check with `authenticatedViews.has(value)` plus the existing admin condition.

- [ ] **Step 6: Run the focused navigation tests**

Run:

```bash
cd frontend
node --test tests/mail-sidebar-refactor.test.mjs tests/signature-navigation-contract.test.mjs
```

Expected result: app-level navigation and signature-unsaved protection assertions pass. Sidebar-template assertions may still fail until Task 4.

---

### Task 4: Rebuild `AppSidebar.vue` as the single mail navigation tree

**Files:**
- Modify: `frontend/src/components/app/AppSidebar.vue:60-115, 145-208`
- Modify: `frontend/src/styles/app-shell.css:245-327, 491-493, 752-919`
- Test: `frontend/tests/mail-sidebar-refactor.test.mjs`
- Test: `frontend/tests/ui-layout.test.mjs`

**Interfaces:**
- Consumes: `currentView`, `unifiedInboxEnabled`, `mailStore.accounts`, `mailStore.currentAccountId`, `mailStore.folders`, `mailStore.currentFolder`, `mailStore.reauthAccountIds`.
- Produces events: `compose`, `select-account(accountId: string)`, `select-folder(path: string)`, `reauthorize-account(accountId: string)`, existing `navigate`, `open-notifications`, `toggle-collapse`, `close-mobile`, `change-password`, `logout`.

- [ ] **Step 1: Add failing assertions for fixed actions, one scroll owner, and shared desktop/mobile markup**

Extend `mail-sidebar-refactor.test.mjs` to require these classes and semantics:

```js
assert.match(sidebar, /class="sidebar-primary-actions"/);
assert.match(sidebar, /class="sidebar-scroll sidebar-mail-navigation"/);
assert.match(sidebar, /class="sidebar-section-title"[^>]*>邮箱账号</);
assert.match(sidebar, /class="sidebar-section-title"[^>]*>文件夹</);
assert.match(sidebar, /:disabled="mailStore\.accounts\.length === 0"/);
assert.match(css, /\.sidebar-scroll\s*\{[^}]*flex:\s*1;[^}]*min-height:\s*0;[^}]*overflow-y:\s*auto/s);
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
cd frontend
node --test tests/mail-sidebar-refactor.test.mjs tests/ui-layout.test.mjs
```

Expected result: FAIL because the sidebar still has generic `navItems` and a mobile-only mail section.

- [ ] **Step 3: Replace generic `navItems` markup with fixed mail-first actions**

Immediately after `.sidebar-header`, render a fixed `.sidebar-primary-actions` containing:

1. “写邮件” as `.sidebar-row.sidebar-compose-action`, disabled when `mailStore.accounts.length === 0`, with an accessible title/label and `@click="emit('compose')"`.
2. Conditional “聚合收件箱” as `.sidebar-row.sidebar-mail-entry`, visible only when `unifiedInboxEnabled`, active only when `currentView === 'unified'`, and `@click="emit('navigate', 'unified')"`.

Move the existing 16×16 plus SVG from the current `MailList.vue` `.compose-entry-btn` into the sidebar compose button, and use existing `AppIcon name="inbox"` for unified inbox. Do not add an icon dependency or add a new AppIcon glyph.

- [ ] **Step 4: Render accounts and folders once inside the existing single scroll owner**

Change `.sidebar-scroll` to `.sidebar-scroll.sidebar-mail-navigation` and render two sections:

```vue
<section class="sidebar-mail-accounts" aria-labelledby="sidebar-accounts-title">
  <h3 id="sidebar-accounts-title" class="sidebar-section-title">邮箱账号</h3>
  <p v-if="mailStore.accounts.length === 0" class="sidebar-empty-copy">暂无邮箱账号</p>
  <div
    v-for="account in mailStore.accounts"
    :key="account.id"
    class="sidebar-account-row"
    :class="{ 'is-reauth': mailStore.reauthAccountIds.has(account.id) }"
  >
    <button
      type="button"
      class="sidebar-row sidebar-account-item"
      :class="{
        active: currentView === 'mail' && mailStore.currentAccountId === account.id,
        'is-context': mailStore.currentAccountId === account.id,
      }"
      :title="collapsed && !mobile ? accountDisplayName(account) : account.email"
      @click="emit('select-account', account.id)"
    >
      <span class="sidebar-row-icon sidebar-account-icon">
        <AccountIcon :account="account" :size="30" decorative />
      </span>
      <span class="sidebar-label-pane sidebar-account-copy">
        <strong>{{ accountDisplayName(account) }}</strong>
        <small>{{ account.email }}</small>
      </span>
    </button>
    <button
      v-if="mailStore.reauthAccountIds.has(account.id)"
      class="sidebar-account-reauth"
      type="button"
      title="重新授权"
      :aria-label="`重新授权 ${accountDisplayName(account)}`"
      @click="emit('reauthorize-account', account.id)"
    >
      <AppIcon name="sync" :size="15" />
    </button>
  </div>
</section>

<section v-if="mailStore.accounts.length > 0" class="sidebar-mail-folders" aria-labelledby="sidebar-folders-title">
  <h3 id="sidebar-folders-title" class="sidebar-section-title">文件夹</h3>
  <button
    v-for="folder in mailStore.folders"
    :key="folder.path"
    type="button"
    class="sidebar-row sidebar-folder-item"
    :class="{ active: currentView === 'mail' && mailStore.currentFolder === folder.path }"
    :title="collapsed && !mobile ? mailStore.folderDisplayName(folder.name) : undefined"
    @click="emit('select-folder', folder.path)"
  >
    <span class="sidebar-row-icon"><AppIcon :name="folderIconName(folder.name)" :size="17" /></span>
    <span class="sidebar-label-pane sidebar-folder-copy">
      <span>{{ mailStore.folderDisplayName(folder.name) }}</span>
      <small>{{ folderCount(folder) }}</small>
    </span>
  </button>
</section>
```

Rename `mobileFolderCount()` to `folderCount()` because the same function is now shared by desktop and mobile.

- [ ] **Step 5: Update props and emitted-event types**

Remove `NavItem`, `MailNavigation`, and the `navItems` prop. Add `unifiedInboxEnabled: boolean` and typed events:

```ts
const emit = defineEmits<{
  'toggle-collapse': [];
  'close-mobile': [];
  compose: [];
  navigate: [key: string];
  'select-account': [accountId: string];
  'select-folder': [path: string];
  'reauthorize-account': [accountId: string];
  'open-notifications': [];
  'change-password': [];
  logout: [];
}>();
```

Delete `selectMailNavigation()` entirely. Add a tiny `requestReauthorization(accountId: string)` helper in `AppSidebar.vue` that emits `reauthorize-account` and then `close-mobile`; use it from the trailing reauthorization button. Account/folder/unified navigation continues to close the mobile drawer through `requestNavigation()` in `App.vue`.

- [ ] **Step 6: Replace mobile-only mail CSS with shared sidebar mail CSS**

In `app-shell.css`:

- keep `.sidebar-scroll` as the only vertical scroll owner;
- add section title, account row, account copy, reauth button, folder copy/count styles outside the mobile media query;
- style `.sidebar-primary-actions` as fixed `flex: 0 0 auto` content between header and scroll region;
- make `.sidebar-compose-action` the single accent-filled primary action while preserving focus-visible behavior inherited from button primitives/global rules;
- hide section titles, label copy, folder counts, and trailing reauth button in the 72px collapsed state; show a small warning indicator on the account icon for expired authorization rather than overlapping the account icon with a second button;
- remove all `.mobile-mail-navigation`, `.mobile-account-*`, and `.mobile-folder-*` selectors;
- inside `@media (max-width: 960px)`, keep the drawer geometry and force labels/section titles/account copy/folder counts/reauth buttons visible again.

Do not change the 248/72/960 dimensions.

- [ ] **Step 7: Run the sidebar/layout tests**

Run:

```bash
cd frontend
node --test tests/mail-sidebar-refactor.test.mjs tests/ui-layout.test.mjs
```

Expected result: the single-sidebar DOM, active-state, mobile-reuse, and geometry contracts pass. MailList-removal assertions may still fail until Task 6.

---

### Task 5: Move low-frequency product navigation into `UserMenu.vue`

**Files:**
- Modify: `frontend/src/components/app/UserMenu.vue:34-68`
- Modify: `frontend/src/styles/app-shell.css:397-478, 910-918`
- Test: `frontend/tests/mail-sidebar-refactor.test.mjs`

**Interfaces:**
- Consumes: existing `navigate(key)` event path from `UserMenu.vue` -> `AppSidebar.vue` -> `App.vue` -> `requestNavigation()`.
- Produces: direct menu destinations `contacts`, `history-sync`, `accounts`, `backup`, while retaining existing profile/signatures/notifications/settings/users/about/password/logout behavior.

- [ ] **Step 1: Add a failing user-menu contract**

Append:

```js
test('low-frequency product areas live in the user menu', async () => {
  const menu = await read('src/components/app/UserMenu.vue');
  const sidebar = await read('src/components/app/AppSidebar.vue');

  for (const [key, label] of [
    ['contacts', '联系人'],
    ['history-sync', '同步管理'],
    ['accounts', '账号管理'],
    ['backup', '邮件备份'],
  ]) {
    assert.match(menu, new RegExp(`navigate\\('${key}'\\)[\\s\\S]*${label}`));
  }
  assert.doesNotMatch(sidebar, />联系人</);
  assert.doesNotMatch(sidebar, />同步管理</);
  assert.doesNotMatch(sidebar, />账号管理</);
  assert.doesNotMatch(sidebar, />邮件备份</);
});
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
cd frontend
node --test tests/mail-sidebar-refactor.test.mjs
```

Expected result: FAIL because those destinations are not yet in `UserMenu.vue`.

- [ ] **Step 3: Implement the approved menu grouping without nested menus**

Render the menu in this order:

```text
Summary
联系人
同步管理
账号管理
邮件备份
---
个人资料
签名管理
第三方通知
设置
---
用户管理 (admin only)
---
关于
修改密码
退出登录
```

Use existing `AppIcon` names already present elsewhere: `contacts`, `sync`, `accounts`, `backup`, existing profile/signature/notifications/settings/users/info/lock/logout icons.

Do not add a submenu or new state.

- [ ] **Step 4: Make the user menu viewport-bounded and internally scrollable**

Add to `.user-menu-popover`:

```css
max-height: calc(100dvh - 96px);
overflow-x: hidden;
overflow-y: auto;
overscroll-behavior: contain;
```

Keep the existing desktop fixed positioning and mobile absolute positioning. The menu must not participate in app-shell layout sizing.

- [ ] **Step 5: Run the focused tests**

Run:

```bash
cd frontend
node --test tests/mail-sidebar-refactor.test.mjs tests/ui-layout.test.mjs
```

Expected result: user-menu ownership and floating/scroll behavior pass.

---

### Task 6: Remove duplicate navigation from `MailList.vue` and make the mail workspace single-column

**Files:**
- Modify: `frontend/src/views/MailList.vue:1-65, 394-440, 942-1066, 1666-1731, 3121-3265, 3313-3318, 3606-3613`
- Test: `frontend/tests/mail-sidebar-refactor.test.mjs`
- Test: `frontend/tests/ui-layout.test.mjs`
- Test: `frontend/tests/mail-search-layout.test.mjs`

**Interfaces:**
- Consumes: app-shell updates to `mailStore.currentAccountId/currentFolder`; existing watcher at current lines 942-955 resets visible list state and calls `loadMessages()`.
- Produces: one-column MailList workspace; retains mobile folder-drawer launcher, single-account reauth banner, search/filter/conversation/select/detail/reply/forward/attachment behavior.

- [ ] **Step 1: Run the existing watcher contract mentally against the new ownership and add the missing negative assertions first**

Ensure `mail-sidebar-refactor.test.mjs` requires all of these to be absent from `MailList.vue`:

```js
assert.doesNotMatch(mail, /class="folder-sidebar"/);
assert.doesNotMatch(mail, /class="account-switcher"/);
assert.doesNotMatch(mail, /function switchAccount\(/);
assert.doesNotMatch(mail, /function handleMailNavigation\(/);
assert.doesNotMatch(mail, /class="compose-entry-btn"/);
assert.doesNotMatch(mail, /function openCompose\(/);
```

Keep a positive assertion for `flymail-toggle-sidebar`, because the mobile current-folder button must still open the shared drawer.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
cd frontend
node --test tests/mail-sidebar-refactor.test.mjs tests/ui-layout.test.mjs
```

Expected result: FAIL on the old desktop sidebar, compose entry, switchAccount, and mail-navigation handler.

- [ ] **Step 3: Remove the desktop sidebar template and local global compose entry**

Delete current template lines 10-53 (`folder-sidebar`) and current lines 60-65 (`compose-entry-btn`). Keep:

- mobile `.folder-picker` at current lines 73-76;
- desktop read-only `.list-count` at current lines 78-80;
- all search/filter/list/detail/pagination markup;
- the single-account reauthorization banner, now calling `reauthorizeAccount()` from Task 2.

- [ ] **Step 4: Remove obsolete navigation code and imports**

Delete:

- `AccountIcon` import;
- local `accountDisplayName()` and `folderIconName()` helpers that only served the removed desktop sidebar;
- `handleMailNavigation()`;
- `flymail-mail-navigation` add/remove listeners;
- `switchAccount()`;
- `openCompose()`.

Keep `navigateToCompose()` because reply, forward, and draft opening still use the generic `flymail-navigate` path. Keep `openMobileSidebar()` and `flymail-toggle-sidebar` for the mobile drawer launcher.

- [ ] **Step 5: Simplify the mail-shell grid and remove old sidebar CSS only**

Change:

```css
.mail-shell {
  ...
  grid-template-columns: minmax(0, 1fr);
  gap: 0;
}

.mail-shell.detail {
  grid-template-columns: minmax(0, 1fr);
}
```

Delete `.compose-entry-btn`, `.folder-sidebar`, `.folder-sidebar-header`, `.folder-nav-list`, `.folder-nav-item`, `.folder-nav-name`, `.folder-nav-count`, `.account-switcher`, `.account-switcher-row`, `.account-switcher-item`, `.account-switcher-copy`, `.account-reauth`, and their reduced-motion references because those selectors correspond directly to the live markup removed in this task. Leave older unrelated dead selectors such as `.account-tabs` untouched because they predate this refactor. Do not alter mail-row density, status badges, pagination, detail toolbar, body overflow, attachment styling, or unrelated responsive behavior in this task.

- [ ] **Step 6: Run focused mail layout/search tests**

Run:

```bash
cd frontend
node --test \
  tests/mail-sidebar-refactor.test.mjs \
  tests/ui-layout.test.mjs \
  tests/mail-search-layout.test.mjs \
  tests/mail-reading-layout.test.mjs
```

Expected result: all pass. Search-toolbar height and mail-reading scroll contracts must remain unchanged.

---

### Task 7: Update permanent design/documentation contracts and release version

**Files:**
- Modify: `DESIGN.md`
- Modify: `README.md`
- Modify: `frontend/tests/design-contract.test.mjs`
- Modify: `VERSION`
- Generated by sync script: `package.json`, `frontend/package.json`, `docker-compose.yml`, `README.md`

**Interfaces:**
- Consumes: implemented UI behavior from Tasks 3-6.
- Produces: stable design invariant and user-facing README description matching the implementation; version `0.0.46` everywhere.

- [ ] **Step 1: Add a failing permanent design-contract assertion**

Extend `frontend/tests/design-contract.test.mjs` so `DESIGN.md` must state, in Chinese, that the permanent desktop sidebar carries mail navigation and low-frequency management/personal functions live in the user menu. For example, assert concepts equivalent to:

```js
assert.match(design, /永久侧栏[^\n]*邮件导航/);
assert.match(design, /管理[^\n]*用户菜单|用户菜单[^\n]*管理/);
```

- [ ] **Step 2: Run the design-contract test and verify RED**

Run:

```bash
cd frontend
node --test tests/design-contract.test.mjs
```

Expected result: FAIL because the new information-architecture invariant is not yet in `DESIGN.md`.

- [ ] **Step 3: Update `DESIGN.md` with one stable information-architecture rule**

Add the rule near the stable-app-shell/navigation guidance, without duplicating the entire task spec:

```text
桌面应用壳的永久侧栏以邮件工作流为主，承载写邮件、聚合收件箱、邮箱账号和文件夹；联系人、同步管理、账号管理、邮件备份以及个人/系统管理入口使用用户菜单。移动端复用同一导航内容并在 960px 断点切换为抽屉，不维护第二套账号/文件夹模板。
```

Also state that folder strong-active styling is only valid on the mail page so non-mail pages do not misrepresent the current view.

- [ ] **Step 4: Update README’s existing navigation capability paragraph**

Replace the outdated text describing the sidebar as a global primary function nav. The updated paragraph must describe:

- 248px expanded / 72px collapsed;
- write mail + optional unified inbox + accounts + folders in the permanent sidebar;
- Contacts / Sync / Accounts / Backup plus personal/admin settings in the avatar menu;
- mobile uses the same navigation content as a drawer.

Do not change unrelated capability descriptions.

- [ ] **Step 5: Bump `VERSION` to `0.0.46` and run the repository version synchronizer**

Set `VERSION` to:

```text
0.0.46
```

Then run:

```bash
npm run sync-version
cat VERSION
node -e "console.log(require('./package.json').version)"
node -e "console.log(require('./frontend/package.json').version)"
```

Expected output for all three version reads: `0.0.46`.

Verify `docker-compose.yml` and README image references are `benxianyu/flymail:0.0.46`.

- [ ] **Step 6: Re-run design and version-focused checks**

Run:

```bash
cd frontend
node --test tests/design-contract.test.mjs tests/mail-sidebar-refactor.test.mjs
cd ..
git diff --check
```

Expected result: PASS.

---

### Task 8: Run full regression, build the image, validate an isolated container, deploy safely, then commit and push

**Files:**
- Verify all task files.
- No additional source files unless a failing verification directly proves a regression introduced by this refactor.

**Interfaces:**
- Consumes: complete implementation and version `0.0.46`.
- Produces: verified local image `benxianyu/flymail:0.0.46`, healthy current container `flymail`, one task commit on `main`, pushed to `origin/main`.

- [ ] **Step 1: Run the complete frontend test suite and production build**

Run:

```bash
cd frontend
npm install
npm test
npm run build
```

Expected result: all Node tests pass, `vue-tsc` passes, Vite production build completes successfully, and no dependency change is introduced by `npm install` beyond the existing lockfile state.

- [ ] **Step 2: Run the complete backend regression suite**

Run:

```bash
cd backend
python -m unittest discover -s tests -v
```

Expected result: all backend tests pass. This refactor does not change backend code; any failure must be investigated rather than skipped.

- [ ] **Step 3: Run shell, Compose, and Git hygiene checks**

Run from repository root:

```bash
bash -n scripts/docker-entrypoint.sh
docker compose config
git diff --check
git status --short
git diff
```

Expected result:

- shell syntax valid;
- Compose config valid and references `benxianyu/flymail:0.0.46`;
- no whitespace errors;
- only files from this plan are modified;
- no `.env`, credentials, logs, databases, attachments, or runtime data appear in the diff.

- [ ] **Step 4: Build the release image locally**

Run:

```bash
docker build -t benxianyu/flymail:$(cat VERSION) .
```

Expected result: image `benxianyu/flymail:0.0.46` builds successfully. Do not run `docker login` or `docker push`.

- [ ] **Step 5: Start an isolated temporary container using a temporary data directory and a special-character MySQL password**

Use an isolated container name/port and never mount `/Docker/flymail/data`:

```bash
tmpdir="$(mktemp -d /tmp/flymail-sidebar-XXXXXX)"
name="flymail-sidebar-test"
mysql_pw='Tmp"\@:/%Pass42'
session_secret="$(openssl rand -hex 32)"

docker run -d \
  --name "$name" \
  -p 127.0.0.1:18081:8080 \
  -e FLYMAIL_ADMIN_USERNAME=testadmin \
  -e FLYMAIL_ADMIN_PASSWORD='TempAdminPass42!' \
  -e FLYMAIL_SESSION_SECRET="$session_secret" \
  -e MYSQL_DATABASE=flymail \
  -e MYSQL_USER=flymail \
  -e MYSQL_PASSWORD="$mysql_pw" \
  -v "$tmpdir:/data" \
  benxianyu/flymail:0.0.46
```

Expected result: container starts without writing anywhere under `/Docker/flymail/data`.

- [ ] **Step 6: Wait for health and verify version, MySQL version/data directory, app data directory, and database read/write**

Run:

```bash
for i in $(seq 1 90); do
  status="$(docker inspect -f '{{.State.Health.Status}}' "$name" 2>/dev/null || true)"
  [ "$status" = healthy ] && break
  sleep 2
done

docker inspect -f '{{.State.Health.Status}}' "$name"
curl -fsS http://127.0.0.1:18081/api/health
docker exec "$name" mysql --version
docker exec "$name" sh -lc 'mysql --protocol=socket --socket=/run/mysqld/mysqld.sock -uroot -Nse "SELECT @@datadir;"'
docker exec "$name" test -d /data/flymail
docker exec "$name" sh -lc 'mysql --protocol=tcp -h127.0.0.1 -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE" -e "CREATE TABLE IF NOT EXISTS persistence_probe (id INT PRIMARY KEY, value VARCHAR(64)); INSERT INTO persistence_probe (id, value) VALUES (1, '\''sidebar-ok'\'') ON DUPLICATE KEY UPDATE value=VALUES(value); SELECT value FROM persistence_probe WHERE id=1;"'
```

Expected result:

- health status `healthy`;
- `/api/health` reports version `0.0.46`;
- MySQL reports 8.0.x;
- `@@datadir` is `/data/mysql/`;
- `/data/flymail` exists;
- probe query returns `sidebar-ok`.

- [ ] **Step 7: Verify restart persistence, log redaction, and image metadata secret hygiene**

Run:

```bash
docker restart "$name"
for i in $(seq 1 90); do
  status="$(docker inspect -f '{{.State.Health.Status}}' "$name" 2>/dev/null || true)"
  [ "$status" = healthy ] && break
  sleep 2
done

docker exec "$name" sh -lc 'mysql --protocol=tcp -h127.0.0.1 -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE" -Nse "SELECT value FROM persistence_probe WHERE id=1;"'

if docker logs "$name" 2>&1 | grep -F "$mysql_pw"; then
  echo 'temporary MySQL password leaked into logs' >&2
  exit 1
fi

if docker image inspect benxianyu/flymail:0.0.46 --format '{{json .Config.Env}}' | grep -Eq 'MYSQL_PASSWORD=|FLYMAIL_SESSION_SECRET=|FLYMAIL_ADMIN_PASSWORD='; then
  echo 'secret-bearing environment variable found in image metadata' >&2
  exit 1
fi
```

Expected result: probe still returns `sidebar-ok`; temporary MySQL password is absent from logs; image metadata contains no password/session/admin secret values.

- [ ] **Step 8: Perform browser-visible UI checks against the isolated container**

Before this check, read the installed `webapp-testing` skill and use its native Python Playwright workflow with headless Chromium against `http://127.0.0.1:18081`. The environment already provides Python Playwright and a Chromium executable, so no project dependency is added. Log in with the temporary admin account and verify at least:

```text
1440×900: exactly one sidebar; 248px expanded; user menu contains Contacts/Sync/Accounts/Backup.
1920×1080: workspace consumes released width; no second MailList folder sidebar.
390×844: drawer width fits viewport; same compose/unified/account/folder structure; no page-level horizontal overflow.
200% zoom: labels/menu remain operable; sidebar/user menu do not push controls outside the viewport.
No-account state: compose is disabled and the sidebar shows “暂无邮箱账号” without breaking layout.
```

Because the isolated container has no real mailbox credentials, account/folder rendering with real provider data remains a post-deployment/browser risk to report explicitly; do not add fake provider credentials just to satisfy the visual check.

- [ ] **Step 9: Verify graceful shutdown and clean temporary runtime data**

Run:

```bash
docker stop -t 30 "$name"
grep -i "shutdown" "$tmpdir/mysql/error.log" | tail -20 || true
docker rm "$name"
rm -rf "$tmpdir"
```

Expected result: container stops within timeout; MySQL error log shows normal shutdown activity; only the isolated `/tmp/flymail-sidebar-*` directory is removed.

- [ ] **Step 10: Safely recreate the current `flymail` container using the verified local image and existing persistent mount**

First verify the current mount before replacement:

```bash
docker inspect flymail --format '{{range .Mounts}}{{.Source}}:{{.Destination}}{{println}}{{end}}'
```

Expected result must include:

```text
/Docker/flymail/data:/data
```

If that mount is not present, stop and investigate instead of recreating the container.

Then deploy the already-built local image without Docker Hub:

```bash
docker compose up -d --force-recreate --no-build
```

Wait for health and verify:

```bash
for i in $(seq 1 90); do
  status="$(docker inspect -f '{{.State.Health.Status}}' flymail 2>/dev/null || true)"
  [ "$status" = healthy ] && break
  sleep 2
done

docker inspect -f '{{.State.Status}} {{.State.Health.Status}}' flymail
docker exec flymail python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4).read().decode())"
docker exec flymail mysql --version
docker exec flymail sh -lc 'mysql --protocol=socket --socket=/run/mysqld/mysqld.sock -uroot -Nse "SELECT @@datadir;"'
docker inspect flymail --format '{{range .Mounts}}{{.Source}}:{{.Destination}}{{println}}{{end}}'
```

Expected result: running + healthy, app version `0.0.46`, MySQL 8.0.x, datadir `/data/mysql/`, and `/Docker/flymail/data:/data` unchanged.

Do not perform destructive database probes on the real persistent data; the read/write/restart persistence proof comes from the isolated test container.

- [ ] **Step 11: Re-read README and final diff, stage only task files, then commit**

Run:

```bash
git status --short
git diff --check
git diff
git add \
  VERSION \
  package.json \
  docker-compose.yml \
  README.md \
  DESIGN.md \
  frontend/package.json \
  frontend/src/App.vue \
  frontend/src/components/app/AppSidebar.vue \
  frontend/src/components/app/UserMenu.vue \
  frontend/src/composables/useAccountReauthorization.ts \
  frontend/src/views/MailList.vue \
  frontend/src/styles/app-shell.css \
  frontend/tests/mail-sidebar-refactor.test.mjs \
  frontend/tests/ui-layout.test.mjs \
  frontend/tests/product-ui-redesign.test.mjs \
  frontend/tests/design-contract.test.mjs \
  docs/superpowers/plans/2026-08-10-mail-first-sidebar-refactor.md

git diff --staged
```

Confirm the staged diff contains only this task, no secrets, and README reflects the implemented behavior.

Commit with:

```bash
git commit -m "🎨 重构邮件优先侧边栏统一导航"
```

- [ ] **Step 12: Push `main` and verify the final repository state**

Run:

```bash
git push origin main
```

If SSH port 22 is unavailable, retry only with the approved GitHub SSH 443 fallback:

```bash
GIT_SSH_COMMAND='ssh -p 443 -o HostName=ssh.github.com' git push origin main
```

Then run:

```bash
git status --short --branch
git log -1 --oneline
```

Expected result: `main` is synchronized with `origin/main`, working tree is clean, and the final commit is `🎨 重构邮件优先侧边栏统一导航`.

---

## Final Verification Matrix

The implementation is complete only when every row below is evidenced by an executed check:

| Requirement | Evidence |
|---|---|
| One desktop sidebar | `mail-sidebar-refactor.test.mjs` + browser 1440/1920 |
| 248px / 72px / 960px geometry | `ui-layout.test.mjs` + CSS inspection + browser |
| Compose is global and disabled with no account | focused source contract + no-account browser check |
| Unified inbox remains mail navigation | source contract + sidebar browser check |
| Accounts/folders owned by app shell | focused source contract |
| Folder active only on mail page | focused source contract |
| Contacts/Sync/Accounts/Backup in user menu | focused source contract + browser menu check |
| Desktop/mobile share one navigation DOM | negative `mobile-mail-navigation` contract |
| `flymail-mail-navigation` removed | negative source contract |
| Signature unsaved guard preserved | `signature-navigation-contract.test.mjs` + app-handler ordering contract |
| Search/filter/conversation/mail reading unchanged | full frontend tests + focused mail layout tests |
| No page-level mobile overflow | 390×844 browser check |
| 200% zoom operable | browser check |
| Backend unchanged/regression-free | backend full unittest suite |
| Shell/Compose valid | `bash -n` + `docker compose config` |
| Image builds | `docker build` |
| Temporary container healthy/version correct | Docker health + `/api/health` |
| MySQL 8.0 `/data/mysql/` | temp container MySQL checks |
| `/data/flymail` exists | temp container filesystem check |
| DB read/write + restart persistence | `persistence_probe` on isolated temp data |
| Password redacted / image has no secrets | log grep + image metadata check |
| Graceful MySQL shutdown | temp container stop + MySQL log check |
| Real persistent mount unchanged | current-container mount inspection before and after recreate |
| README/DESIGN synchronized | design-contract test + final diff review |
| Version synchronized | `VERSION`, root package, frontend package = `0.0.46` |
| Code pushed, Docker Hub untouched | Git push evidence; no Docker Hub command |

## Implementation Risks to Report at Handoff

- The isolated container can validate the no-account sidebar state but cannot prove populated account/folder visual density against Gmail/Outlook/QQ/custom IMAP data without real mailbox credentials.
- OAuth reauthorization popup behavior still depends on browser/WebView popup policy and real Gmail/Microsoft settings; extraction must preserve the current behavior but cannot make third-party authorization independently testable offline.
- Real mailbox connectivity, proxy configuration, provider-specific custom folders, and the final visual result with the user’s actual account names/counts still require real-account/browser confirmation after deployment.
- No database or persistence migration is part of this refactor; any observed data change would be a regression and must stop deployment.
