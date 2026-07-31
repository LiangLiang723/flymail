# FlyMail Product UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild FlyMail’s authenticated application shell and every top-level page into one cohesive, responsive, product-grade UI while preserving all existing mail, account, user, notification, and persistence behavior.

**Architecture:** Keep the current Vue 3 application and four `PageFrame` templates, but replace the accidental per-page width and surface rules with explicit `fluid | form | reading` width modes. Consolidate reusable controls into shared UI components, make the sidebar’s collapsed brand slot behave like ChatGPT, and migrate pages in functional groups so each commit remains usable and independently verifiable.

**Tech Stack:** Vue 3, TypeScript, Pinia, scoped CSS, semantic CSS tokens, Node test runner, Vite, FastAPI, MySQL 8.0, Docker.

## Global Constraints

- Work only in `/home/chatgpt/flymail` on branch `main`.
- Keep the existing FastAPI, Vue 3, Pinia, IMAP, SMTP, WebSocket, MySQL, and single-container architecture.
- Do not add or upgrade production dependencies.
- Do not change APIs, authentication, permissions, mailbox behavior, user isolation, notification behavior, or database schemas.
- Do not modify or delete `/Docker/flymail/data`; temporary validation must use a separate directory and container name.
- Keep desktop sidebar widths at 248px expanded and 72px collapsed.
- Collapsed sidebar default state shows the FlyMail Logo; only hover or keyboard focus on the shared brand slot reveals the expand icon.
- Fluid workspaces must use the available width and must not inherit a fixed 1280px maximum.
- Form pages use a left-aligned maximum width of 1120px; reading pages use a left-aligned maximum width of 960px.
- Mobile workspaces are edge-to-edge below 960px; form and reading pages retain a compact gutter.
- Shared color values belong in `tokens.css`; shared component appearance belongs in `components.css`; page scoped styles may only describe page-specific structure.
- Support light theme, dark theme, `prefers-reduced-motion`, `prefers-reduced-transparency`, and `prefers-contrast`.
- Final release target is `0.0.23`.

---

### Task 1: Replace Accidental Width Rules with Explicit Page Contracts

**Files:**
- Modify: `frontend/src/components/layout/PageFrame.vue`
- Modify: `frontend/src/styles/tokens.css`
- Modify: `frontend/src/styles/layout-system.css`
- Modify: `frontend/tests/design-system.test.mjs`
- Modify: `frontend/tests/page-templates.test.mjs`

**Interfaces:**
- Produces: `PageWidth = 'fluid' | 'form' | 'reading'`.
- Produces: `PageFrame` prop `width?: PageWidth`.
- Produces: `.page-frame__shell`, `.page-frame--width-fluid`, `.page-frame--width-form`, and `.page-frame--width-reading` contracts.
- Produces: `--page-gutter`, `--page-gutter-compact`, `--page-form-max`, and `--page-reading-max` tokens.

- [ ] **Step 1: Add failing token and template tests**

Add these assertions to `frontend/tests/design-system.test.mjs`:

```js
test('page width tokens distinguish fluid workspaces from bounded forms', async () => {
  const tokens = await readSource('src/styles/tokens.css');

  assert.match(tokens, /--page-gutter:\s*24px/);
  assert.match(tokens, /--page-gutter-compact:\s*16px/);
  assert.match(tokens, /--page-form-max:\s*1120px/);
  assert.match(tokens, /--page-reading-max:\s*960px/);
  assert.doesNotMatch(tokens, /--page-content-max:\s*1280px/);
});
```

Replace the current bounded-column assertions in `frontend/tests/page-templates.test.mjs` with:

```js
test('page frames expose explicit fluid form and reading widths', async () => {
  const frame = await read('src/components/layout/PageFrame.vue');
  const layout = await read('src/styles/layout-system.css');

  assert.match(frame, /type PageWidth = 'fluid' \| 'form' \| 'reading'/);
  assert.match(frame, /width\?: PageWidth/);
  assert.match(frame, /page-frame__shell/);
  assert.match(layout, /\.page-frame--width-fluid\s*\{[^}]*--page-frame-max:\s*none/s);
  assert.match(layout, /\.page-frame--width-form\s*\{[^}]*--page-frame-max:\s*var\(--page-form-max\)/s);
  assert.match(layout, /\.page-frame--width-reading\s*\{[^}]*--page-frame-max:\s*var\(--page-reading-max\)/s);
  assert.match(layout, /\.page-frame__shell\s*\{[^}]*max-width:\s*var\(--page-frame-max\);[^}]*margin-right:\s*auto/s);
});
```

- [ ] **Step 2: Run the targeted tests and confirm RED**

Run:

```bash
cd frontend
npm test -- --test-name-pattern="page width tokens|page frames expose explicit"
```

Expected: both new tests fail because the tokens, prop, shell, and width classes do not exist.

- [ ] **Step 3: Implement the `PageFrame` width API**

Refactor `PageFrame.vue` to use this script contract:

```ts
import { computed } from 'vue';

type PageTemplate = 'workspace' | 'management' | 'split' | 'document';
type PageWidth = 'fluid' | 'form' | 'reading';

const props = withDefaults(defineProps<{
  template?: PageTemplate;
  width?: PageWidth;
}>(), {
  template: 'management',
});

const resolvedWidth = computed<PageWidth>(() => (
  props.width || (props.template === 'document' ? 'form' : 'fluid')
));
```

Wrap the optional header, optional toolbar, and body in one `.page-frame__shell`, and bind `page-frame--width-${resolvedWidth}` on the root section. Keep the existing slot names and classes so page business markup does not change.

- [ ] **Step 4: Replace width and gutter tokens**

In `tokens.css`, replace the previous page sizing values with:

```css
--page-gutter: 24px;
--page-gutter-compact: 16px;
--page-section-gap: 16px;
--page-form-max: 1120px;
--page-reading-max: 960px;
--panel-padding: 16px;
```

Retain compatibility aliases only where an unmigrated page still requires them, and remove the alias before Task 11 finishes.

- [ ] **Step 5: Rebuild the shared page layout**

Implement these structural rules in `layout-system.css`:

```css
.page-frame {
  --page-frame-max: none;
  padding: var(--page-gutter);
}

.page-frame__shell {
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  max-width: var(--page-frame-max);
  margin-left: 0;
  margin-right: auto;
  display: grid;
}

.page-frame--width-fluid { --page-frame-max: none; }
.page-frame--width-form { --page-frame-max: var(--page-form-max); }
.page-frame--width-reading { --page-frame-max: var(--page-reading-max); }
```

Move page gutter ownership to the `PageFrame` root. Remove management-specific centering and the hard `1280px` cap. Preserve deterministic scroll ownership for workspace, management, split, and document bodies.

Under `@media (max-width: 960px)`, set workspace and split root padding to `0`, and set document/management padding to `var(--page-gutter-compact)`.

- [ ] **Step 6: Run targeted and complete layout tests**

Run:

```bash
cd frontend
npm test -- --test-name-pattern="page width tokens|page frames expose explicit|page frames adapt|each page template owns"
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit the page contract**

```bash
git add frontend/src/components/layout/PageFrame.vue frontend/src/styles/tokens.css frontend/src/styles/layout-system.css frontend/tests/design-system.test.mjs frontend/tests/page-templates.test.mjs
git commit -m "🎨 重建页面宽度与外层间距契约"
```

---

### Task 2: Finish the Shared Component System Before Migrating Pages

**Files:**
- Create: `frontend/src/components/ui/UiField.vue`
- Create: `frontend/src/components/ui/UiBadge.vue`
- Create: `frontend/src/components/ui/UiSegmentedControl.vue`
- Modify: `frontend/src/components/ui/UiCard.vue`
- Modify: `frontend/src/components/ui/UiButton.vue`
- Modify: `frontend/src/styles/components.css`
- Modify: `frontend/src/styles/page-system.css`
- Modify: `frontend/tests/design-system.test.mjs`
- Create: `frontend/tests/product-ui-redesign.test.mjs`

**Interfaces:**
- Produces: `UiField` props `label`, `forId`, `hint`, `error`, and `required`.
- Produces: `UiBadge` props `tone?: 'neutral' | 'accent' | 'success' | 'warning' | 'danger'` and `size?: 'sm' | 'md'`.
- Produces: `UiSegmentedControl` props `modelValue`, `options`, and `label`; emits `update:modelValue`.
- Extends: `UiCard` props `variant?: 'default' | 'subtle' | 'raised'` and `padding?: 'none' | 'sm' | 'md' | 'lg'`.
- Produces: shared `.ui-input`, `.ui-select`, `.ui-textarea`, `.ui-checkbox`, `.ui-section`, `.ui-stat-grid`, `.ui-list-row`, and `.ui-detail-grid` patterns.

- [ ] **Step 1: Add failing component contract tests**

Append to `design-system.test.mjs`:

```js
test('product primitives cover fields badges segmented controls and card density', async () => {
  const field = await readSource('src/components/ui/UiField.vue');
  const badge = await readSource('src/components/ui/UiBadge.vue');
  const segmented = await readSource('src/components/ui/UiSegmentedControl.vue');
  const card = await readSource('src/components/ui/UiCard.vue');

  assert.match(field, /error\?: string/);
  assert.match(field, /ui-field__message/);
  assert.match(badge, /'neutral' \| 'accent' \| 'success' \| 'warning' \| 'danger'/);
  assert.match(segmented, /update:modelValue/);
  assert.match(segmented, /aria-label/);
  assert.match(card, /'default' \| 'subtle' \| 'raised'/);
  assert.match(card, /'none' \| 'sm' \| 'md' \| 'lg'/);
});
```

Create `product-ui-redesign.test.mjs` with shared helpers and this first test:

```js
import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (file) => readFile(path.join(root, file), 'utf8');

test('shared page patterns own reusable controls and surfaces', async () => {
  const components = await read('src/styles/components.css');
  const pages = await read('src/styles/page-system.css');

  for (const selector of ['.ui-input', '.ui-select', '.ui-textarea', '.ui-checkbox', '.ui-badge', '.ui-segmented']) {
    assert.match(components, new RegExp(selector.replace('.', '\\.')));
  }
  for (const selector of ['.ui-section', '.ui-stat-grid', '.ui-list-row', '.ui-detail-grid']) {
    assert.match(pages, new RegExp(selector.replace('.', '\\.')));
  }
});
```

- [ ] **Step 2: Run the component tests and confirm RED**

```bash
cd frontend
npm test -- --test-name-pattern="product primitives|shared page patterns"
```

Expected: failure because the three components and shared selectors are missing.

- [ ] **Step 3: Implement `UiField`**

Use this markup contract:

```vue
<label class="ui-field" :for="forId">
  <span v-if="label" class="ui-field__label">
    {{ label }}<span v-if="required" aria-hidden="true"> *</span>
  </span>
  <slot />
  <span v-if="error || hint" class="ui-field__message" :class="{ 'is-error': Boolean(error) }">
    {{ error || hint }}
  </span>
</label>
```

Expose the exact props named in the Interfaces section. Do not generate input IDs internally; pages pass the native control ID through `forId`.

- [ ] **Step 4: Implement `UiBadge` and `UiSegmentedControl`**

Use this option type in `UiSegmentedControl.vue`:

```ts
interface SegmentOption {
  value: string;
  label: string;
  count?: number;
  disabled?: boolean;
}
```

Render buttons with `aria-pressed`, preserve disabled options, and emit the selected string value. Render counts only when the option explicitly provides one.

- [ ] **Step 5: Extend `UiCard` and shared styles**

Bind card classes as:

```vue
<section
  class="ui-card"
  :class="[
    `ui-card--${variant}`,
    `ui-card--padding-${padding}`,
    { 'ui-card--interactive': interactive },
  ]"
