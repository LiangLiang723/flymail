# FlyMail Complete UI Control Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Audit and repair every FlyMail interactive control across light theme, dark theme, desktop and mobile so buttons, links, fields, disabled states, dialogs and attachment actions use deterministic semantic styling without browser-native artifacts or selector collisions.

**Architecture:** Keep the existing Vue 3 design system and page templates. Tighten the shared control contract in `components.css`, remove generic class selectors that leak across unrelated components, and give page-specific controls explicit semantic classes. Add static contract tests plus real Chromium theme/page/state audits; do not add production dependencies.

**Tech Stack:** Vue 3, TypeScript, CSS custom properties, Node test runner, Vite, Playwright as a temporary verification tool, FastAPI, Docker.

## Global Constraints

- Work only in `/home/chatgpt/flymail` on branch `main`.
- Preserve `/Docker/flymail/data`; no database migration or destructive mail operation.
- Do not change authentication, mail synchronization, SMTP/IMAP behavior or external notification behavior.
- Do not add or upgrade production dependencies.
- Use semantic tokens for both light and dark themes; no new fixed palette values in page/component style blocks.
- Every button must have explicit normal, hover, active, focus-visible and disabled behavior through a shared primitive or a complete local contract.
- Generic state names such as `.danger` must not globally impose component appearance.
- Icon-only buttons require an accessible label through visible text, `aria-label`, or `title`.
- Browser verification must cover all authenticated pages in light and dark themes at 1440×900 and mobile at 390×844.
- Final image must be built locally as `benxianyu/flymail:<VERSION>`; Docker Hub is not uploaded.

---

### Task 1: Lock the shared button selector contract

**Files:**
- Modify: `frontend/tests/design-system.test.mjs`
- Modify: `frontend/src/styles/components.css`

**Interfaces:**
- Consumes: existing `.btn`, `.ui-button`, `.btn-primary`, `.btn-secondary`, `.btn-danger`, `.btn-icon` primitives.
- Produces: selector contract where only explicit button variants control destructive backgrounds; generic `.danger` remains available only inside scoped component selectors.

- [ ] **Step 1: Add failing selector-collision tests**

Add assertions that `components.css` does not include a standalone `.danger` selector in the destructive button group and that destructive styles are limited to `.ui-button--danger`, `.btn-danger`, `.toolbar-btn.danger`, and `.btn-delete`.

```js
test('shared button variants never style generic danger state classes', async () => {
  const source = await readSource('src/styles/components.css');
  assert.doesNotMatch(source, /(?:^|,)\s*\.danger\s*(?:,|\{)/m);
  assert.match(source, /\.ui-button--danger,[\s\S]*\.btn-danger,[\s\S]*\.toolbar-btn\.danger,[\s\S]*\.btn-delete\s*\{/);
});
```

- [ ] **Step 2: Run the focused test and confirm RED**

Run:

```bash
cd frontend
node --test tests/design-system.test.mjs
```

Expected: FAIL because the shared destructive selector currently includes `.danger`.

- [ ] **Step 3: Remove generic destructive selector leakage**

In `components.css`, remove standalone `.danger` from the destructive background selector. Preserve the explicit button variants and their hover/disabled states.

- [ ] **Step 4: Run the focused test and confirm GREEN**

Run the same Node test and expect all design-system tests to pass.

- [ ] **Step 5: Commit the selector contract**

```bash
git add frontend/tests/design-system.test.mjs frontend/src/styles/components.css
git commit -m "🐛 修复危险状态选择器误伤按钮"
```

---

### Task 2: Repair notification actions and attachment buttons

**Files:**
- Modify: `frontend/tests/ui-layout.test.mjs`
- Modify: `frontend/src/views/NotificationSettings.vue`
- Modify: `frontend/src/views/MailList.vue`
- Modify: `frontend/src/styles/components.css`

**Interfaces:**
- Consumes: shared `.btn-secondary`, `.btn-danger-ghost`, `.ui-icon-button` contracts.
- Produces: `btn-danger-ghost` for non-primary destructive actions and `attachment-action` for icon/text attachment controls.

