# UI Compact Content Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep recipient fields and other compact user-content controls visually stable when long or dynamic text is inserted, without changing the broader FlyMail layout system.

**Architecture:** Fix the root cause at the compact-control boundary instead of changing compose row heights globally. Recipient chips get their own compact remove-button size and an independently truncatable label; other audited user-generated compact surfaces get bounded text behavior. Existing page templates, desktop/mobile grids, mail reading layouts, and standard icon-button sizing remain unchanged.

**Tech Stack:** Vue 3, scoped Vue CSS, shared CSS design tokens, Node test runner, Playwright, Docker.

## Global Constraints

- Work only in `/home/chatgpt/flymail` on `main`.
- Preserve `/Docker/flymail/data`; no database migration or data deletion.
- Do not add or upgrade production dependencies.
- Keep standard icon buttons at the existing shared size; only compact contexts get smaller explicit sizing.
- Preserve natural wrapping when there are genuinely many recipients; prevent one ordinary recipient chip from increasing field height.
- Support desktop and mobile widths without horizontal page overflow.
- Bump the patch release to `0.0.40` after frontend behavior changes.
- Build only the local `benxianyu/flymail:0.0.40` image; do not upload Docker Hub.

---

### Task 1: Stabilize compose recipient chips

**Files:**
- Modify: `frontend/src/views/ComposeEmail.vue`
- Modify: `frontend/src/styles/page-system.css`
- Test: `frontend/tests/compose-compact-content.test.mjs`

**Interfaces:**
- Consumes the existing `.tag-input`, `.tag`, `.tag-remove`, and recipient list markup.
- Produces `.tag-label` as the bounded text child; recipient chip remove buttons use an explicit compact size while all global standard icon buttons stay unchanged.

- [x] **Step 1: Write a failing recipient-chip contract test**

Create `frontend/tests/compose-compact-content.test.mjs` that asserts all three recipient loops wrap `addr` in a `tag-label` with a title, and that the compose CSS defines a compact `.tag-remove` size plus bounded single-line `.tag-label` text.

```js
assert.ok((compose.match(/class="tag-label" :title="addr"/g) || []).length >= 3);
assert.match(compose, /\.tag-label\s*\{[^}]*min-width:\s*0;[^}]*overflow:\s*hidden;[^}]*text-overflow:\s*ellipsis;[^}]*white-space:\s*nowrap;/s);
assert.match(pageSystem, /\.compose-page \.tag-remove\s*\{[^}]*width:\s*20px;[^}]*height:\s*20px;/s);
assert.match(pageSystem, /\.compose-page \.tag\s*\{[^}]*max-width:/s);
```

- [x] **Step 2: Run the focused test and confirm RED**

Run: `cd frontend && node --test tests/compose-compact-content.test.mjs`

Expected: FAIL because raw recipient text has no `.tag-label` and `.tag-remove` still inherits the global medium icon control size.

- [x] **Step 3: Implement the smallest recipient fix**

Change each recipient chip to:

```vue
<span class="tag">
  <span class="tag-label" :title="addr">{{ addr }}</span>
  <button class="tag-remove" ...>&times;</button>
</span>
```

Add compose-context rules that keep chips at one compact line, preserve the remove button, and reserve enough width for the input:

```css
.compose-page .tag {
  min-width: 0;
  max-width: min(420px, calc(100% - 132px));
}

.compose-page .tag-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.compose-page .tag-remove {
  width: 20px;
  height: 20px;
  border-radius: var(--ui-radius-sm);
}
```

Keep `.tag-input { flex-wrap: wrap; }` so multiple recipients can wrap only when space is actually exhausted.

- [x] **Step 4: Verify focused tests and production build**

Run:

```bash
cd frontend
node --test tests/compose-compact-content.test.mjs tests/compose-signature-contract.test.mjs tests/control-primitives.test.mjs
npm run build
```

Expected: all pass.

---

### Task 2: Bound other audited compact user-content surfaces

**Files:**
- Modify: `frontend/src/views/ComposeEmail.vue`
- Modify: `frontend/src/views/AccountList.vue`
- Modify: `frontend/src/styles/app-shell.css`
- Test: `frontend/tests/compose-compact-content.test.mjs`
- Test: `frontend/tests/ui-layout.test.mjs`

**Interfaces:**
- Contact suggestion rows keep name and address within the existing suggestion width.
- Account group tags remain one-line chips and truncate pathological group names instead of widening dialogs.
- Global toast/confirm text may wrap long unbroken error/path strings inside its existing bounded container.

- [x] **Step 1: Extend tests and confirm RED**

Require:

```js
assert.match(compose, /\.contact-suggestions strong[\s\S]*min-width:\s*0[\s\S]*text-overflow:\s*ellipsis/);
assert.match(accounts, /\.group-tag\s*\{[^}]*max-width:[^;}]+;[^}]*overflow:\s*hidden;[^}]*text-overflow:\s*ellipsis;[^}]*white-space:\s*nowrap;/s);
assert.match(shell, /\.toast-container \.toast-item[\s\S]*overflow-wrap:\s*anywhere/);
assert.match(shell, /\.confirm-message[\s\S]*overflow-wrap:\s*anywhere/);
```

Run: `cd frontend && node --test tests/compose-compact-content.test.mjs tests/ui-layout.test.mjs`

Expected: FAIL on the newly required bounded-content rules.

- [x] **Step 2: Implement only the audited bounds**

For compose suggestions, give both `strong` and `small` `min-width: 0`, overflow hidden, ellipsis, and nowrap; keep the email secondary and do not change suggestion row height. For `.group-tag`, add a container-relative maximum width and ellipsis without changing padding or colors. For app toasts and confirm messages, add `overflow-wrap: anywhere` so server error strings or filesystem-like tokens cannot escape the bounded surface.

- [x] **Step 3: Run focused tests and full frontend regression**

Run:

```bash
cd frontend
node --test tests/compose-compact-content.test.mjs tests/ui-layout.test.mjs
npm test
npm run build
```

Expected: all frontend tests and production build pass.

---

### Task 3: Browser layout regression at desktop and mobile widths

**Files:**
- No production file unless browser evidence reveals a missed root-cause edge case.

**Interfaces:**
- Browser evidence must show stable recipient height for one long address and no horizontal viewport overflow on compact user-content surfaces.

- [x] **Step 1: Re-run the original reproduction on the production build**

Use the local `webapp-testing` Playwright helper with mocked authenticated APIs. At `1440x1000`, record `.tag-input` height before and after adding a long address. Require the height to remain at the compact baseline (allow at most 2px rendering variance) and the chip label to truncate while the remove button remains visible.

- [x] **Step 2: Exercise multiple recipients and mobile width**

At `390x844`, add one very long recipient and then several normal recipients. Require `document.documentElement.scrollWidth <= document.documentElement.clientWidth`; one long chip stays bounded, and multiple recipients may wrap naturally without an oversized remove button.

- [x] **Step 3: Inspect the audited dynamic surfaces**

Use mocked long contact suggestions, long account group labels where practical, and a long toast/confirm string. Require their rendered boxes to remain within their parent or viewport. If a new real failure is found, first add a failing contract/browser regression assertion, then make the smallest source fix and rerun Tasks 1–3.

---

### Task 4: Release, Docker validation, production replacement, and Git

**Files:**
- Modify: `VERSION`
- Modify via sync: `package.json`
- Modify via sync: `frontend/package.json`
- Modify via sync: `docker-compose.yml`
- Modify: `README.md`
- Update: `docs/superpowers/plans/2026-08-07-ui-compact-content-consistency.md`

**Interfaces:**
- Produces release `0.0.40` and local image `benxianyu/flymail:0.0.40`.
- Production remains container `flymail`, existing port/network/restart policy, and `/Docker/flymail/data:/data`.

- [x] **Step 1: Update README and version**

Document that recipient chips stay compact and long dynamic labels are bounded rather than stretching controls. Set `VERSION` to `0.0.40`, run `npm run sync-version`, and confirm VERSION/root/frontend/Compose/README all match.

- [x] **Step 2: Fresh complete verification**

Run:

```bash
cd backend && python -m unittest discover -s tests -v
cd ../frontend && npm install && npm test && npm run build
cd ..
bash -n scripts/docker-entrypoint.sh
docker compose config
git diff --check
git status --short
git diff
```

If the repository still intentionally lacks `.env`, validate Compose safely with `.env.example` without creating or exposing production secrets.

- [x] **Step 3: Build and validate isolated Docker container**

Build `docker build -t benxianyu/flymail:0.0.40 .`. Use a unique `/Docker/flymail/` temporary data directory and container name, with a test database password containing quote, backslash, `@`, `:`, `/`, or `%`. Verify healthy `/api/health`, MySQL 8.0 `/data/mysql/`, `/data/flymail`, DB read/write, restart persistence, log redaction, image metadata secret absence, and safe MySQL shutdown. Clean temporary container/data afterward.

- [x] **Step 4: Replace production with rollback protection**

Read the current `flymail` image, host port, mount, network, restart policy, health, MySQL version/datadir, and non-sensitive data fingerprint. Preserve the old container for rollback while starting `benxianyu/flymail:0.0.40` with identical runtime settings and `/Docker/flymail/data:/data`. Require healthy status, correct version, unchanged data fingerprint, and a successful second restart before removing rollback.

- [x] **Step 5: Final review, commit, and push**

Mark completed plan steps, run fresh verification, stage only this task's files, review staged diff and secret scan, commit with `🎨 修复动态内容撑高与溢出问题`, fetch origin, verify remote is not ahead, and push `origin main` without force. Verify local and remote SHA match and the workspace is clean.