>
```

Implement all new component and page pattern styles with semantic tokens. Do not add fixed hex or `rgb/rgba` values outside `tokens.css`.

- [ ] **Step 6: Run targeted tests and the production type checker**

```bash
cd frontend
npm test -- --test-name-pattern="product primitives|shared page patterns|shared component styles"
npm run build
```

Expected: tests pass and `vue-tsc` reports no errors.

- [ ] **Step 7: Commit the component foundation**

```bash
git add frontend/src/components/ui/UiField.vue frontend/src/components/ui/UiBadge.vue frontend/src/components/ui/UiSegmentedControl.vue frontend/src/components/ui/UiCard.vue frontend/src/components/ui/UiButton.vue frontend/src/styles/components.css frontend/src/styles/page-system.css frontend/tests/design-system.test.mjs frontend/tests/product-ui-redesign.test.mjs
git commit -m "✨ 完善整站表单卡片与状态组件"
```

---

### Task 3: Rebuild the App Shell and ChatGPT-Style Collapsed Sidebar

**Files:**
- Modify: `frontend/src/components/app/AppSidebar.vue`
- Modify: `frontend/src/components/app/UserMenu.vue`
- Modify: `frontend/src/components/app/NotificationDrawer.vue`
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/styles/app-shell.css`
- Modify: `frontend/tests/ui-layout.test.mjs`
- Modify: `frontend/tests/product-ui-redesign.test.mjs`

**Interfaces:**
- Produces: collapsed-only button `.sidebar-collapsed-toggle` containing `.sidebar-collapsed-logo` and `.sidebar-collapsed-expand`.
- Preserves: `toggle-collapse`, `navigate`, `open-notifications`, `change-password`, `logout`, and `mail-navigation` events.
- Preserves: 248px expanded and 72px collapsed shell columns.

- [ ] **Step 1: Replace the sidebar regression expectations**

Update the collapsed-sidebar test in `ui-layout.test.mjs` to require one shared slot:

```js
test('collapsed sidebar swaps one brand slot from logo to expand icon on interaction', async () => {
  const component = await readSource('src/components/app/AppSidebar.vue');
  const css = await readSource('src/styles/app-shell.css');

  assert.match(component, /sidebar-collapsed-toggle/);
  assert.match(component, /sidebar-collapsed-logo/);
  assert.match(component, /sidebar-collapsed-expand/);
  assert.match(css, /\.sidebar-collapsed-toggle\s*\{[^}]*width:\s*44px;[^}]*height:\s*44px/s);
  assert.match(css, /\.sidebar-collapsed-logo\s*\{[^}]*opacity:\s*1/s);
  assert.match(css, /\.sidebar-collapsed-expand\s*\{[^}]*opacity:\s*0/s);
  assert.match(css, /\.sidebar-collapsed-toggle:hover \.sidebar-collapsed-logo,[\s\S]*\.sidebar-collapsed-toggle:focus-visible \.sidebar-collapsed-logo\s*\{[^}]*opacity:\s*0/s);
  assert.match(css, /\.sidebar-collapsed-toggle:hover \.sidebar-collapsed-expand,[\s\S]*\.sidebar-collapsed-toggle:focus-visible \.sidebar-collapsed-expand\s*\{[^}]*opacity:\s*1/s);
});
```

Add this source contract to `product-ui-redesign.test.mjs`:

```js
test('sidebar header renders mutually exclusive desktop controls', async () => {
  const source = await read('src/components/app/AppSidebar.vue');

  assert.match(source, /v-if="collapsed && !mobile"[\s\S]*class="sidebar-collapsed-toggle"/);
  assert.match(source, /v-if="!mobile"[\s\S]*class="sidebar-header-action"/);
  assert.match(source, /v-else[\s\S]*class="sidebar-mobile-close"/);
  assert.doesNotMatch(source, /app-shell\.sidebar-collapsed[\s\S]*sidebar-header-action/);
});
```

- [ ] **Step 2: Run the sidebar tests and confirm RED**

```bash
cd frontend
npm test -- --test-name-pattern="collapsed sidebar swaps one brand slot|expanded-only collapse"
```

Expected: failure because the current implementation uses a brand element and a separately positioned action.

- [ ] **Step 3: Refactor the sidebar header markup**

Implement three mutually exclusive states:

```vue
<div class="sidebar-header">
  <button
    v-if="collapsed && !mobile"
    class="sidebar-collapsed-toggle"
    type="button"
    aria-label="展开侧边栏"
    title="展开侧边栏"
    @click="$emit('toggle-collapse')"
  >
    <img class="sidebar-collapsed-logo" src="/icon.png" alt="" />
    <AppIcon class="sidebar-collapsed-expand" name="panel-left-open" :size="18" />
  </button>

  <template v-else>
    <div class="sidebar-brand">
      <span class="sidebar-brand-logo"><img src="/icon.png" alt="FlyMail" /></span>
      <span class="sidebar-brand-copy">
        <strong>FlyMail</strong>
        <small>Docker 多用户版</small>
      </span>
    </div>
    <button
      v-if="!mobile"
      class="sidebar-header-action"
      type="button"
      aria-label="折叠侧边栏"
      title="折叠侧边栏"
      @click="$emit('toggle-collapse')"
    >
      <AppIcon name="panel-left-close" :size="18" />
    </button>
    <button
      v-else
      class="sidebar-mobile-close"
      type="button"
      aria-label="关闭导航"
      @click="$emit('close-mobile')"
    >
      <AppIcon name="close" :size="19" />
    </button>
  </template>
</div>
```

The two visuals inside `.sidebar-collapsed-toggle` must occupy the same absolute center. The button itself remains the pointer and keyboard target in every state.

- [ ] **Step 4: Rebuild sidebar geometry and motion**

In `app-shell.css`:

- Keep one 72px collapsed rail.
- Center all main navigation, notification, and avatar controls on x=36px.
- Remove delayed `visibility` transitions used to hide overlap.
- Use only opacity and transform for brand visual swapping.
- Restrict the hover swap with `@media (hover: hover) and (pointer: fine)`; keyboard focus remains available outside that media query.
- Under `prefers-reduced-motion`, make the visual swap immediate.

- [ ] **Step 5: Align bottom navigation and floating menus**

Update `UserMenu.vue`, `NotificationDrawer.vue`, and relevant shell CSS so menu rows, notification rows, dividers, border radii, shadows, and focus states use the shared tokens and component geometry. Preserve all existing menu destinations and actions.

- [ ] **Step 6: Run shell tests and build**

```bash
cd frontend
npm test -- --test-name-pattern="application shell|responsive shell|collapsed sidebar|account menu"
npm run build
```

Expected: all selected tests and production build pass.

- [ ] **Step 7: Commit the shell redesign**

```bash
git add frontend/src/components/app/AppSidebar.vue frontend/src/components/app/UserMenu.vue frontend/src/components/app/NotificationDrawer.vue frontend/src/App.vue frontend/src/styles/app-shell.css frontend/tests/ui-layout.test.mjs frontend/tests/product-ui-redesign.test.mjs
git commit -m "🎨 重构应用壳层与折叠侧边栏交互"
```