- [ ] **Step 1: Add failing markup and CSS contract tests**

Add assertions that:

```js
assert.match(notificationSource, /class="btn btn-danger-ghost"/);
assert.doesNotMatch(notificationSource, /class="btn btn-secondary danger"/);
assert.match(mailSource, /class="attachment-action"/);
assert.match(componentCss, /\.btn-danger-ghost\s*\{/);
assert.match(componentCss, /\.attachment-action\s*\{[^}]*border:\s*1px solid transparent;/s);
assert.match(componentCss, /\.attachment-action\s*\{[^}]*background:\s*transparent;/s);
```

- [ ] **Step 2: Run focused tests and confirm RED**

```bash
cd frontend
node --test tests/design-system.test.mjs tests/ui-layout.test.mjs
```

Expected: FAIL because the new variants and markup do not exist.

- [ ] **Step 3: Implement explicit action variants**

Add a shared `btn-danger-ghost` style using `--ui-danger`, `--ui-danger-soft`, and semantic hover/focus/disabled states. Replace the notification cleanup button class.

Add a shared `attachment-action` contract with fixed control height, explicit border/background reset, icon/text alignment, hover state, active state and disabled state. Replace both attachment buttons in `MailList.vue`; keep download and NAS behavior unchanged.

- [ ] **Step 4: Verify focused tests and frontend build**

```bash
cd frontend
npm test
npm run build
```

Expected: all tests pass and Vite build exits 0.

- [ ] **Step 5: Commit repaired controls**

```bash
git add frontend/tests/ui-layout.test.mjs frontend/src/views/NotificationSettings.vue frontend/src/views/MailList.vue frontend/src/styles/components.css
git commit -m "🐛 修复通知与附件操作按钮样式"
```

---

### Task 3: Audit every interactive control and normalize missing states

**Files:**
- Modify: `frontend/tests/design-system.test.mjs`
- Modify as evidence requires: `frontend/src/styles/components.css`
- Modify as evidence requires: affected files under `frontend/src/views/` and `frontend/src/components/`

**Interfaces:**
- Consumes: explicit shared control primitives from Tasks 1–2.
- Produces: static contract preventing unstyled native buttons, destructive selector collisions, unlabeled icon controls and missing disabled states.

- [ ] **Step 1: Inventory all controls**

Run:

```bash
rg -n '<button|<a |<input|<select|<textarea' frontend/src --glob '*.vue'
rg -n 'class="[^"]*(btn|button|action|link)[^"]*"' frontend/src --glob '*.vue'
rg -n '^\s*\.(danger|active|disabled|primary|secondary)\b' frontend/src --glob '*.{css,vue}'
```

Classify controls into shared buttons, icon buttons, segmented/filter controls, navigation rows, menu items, inline links, attachment actions and form fields.

- [ ] **Step 2: Add static regressions for discovered defects**

For each confirmed defect, add a narrowly scoped assertion. Required checks:

```js
assert.doesNotMatch(sharedCss, /(?:^|,)\s*\.(danger|active|disabled)\s*(?:,|\{)/m);
assert.doesNotMatch(mailSource, /class="att-download"/);
```

Scan visible icon-only buttons and ensure each has `aria-label`, `title`, or visible text.

- [ ] **Step 3: Verify tests fail for actual defects only**

Run `npm test`. A failing assertion must map to a control observed in source or the browser audit; remove speculative assertions.

- [ ] **Step 4: Apply minimal component-level fixes**

Use explicit semantic classes. Do not globally reset all `button` elements because navigation, editor controls and provider cards have established behavior. Preserve every click handler and disabled expression.

- [ ] **Step 5: Verify tests and build**

```bash
cd frontend
npm test
npm run build
```

- [ ] **Step 6: Commit the complete control-state audit**

```bash
git add frontend/src frontend/tests
git commit -m "🎨 统一全部交互控件状态规范"
```

---

### Task 4: Execute light/dark browser matrix and repair runtime-only defects

**Files:**
- Modify as evidence requires: affected frontend CSS/Vue files
- Modify: `frontend/tests/ui-layout.test.mjs` for durable runtime discoveries expressible statically

**Interfaces:**
- Consumes: production container and temporary Playwright Chromium runtime.
- Produces: evidence that all pages and common dialogs render without low-contrast controls, browser-native borders, clipping or horizontal overflow.

- [ ] **Step 1: Build a fresh temporary image and container**

Use an independent temporary data directory and container name. Do not mount `/Docker/flymail/data`.

- [ ] **Step 2: Audit all pages in both themes**

At 1440×900, inspect:

- 聚合收件箱
- 邮件管理 and a mail detail when available
- 写邮件
- 联系人 and add/edit dialog
- 同步管理
- 账号管理 and add/edit dialog
- 邮件备份 and detail when available
- 用户管理 and create dialog
- 第三方通知
- 设置 and About modal
- 关于
- notification drawer
- confirm dialog

For every visible button/link/field, collect computed foreground, effective background, border style, opacity, dimensions and disabled state. Fail the audit when:

- opaque foreground/background contrast is below 3:1 for controls or 4.5:1 for ordinary text;
- a button keeps browser-native `outset` border;
- enabled button text is visually indistinguishable from its background;
- control dimensions are zero or clipped;
- page has horizontal overflow.

- [ ] **Step 3: Audit mobile layout**

At 390×844 in both themes, inspect all primary pages, mobile sidebar, mail filters, compose toolbar, dialogs and notification drawer. Verify no horizontal overflow and minimum touch targets remain usable.

- [ ] **Step 4: Fix runtime-only defects with RED/GREEN coverage**

For each defect, add a static or component regression test first, reproduce RED, apply the smallest CSS/markup change, then rerun the focused browser state.

- [ ] **Step 5: Re-run the complete browser matrix**

Expected: zero low-contrast enabled controls, zero browser-native button borders, zero page-level horizontal overflow, and all disabled controls remain visibly distinct in both themes.

- [ ] **Step 6: Commit browser-discovered fixes**

```bash
git add frontend/src frontend/tests
git commit -m "✅ 补齐双主题全页面视觉回归"
```

---

### Task 5: Version, documentation, Docker verification and deployment

**Files:**
- Modify: `VERSION`
- Modify via `npm run sync-version`: `package.json`, `frontend/package.json`, `docker-compose.yml`, `README.md`
- Modify: `README.md` only if user-visible design-system behavior needs documentation

**Interfaces:**
- Consumes: verified UI changes.
- Produces: synchronized release, locally built image, healthy deployed `flymail` container, clean Git history on `origin/main`.

- [ ] **Step 1: Increment patch version**

Set `VERSION` to the next patch after `0.0.16`, then run:

```bash
npm run sync-version
```

Verify the root package, frontend package, Compose image and README all match.

- [ ] **Step 2: Run complete code gates**

```bash
cd backend
python -m unittest discover -s tests -v
cd ../frontend
npm test
npm run build
cd ..
bash -n scripts/docker-entrypoint.sh
docker compose config
git diff --check
git status --short
git diff
```

- [ ] **Step 3: Build the local image**

```bash
docker build -t benxianyu/flymail:$(cat VERSION) .
```

- [ ] **Step 4: Verify a temporary container**

Confirm healthy status, `/api/health` version, MySQL 8.0, `/data/mysql/`, `/data/flymail`, database read/write, restart persistence, redacted logs, clean image metadata and safe MySQL shutdown.

- [ ] **Step 5: Deploy with rollback protection**

Preserve the current container as a stopped rollback point until the replacement reaches `healthy`. Reuse exactly `/Docker/flymail/data:/data`; do not delete or migrate data.

- [ ] **Step 6: Verify production after restart**

Repeat the light/dark browser matrix against the production container, restart it, verify health and database counts, then remove temporary and rollback resources.

- [ ] **Step 7: Review and push**

```bash
git status --short
git diff --check
git diff
git add <only task files>
git diff --staged
git commit -m "🐛 修复双主题全部交互控件显示异常"
git push origin main
```

Do not run `docker login` or `docker push`.