---

### Task 4: Standardize Login, Toasts, Confirmation, and Global Floating Surfaces

**Files:**
- Modify: `frontend/src/views/LoginView.vue`
- Modify: `frontend/src/components/app/AppBootScreen.vue`
- Modify: `frontend/src/components/app/AuthGate.vue`
- Modify: `frontend/src/components/mail/ImageViewer.vue`
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/styles/app-shell.css`
- Modify: `frontend/src/styles/page-system.css`
- Modify: `frontend/tests/ui-layout.test.mjs`
- Modify: `frontend/tests/product-ui-redesign.test.mjs`

**Interfaces:**
- Produces: common floating surface treatment for login card, confirmation dialog, toast, notification drawer, user menu, and image viewer toolbar.
- Preserves: authentication state handling, login events, confirmation store behavior, and image viewer gestures.

- [ ] **Step 1: Add global-surface source tests**

Add:

```js
test('anonymous and global floating surfaces use shared product primitives', async () => {
  const login = await read('src/views/LoginView.vue');
  const app = await read('src/App.vue');
  const viewer = await read('src/components/mail/ImageViewer.vue');

  assert.match(login, /<UiCard/);
  assert.match(login, /<UiField/);
  assert.match(login, /<UiButton/);
  assert.match(app, /confirm-dialog/);
  assert.match(viewer, /UiIconButton/);
});
```

- [ ] **Step 2: Run the test and confirm RED**

```bash
cd frontend
npm test -- --test-name-pattern="anonymous and global floating surfaces"
```

Expected: login and image viewer do not yet use all required shared primitives.

- [ ] **Step 3: Migrate login and boot states**

Replace local login field/button/card appearance with `UiCard`, `UiField`, `UiButton`, and `UiAlert`. Keep the current API requests, error classification, password visibility, submit loading, and success event unchanged.

Align `AppBootScreen` with the same logo, typography, canvas, and loading treatment used by the authenticated shell.

- [ ] **Step 4: Standardize global overlays**

Keep the confirmation markup in `App.vue`, but move its visual treatment to shared floating-surface classes. Use common button primitives or the same shared `.btn` compatibility layer until all callers are migrated. Align Toast, confirmation, notification drawer, user menu, and image viewer toolbar radii, borders, shadows, and backdrop tokens.

- [ ] **Step 5: Verify authentication and viewer regressions**

```bash
cd frontend
npm test -- --test-name-pattern="authentication boot|auth bootstrap|login errors|image viewer|anonymous and global"
npm run build
```

Expected: authentication and image viewer behavior remains green.

- [ ] **Step 6: Commit global surface migration**

```bash
git add frontend/src/views/LoginView.vue frontend/src/components/app/AppBootScreen.vue frontend/src/components/app/AuthGate.vue frontend/src/components/mail/ImageViewer.vue frontend/src/App.vue frontend/src/styles/app-shell.css frontend/src/styles/page-system.css frontend/tests/ui-layout.test.mjs frontend/tests/product-ui-redesign.test.mjs
git commit -m "🎨 统一登录与全局浮层视觉"
```

---

### Task 5: Redesign the Unified Inbox as a Fluid Mail Workspace

**Files:**
- Modify: `frontend/src/views/UnifiedInbox.vue`
- Modify: `frontend/src/styles/page-system.css`
- Modify: `frontend/tests/page-templates.test.mjs`
- Modify: `frontend/tests/product-ui-redesign.test.mjs`

**Interfaces:**
- Consumes: `PageFrame width="fluid"`, `UiCard`, `UiButton`, `UiBadge`, `UiSegmentedControl`, `UiEmptyState`, and `UiLoadingState`.
- Preserves: account selection, filters, pagination, refresh, mark-all-read, and message navigation behavior.

- [ ] **Step 1: Add a failing unified-inbox page contract**

```js
test('unified inbox is a fluid workspace built from shared sections', async () => {
  const source = await read('src/views/UnifiedInbox.vue');

  assert.match(source, /<PageFrame[^>]*template="management"[^>]*width="fluid"/);
  assert.match(source, /<UiCard/);
  assert.match(source, /<UiSegmentedControl/);
  assert.match(source, /class="unified-account-layout"/);
  assert.match(source, /class="ui-list-row message-row"/);
  assert.doesNotMatch(source, /\.settings-card,[^}]*\.filter-bar,[^}]*\.message-list/);
});
```

- [ ] **Step 2: Run the test and confirm RED**

```bash
cd frontend
npm test -- --test-name-pattern="unified inbox is a fluid workspace"
```

- [ ] **Step 3: Rebuild the page hierarchy**

Use this order:

1. `PageHeader` with select, refresh, and mark-all-read actions.
2. `UiCard` account-selection section using `.unified-account-layout`.
3. `UiCard padding="sm"` filter/summary toolbar.
4. `UiCard padding="none"` message list or shared state.

At widths above 1200px, `.unified-account-layout` becomes a main selection column plus a 240–280px action/summary column. Below 1200px, it becomes one column.

- [ ] **Step 4: Migrate controls and rows**

Use `UiSegmentedControl` for all/unread/read/attachment filters, `UiBadge` for provider and counts, and `UiButton` for actions. Keep native checkbox semantics and current account IDs. Make the message list fluid and preserve the current grid columns, with responsive column reduction below 1100px.

- [ ] **Step 5: Remove only superseded local visual CSS**

Keep page-specific message grid definitions and responsive behavior. Remove local card, button, badge, empty-state, loading-state, border, radius, and outer-width rules now owned by shared styles.

- [ ] **Step 6: Verify unified inbox behavior and build**

```bash
cd frontend
npm test -- --test-name-pattern="unified inbox|top-level data pages|fluid workspace"
npm run build
```

- [ ] **Step 7: Commit unified inbox redesign**

```bash
git add frontend/src/views/UnifiedInbox.vue frontend/src/styles/page-system.css frontend/tests/page-templates.test.mjs frontend/tests/product-ui-redesign.test.mjs
git commit -m "🎨 重构聚合收件箱流体工作台"
```

---

### Task 6: Redesign Account, Sync, and User Management Consoles

**Files:**
- Modify: `frontend/src/views/AccountList.vue`
- Modify: `frontend/src/views/HistorySync.vue`
- Modify: `frontend/src/views/UserManagement.vue`
- Modify: `frontend/src/styles/page-system.css`
- Modify: `frontend/tests/page-templates.test.mjs`
- Modify: `frontend/tests/product-ui-redesign.test.mjs`

**Interfaces:**
- Consumes: fluid management template and shared UI primitives.
- Produces: `.account-card-grid`, `.sync-summary-grid`, `.sync-card-grid`, and `.user-list` page structures.
- Preserves: account creation/edit/delete/reconnect, sync pause/resume/retry/reset/refresh, and administrator user actions.

- [ ] **Step 1: Add failing console contracts**

```js
test('management consoles use fluid responsive product layouts', async () => {
  const contracts = {
    'AccountList.vue': ['account-card-grid', 'UiSegmentedControl', 'UiBadge'],
    'HistorySync.vue': ['sync-summary-grid', 'sync-card-grid', 'UiBadge'],
    'UserManagement.vue': ['user-list', 'UiField', 'UiBadge'],
  };

  for (const [file, required] of Object.entries(contracts)) {
    const source = await read(`src/views/${file}`);
    assert.match(source, /<PageFrame[^>]*width="fluid"/);
    for (const value of required) assert.match(source, new RegExp(value));
  }
});
```

- [ ] **Step 2: Run and confirm RED**

```bash
cd frontend
npm test -- --test-name-pattern="management consoles use fluid"
```

- [ ] **Step 3: Migrate `AccountList.vue`**

- Keep the provider dialogs and account business handlers unchanged.
- Use a compact `PageToolbar` with search, grouping mode, connection filter, total count, and add action.
- Replace the single full-width account stack with `.account-card-grid` using `repeat(auto-fill, minmax(340px, 1fr))`.
- Make each account `UiCard` show provider icon, email, label, connection badge, last sync summary, and an aligned action row.
- Keep destructive actions in danger styling and preserve all confirmation flows.

- [ ] **Step 4: Migrate `HistorySync.vue`**

- Add `.sync-summary-grid` for total accounts, active tasks, failed tasks, and cached-summary progress.
- Render jobs in `.sync-card-grid` using `minmax(360px, 1fr)`.
- Use shared badges for status and shared progress styling.
- Preserve all task status wording and the distinction between summary progress, active phase, failure time, and completion time.

- [ ] **Step 5: Migrate `UserManagement.vue`**

- Use `UiField` in the filter toolbar and create/edit forms.
- Use `UiBadge` for role and account status.
- Keep desktop rows dense and aligned; at small widths, turn each row into a stacked card without hiding username, nickname, role, status, or actions.
- Preserve administrator-only guards and all API payloads.

- [ ] **Step 6: Remove superseded page-local primitives**

Delete only local CSS that redefines shared buttons, fields, badges, cards, page gutters, and generic empty/loading states. Keep provider-specific dialog layout, sync progress grid internals, and user row data layout where they remain page-specific.

- [ ] **Step 7: Verify the three consoles**

```bash
cd frontend
npm test -- --test-name-pattern="management pages|management toolbar|management consoles|history sync|administrator user management"
npm run build
```

- [ ] **Step 8: Commit console redesign**

```bash
git add frontend/src/views/AccountList.vue frontend/src/views/HistorySync.vue frontend/src/views/UserManagement.vue frontend/src/styles/page-system.css frontend/tests/page-templates.test.mjs frontend/tests/product-ui-redesign.test.mjs
git commit -m "🎨 重构账号同步与用户管理控制台"
```

---

### Task 7: Rebuild Contacts as a Complete Split Workspace

**Files:**
- Modify: `frontend/src/views/ContactList.vue`
- Modify: `frontend/src/styles/layout-system.css`
- Modify: `frontend/src/styles/page-system.css`
- Modify: `frontend/tests/page-templates.test.mjs`
- Modify: `frontend/tests/product-ui-redesign.test.mjs`

**Interfaces:**
- Consumes: fluid split template and shared fields/buttons/cards/states.
- Produces: `.contact-workspace`, `.contact-list-pane`, and `.contact-detail-pane` with exactly two vertical scroll owners.
- Preserves: contact search, add, edit, delete, selection, autocomplete data, and user isolation behavior.

- [ ] **Step 1: Add failing split-workspace tests**

```js
test('contacts render one bounded split workspace with two scroll owners', async () => {
  const source = await read('src/views/ContactList.vue');
  const layout = await read('src/styles/layout-system.css');

  assert.match(source, /<PageFrame[^>]*template="split"[^>]*width="fluid"/);
  assert.match(source, /class="contact-workspace split-grid"/);
  assert.match(source, /class="contact-list-pane ui-scroll-region ui-scroll-region--y"/);
  assert.match(source, /class="contact-detail-pane ui-scroll-region ui-scroll-region--y"/);
  assert.match(layout, /\.page-frame--split \.page-frame__body\s*\{[^}]*border:\s*1px solid var\(--ui-border\)/s);
});
```

- [ ] **Step 2: Run and confirm RED**

```bash
cd frontend
npm test -- --test-name-pattern="contacts render one bounded split"
```

- [ ] **Step 3: Rebuild the contact workspace structure**

- Keep one outer split panel supplied by `PageFrame`.
- Left pane: compact toolbar, `UiField` search, `UiButton` add action, contact list, and compact shared states.
- Right pane: selected contact detail, editable form, or a shared empty state.
- Make the list pane 320px by default, clamp it between 280px and 380px, and let the detail pane fill the remainder.

- [ ] **Step 4: Standardize rows and detail cards**

Use `.ui-list-row` for contact rows, shared avatar geometry, `.ui-detail-grid` for fields, and shared buttons for edit/delete/save/cancel. Preserve keyboard and click selection behavior.

- [ ] **Step 5: Preserve mobile behavior**

Below 760px, show one pane at a time using the existing selected-contact state. Keep the mobile back action visible and ensure the page has no horizontal overflow.

- [ ] **Step 6: Verify contacts**

```bash
cd frontend
npm test -- --test-name-pattern="contacts use|contacts render|top-level data pages"
npm run build
```

- [ ] **Step 7: Commit contact redesign**

```bash
git add frontend/src/views/ContactList.vue frontend/src/styles/layout-system.css frontend/src/styles/page-system.css frontend/tests/page-templates.test.mjs frontend/tests/product-ui-redesign.test.mjs
git commit -m "🎨 重构联系人分栏工作台"
```

---

### Task 8: Refine the Core Mail Workspace Without Changing Mail Behavior

**Files:**
- Modify: `frontend/src/views/MailList.vue`
- Modify: `frontend/src/styles/page-system.css`
- Modify: `frontend/tests/ui-layout.test.mjs`
- Modify: `frontend/tests/profile-and-image-viewer.test.mjs`
- Modify: `frontend/tests/product-ui-redesign.test.mjs`

**Interfaces:**
- Consumes: fluid workspace, shared controls, shared rows, shared badges, shared states, existing `ImageViewer`.
- Preserves: account/folder selection, pagination, search, filters, refresh, read/star/delete actions, message detail, attachment download, inline images, PDF export, reply, and forward.

- [ ] **Step 1: Add mail-workspace structure tests**

```js
test('mail management uses the fluid workspace and shared toolbar primitives', async () => {
  const source = await read('src/views/MailList.vue');

  assert.match(source, /<PageFrame[^>]*template="workspace"[^>]*width="fluid"/);
  assert.match(source, /class="mail-shell workspace-grid"/);
  assert.match(source, /UiIconButton/);
  assert.match(source, /UiBadge/);
  assert.match(source, /UiEmptyState/);
});
```

Keep the existing assertions that inline assets are not attachment rows and that the image viewer opens from message body images.

- [ ] **Step 2: Run and confirm RED**

```bash
cd frontend
npm test -- --test-name-pattern="mail management uses the fluid|mail detail hides inline"
```

- [ ] **Step 3: Migrate the top toolbar and message list**

- Replace visually duplicated toolbar buttons with `UiButton` or `UiIconButton` while retaining click handlers, loading state, titles, and accessible names.
- Use shared segmented/filter styling for all/unread/read.
- Use `UiBadge` for counts and attachment indicators.
- Keep list-row grid density, but unify unread, selected, hover, keyboard focus, sender, subject, preview, and time hierarchy.

- [ ] **Step 4: Refine message detail**

- Keep message rendering and sanitization unchanged.
- Use shared section surfaces for header, recipients, body, attachments, and actions.
- Preserve inline image click delegation and image viewer image ordering.
- Keep ordinary attachments separate from inline images.

- [ ] **Step 5: Refine desktop and mobile geometry**

- Desktop wide view must use the available workspace width.
- Intermediate widths may collapse the detail pane or narrow the folder pane according to current interaction state.
- Mobile continues using the drawer for account/folder navigation and one main mail view at a time.
- Do not introduce a permanent empty preview pane when no message is selected.

- [ ] **Step 6: Remove only generic local visual rules**

Preserve mail-specific grid, editor body, attachment layout, and message content rules. Remove local definitions for generic button, field, badge, card, empty state, loading state, page gutter, and floating surface appearance.

- [ ] **Step 7: Verify all mail regressions**

```bash
cd frontend
npm test -- --test-name-pattern="mail view|mail management|mobile mail|mail detail|image viewer|manual refresh"
npm run build
```

- [ ] **Step 8: Commit mail workspace redesign**

```bash
git add frontend/src/views/MailList.vue frontend/src/styles/page-system.css frontend/tests/ui-layout.test.mjs frontend/tests/profile-and-image-viewer.test.mjs frontend/tests/product-ui-redesign.test.mjs
git commit -m "🎨 完善邮件管理工作台视觉层级"
```

---

### Task 9: Unify Compose and Backup Workspaces

**Files:**
- Modify: `frontend/src/views/ComposeEmail.vue`
- Modify: `frontend/src/views/Backup.vue`
- Modify: `frontend/src/components/TiptapEditor.vue`
- Modify: `frontend/src/components/NasPathPicker.vue`
- Modify: `frontend/src/styles/page-system.css`
- Modify: `frontend/tests/product-ui-redesign.test.mjs`

**Interfaces:**
- Consumes: fluid workspace, `UiField`, shared buttons/cards/states.
- Preserves: recipient autocomplete, reply/forward data, attachment upload, drag/drop, scheduling, signatures, sending, backup mailbox selection, backup list/detail, and NAS path behavior.

- [ ] **Step 1: Add compose and backup contracts**

```js
test('compose and backup use shared fluid workspace surfaces', async () => {
  const compose = await read('src/views/ComposeEmail.vue');
  const backup = await read('src/views/Backup.vue');

  assert.match(compose, /<PageFrame[^>]*width="fluid"/);
  assert.match(compose, /<UiField/);
  assert.match(compose, /<UiButton/);
  assert.match(backup, /<PageFrame[^>]*width="fluid"/);
  assert.match(backup, /<UiEmptyState/);
  assert.match(backup, /<UiLoadingState/);
});
```

- [ ] **Step 2: Run and confirm RED**

```bash
cd frontend
npm test -- --test-name-pattern="compose and backup use shared"
```

- [ ] **Step 3: Migrate compose fields and actions**

Use `UiField` for sender, recipients, subject, schedule time, and signature-related controls. Use `UiButton`/`UiIconButton` for send, schedule, attachment, discard, recipient expansion, and editor actions. Keep all existing handlers and validation messages.

- [ ] **Step 4: Align editor and attachment surfaces**

Update `TiptapEditor.vue` and compose-specific styles so the editor toolbar, popovers, document canvas, attachment chips, drag overlay, and schedule panel share tokens and focus behavior. Preserve editor extensions, HTML output, tables, links, images, and empty paragraph handling.

- [ ] **Step 5: Migrate backup workspace**

Use shared toolbar, list rows, badges, loading/empty states, and detail sections. Preserve backup execution, mailbox selection, local archive metadata, attachment download, and current list/detail navigation.

- [ ] **Step 6: Verify compose and backup**

```bash
cd frontend
npm test -- --test-name-pattern="editor popovers|attachment controls|compose and backup|top-level asynchronous"
npm run build
```

- [ ] **Step 7: Commit workspace migration**

```bash
git add frontend/src/views/ComposeEmail.vue frontend/src/views/Backup.vue frontend/src/components/TiptapEditor.vue frontend/src/components/NasPathPicker.vue frontend/src/styles/page-system.css frontend/tests/product-ui-redesign.test.mjs
git commit -m "🎨 统一写信与邮件备份工作区"
```

---

### Task 10: Rebuild Settings, Profile, Notifications, and About as Cohesive Documents

**Files:**
- Modify: `frontend/src/views/Settings.vue`
- Modify: `frontend/src/views/Profile.vue`
- Modify: `frontend/src/views/NotificationSettings.vue`
- Modify: `frontend/src/views/About.vue`
- Modify: `frontend/src/styles/page-system.css`
- Modify: `frontend/tests/page-templates.test.mjs`
- Modify: `frontend/tests/profile-and-image-viewer.test.mjs`
- Modify: `frontend/tests/product-ui-redesign.test.mjs`

**Interfaces:**
- Consumes: `document` template, `form` and `reading` widths, `UiCard`, `UiField`, `UiButton`, `UiBadge`, `UiAlert`, and shared section patterns.
- Preserves: theme selection, cleanup settings, attachment cache settings, Gmail proxy/OAuth settings, profile update/avatar upload, third-party notification settings/tests, and about/version content.

- [ ] **Step 1: Add document page contracts**

```js
test('document pages use explicit form and reading widths with shared sections', async () => {
  const formPages = ['Settings.vue', 'Profile.vue', 'NotificationSettings.vue'];
  for (const file of formPages) {
    const source = await read(`src/views/${file}`);
    assert.match(source, /<PageFrame[^>]*template="document"[^>]*width="form"/);
    assert.match(source, /<UiCard/);
    assert.match(source, /<UiField/);
  }

  const about = await read('src/views/About.vue');
  assert.match(about, /<PageFrame[^>]*template="document"[^>]*width="reading"/);
  assert.match(about, /<UiCard/);
});
```

- [ ] **Step 2: Run and confirm RED**

```bash
cd frontend
npm test -- --test-name-pattern="document pages use explicit"
```

- [ ] **Step 3: Migrate `Settings.vue`**

Organize settings into shared section cards in this order: appearance, attachment cache, upload cleanup, Gmail OAuth/proxy, Outlook OAuth, and other existing user settings. Use `UiField` and shared action rows. Preserve every API field, validation rule, test button, and explanatory text.

- [ ] **Step 4: Migrate `Profile.vue`**

Create one primary profile card with avatar, username, nickname, hint text, and save action. Preserve 256×256 WebP upload behavior, current preview, username validation, event emission, and error handling.

- [ ] **Step 5: Migrate `NotificationSettings.vue`**

Group channel enablement, credentials, proxy reuse, test action, and save action into clear shared sections. Keep secrets masked and preserve all existing payload fields.

- [ ] **Step 6: Migrate `About.vue`**

Use `width="reading"`, shared cards, and consistent typographic sections for version, features, open-source attribution, runtime notes, and links. Do not add network requests or new product claims.

- [ ] **Step 7: Verify document pages**

```bash
cd frontend
npm test -- --test-name-pattern="settings documents|document pages use explicit|administrator user management offers profile"
npm run build
```

- [ ] **Step 8: Commit document redesign**

```bash
git add frontend/src/views/Settings.vue frontend/src/views/Profile.vue frontend/src/views/NotificationSettings.vue frontend/src/views/About.vue frontend/src/styles/page-system.css frontend/tests/page-templates.test.mjs frontend/tests/profile-and-image-viewer.test.mjs frontend/tests/product-ui-redesign.test.mjs
git commit -m "🎨 重构设置资料通知与关于页面"
```

---

### Task 11: Remove Cross-Page CSS Conflicts and Complete the Responsive Audit

**Files:**
- Modify: `frontend/src/styles/macos.css`
- Modify: `frontend/src/styles/components.css`
- Modify: `frontend/src/styles/app-shell.css`
- Modify: `frontend/src/styles/layout-system.css`
- Modify: `frontend/src/styles/page-system.css`
- Modify: all migrated `frontend/src/views/*.vue` files only where conflicting scoped rules remain
- Modify: `frontend/tests/design-system.test.mjs`
- Modify: `frontend/tests/page-templates.test.mjs`
- Modify: `frontend/tests/ui-layout.test.mjs`
- Modify: `frontend/tests/product-ui-redesign.test.mjs`

**Interfaces:**
- Produces: one authoritative owner for page gutters, shared components, shell geometry, and cross-page patterns.
- Removes: migration-only aliases and duplicated generic page styles.

- [ ] **Step 1: Add source ownership tests**

Add these checks to `product-ui-redesign.test.mjs`:

```js
test('page scoped styles do not redefine shared product primitives', async () => {
  const pages = [
    'About.vue', 'AccountList.vue', 'Backup.vue', 'ComposeEmail.vue',
    'ContactList.vue', 'HistorySync.vue', 'LoginView.vue', 'MailList.vue',
    'NotificationSettings.vue', 'Profile.vue', 'Settings.vue',
    'UnifiedInbox.vue', 'UserManagement.vue',
  ];
  const forbidden = /\.(btn|card|empty-state|loading-state|page-frame|ui-input|ui-select|ui-badge)\s*\{/;

  for (const file of pages) {
    const source = await read(`src/views/${file}`);
    const styles = [...source.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/gi)]
      .map((match) => match[1])
      .join('\n');
    assert.doesNotMatch(styles, forbidden, file);
  }
});

test('legacy compatibility stylesheet does not own active page roots', async () => {
  const legacy = await read('src/styles/macos.css');
  for (const root of ['mail-view', 'compose-page', 'account-page', 'contact-page', 'settings-page']) {
    assert.doesNotMatch(legacy, new RegExp(`\\.${root}\\s*\\{`));
  }
});
```

- [ ] **Step 2: Run and confirm RED**

```bash
cd frontend
npm test -- --test-name-pattern="page scoped styles do not redefine|legacy compatibility stylesheet"
```

Expected: failures identify the remaining duplicated or legacy-owned selectors.

- [ ] **Step 3: Remove conflicts by ownership, not by visual guesswork**

For every failing selector:

1. Confirm the same behavior exists in the authoritative shared stylesheet.
2. Remove the page or legacy duplicate.
3. Re-run the targeted test immediately.
4. Do not remove page-specific grid, MIME body, provider, editor, or data-layout rules.

Remove `--page-padding`, `--page-content-max`, and other migration aliases once no source reference remains. Verify with:

```bash
rg -n --glob='*.vue' --glob='*.css' -- '--page-padding|--page-content-max' frontend/src
```

Expected: no active references.

- [ ] **Step 4: Audit responsive layout at exact viewports**

Use the running frontend in a real browser and record screenshots for:

- 1440×900 desktop expanded sidebar.
- 1440×900 desktop collapsed sidebar, default Logo.
- 1440×900 collapsed sidebar with pointer over brand slot, expand icon visible.
- 1920×1080 desktop.
- 2560×1440 ultra-wide desktop.
- 390×844 mobile.

Check every top-level view: unified, mail, contacts, history-sync, accounts, backup, profile, users, settings, notifications, about, compose, and login.

For each screenshot verify:

- No accidental right-side dead column on fluid pages.
- No inconsistent sidebar-to-content gutter.
- No horizontal overflow.
- Header actions wrap without overlap.
- Cards and rows use consistent radii and borders.
- Empty and loading states remain centered in their actual owner.
- Collapsed Logo and hover/focus expand icon use one slot.

- [ ] **Step 5: Audit themes and accessibility preferences**

Repeat representative unified inbox, mail, contacts, settings, login, and sidebar checks in light and dark themes. Toggle reduced motion, reduced transparency, and high contrast through browser emulation or operating-system settings. Verify focus order with keyboard only.

- [ ] **Step 6: Run the complete frontend suite**

```bash
cd frontend
npm test
npm run build
```

Expected: all tests pass; `vue-tsc` and Vite build succeed. The existing chunk-size warning may remain, but no new warning or error is accepted.

- [ ] **Step 7: Commit the final CSS cleanup**

```bash
git add frontend/src/styles frontend/src/views frontend/tests
git commit -m "🧹 清理整站样式冲突并完成响应式审查"
```

---

### Task 12: Document, Version, Build, Deploy, and Verify Release 0.0.23

**Files:**
- Modify: `README.md`
- Modify: `VERSION`
- Generated by version sync: `package.json`
- Generated by version sync: `frontend/package.json`
- Generated by version sync: `docker-compose.yml`
- Review: `.env.example`

**Interfaces:**
- Produces: release version `0.0.23`.
- Produces: local image `benxianyu/flymail:0.0.23`.
- Preserves: production container name `flymail` and `/Docker/flymail/data:/data`.

- [ ] **Step 1: Update user-facing documentation**

Update README’s UI capability section to document:

- Fluid workspaces for mail, contacts, unified inbox, account, sync, user, compose, and backup pages.
- Form and reading widths for settings, profile, notification, and about pages.
- Shared product components and consistent responsive behavior.
- ChatGPT-style collapsed brand slot that shows the Logo by default and the expand icon only on hover/focus.

Confirm no environment variables changed; leave `.env.example` unchanged.

- [ ] **Step 2: Synchronize version 0.0.23**

Set `VERSION` to:

```text
0.0.23
```

Then run:

```bash
npm run sync-version
cat VERSION
node -e "console.log(require('./package.json').version)"
node -e "console.log(require('./frontend/package.json').version)"
```

Expected: all three values are `0.0.23`, and README/docker-compose image tags are `benxianyu/flymail:0.0.23`.

- [ ] **Step 3: Run complete source verification**

```bash
cd backend
python -m unittest discover -s tests -v
cd ../frontend
npm test
npm run build
cd ..
bash -n scripts/docker-entrypoint.sh
docker compose --env-file .env.example config -q
git diff --check
git status --short
```

Expected: 146 or more backend tests pass, all frontend tests pass, frontend build succeeds, shell and Compose checks succeed, and diff check reports no whitespace errors.

- [ ] **Step 4: Build the release image**

```bash
docker build -t benxianyu/flymail:0.0.23 .
docker image inspect benxianyu/flymail:0.0.23 --format '{{.Id}}'
```

Expected: build succeeds and returns a concrete image ID.

- [ ] **Step 5: Validate an isolated temporary container**

Use a temporary container name and temporary host data directory, never `/Docker/flymail/data`. Use a database password containing a quote, backslash, `@`, `:`, `/`, and `%`.

Verify:

1. Container reaches `healthy`.
2. `/api/health` returns version `0.0.23`.
3. MySQL reports version 8.0 and data directory `/data/mysql/`.
4. `/data/flymail` and required subdirectories exist.
5. A temporary database row can be written and read.
6. The row remains after container restart.
7. Logs do not contain the database password or unredacted database URL.
8. Image metadata contains no administrator password, database password, or session secret.
9. SIGTERM stops the application and MySQL safely.
10. Temporary container and temporary data are removed after verification.

- [ ] **Step 6: Replace the production container safely**

Before replacement, record current image, health, restart policy, mount, and user/account/message counts. Recreate `flymail` with the existing environment and exact mount:

```text
/Docker/flymail/data:/data
```

Verify health, version, MySQL version/data directory, current counts, and restart persistence. Do not delete, move, or reinitialize `/Docker/flymail/data`.

- [ ] **Step 7: Inspect final diff and staged content**

```bash
git status --short
git diff --check
git diff
git add README.md VERSION package.json frontend/package.json docker-compose.yml frontend/src frontend/tests
git diff --staged --check
git diff --staged
```

Confirm staged files contain only this redesign and release changes. Scan for credentials before committing.

- [ ] **Step 8: Commit and push**

```bash
git commit -m "🎨 完成 FlyMail 整站产品级界面重设计"
git push origin main
```

If port 22 is unavailable, retry with:

```bash
GIT_SSH_COMMAND='ssh -p 443 -o HostName=ssh.github.com' git push origin main
```

Do not force-push. Do not upload Docker Hub unless the user explicitly requests it.

- [ ] **Step 9: Final delivery report**

Report the visible result, changed file responsibilities, backend test count, frontend test/build result, Shell and Compose checks, image ID, temporary-container health, MySQL version/data directory, `/data` restart persistence, README/environment documentation conclusion, commit title/SHA/branch/push result, Docker Hub status, real-browser or real-mail risks, and:

```text
最终部署版本：0.0.23
镜像：benxianyu/flymail:0.0.23
容器：flymail
运行状态：运行正常
健康检查：已通过
```
