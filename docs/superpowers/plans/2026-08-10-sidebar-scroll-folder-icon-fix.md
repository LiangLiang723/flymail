# Sidebar Scroll and Folder Icon Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the FlyMail sidebar so custom folders retain the standard folder outline with an embedded first character, account and folder lists own separate vertical scrolling, and the sidebar never exposes horizontal scrolling.

**Architecture:** Keep the existing `AppSidebar.vue` information architecture. The outer mail-navigation container becomes a non-scrolling flex allocator; the account list scrolls only when more than five accounts exist, while a dedicated folder-list wrapper owns the remaining vertical scroll. Custom folder rendering reuses the existing `AppIcon` folder outline and overlays a tiny initial inside the icon rather than replacing the icon family.

**Tech Stack:** Vue 3, TypeScript, CSS, Node test runner, Vite, Docker.

## Global Constraints

- Work only in `/home/chatgpt/flymail` on `main`.
- Do not change backend APIs, database schema, authentication, or mailbox synchronization behavior.
- Do not delete, migrate, or write test data into `/Docker/flymail/data`.
- Do not upload Docker Hub.
- Preserve desktop 248px / 72px sidebar geometry and the 960px mobile drawer breakpoint.
- The sidebar must not expose horizontal scrolling in expanded, collapsed, mobile, or 200% zoom states.

---

### Task 1: Lock the corrected sidebar contracts

**Files:**
- Modify: `frontend/tests/mail-sidebar-refactor.test.mjs`
- Test: `frontend/tests/mail-sidebar-refactor.test.mjs`

**Interfaces:**
- Consumes: existing AppSidebar static layout contract.
- Produces: executable contracts for independent vertical scroll owners and standard folder-outline initials.

- [x] **Step 1: Add failing scroll-owner assertions**

```js
assert.match(css, /\.sidebar-scroll\s*\{[^}]*overflow:\s*hidden;/s);
assert.match(css, /\.sidebar-account-scroll\.has-scroll\s*\{[^}]*max-height:\s*240px;[^}]*overflow-x:\s*hidden;[^}]*overflow-y:\s*auto;/s);
assert.match(css, /\.sidebar-folder-scroll\s*\{[^}]*overflow-x:\s*hidden;[^}]*overflow-y:\s*auto;/s);
```

- [x] **Step 2: Add failing folder-icon assertions**

```js
assert.match(sidebar, /class="sidebar-folder-glyph"[\s\S]*<AppIcon name="folder" :size="18" \/>[\s\S]*class="sidebar-folder-initial"/s);
assert.doesNotMatch(sidebar, /folder-letter/);
```

- [x] **Step 3: Verify RED**

Run: `cd frontend && node --test tests/mail-sidebar-refactor.test.mjs`
Expected before implementation: failures for the outer scroll owner, custom folder icon, and scrollbar styling.

### Task 2: Implement the minimal sidebar fix

**Files:**
- Modify: `frontend/src/components/app/AppSidebar.vue`
- Modify: `frontend/src/styles/app-shell.css`
- Test: `frontend/tests/mail-sidebar-refactor.test.mjs`

**Interfaces:**
- Consumes: `mailStore.accounts`, `mailStore.folders`, `folderIconName()`.
- Produces: `.sidebar-account-scroll`, `.sidebar-folder-scroll`, `.sidebar-folder-glyph`, and `.sidebar-folder-initial`.

- [x] **Step 1: Keep the folder outline for custom folders**

```vue
<span v-if="folderIconName(folder.name) === 'folder'" class="sidebar-folder-glyph" aria-hidden="true">
  <AppIcon name="folder" :size="18" />
  <span class="sidebar-folder-initial">{{ folderLetter(folder.name) }}</span>
</span>
```

- [x] **Step 2: Make the outer navigation non-scrolling and split the two vertical scroll owners**

```css
.sidebar-scroll { overflow: hidden; }
.sidebar-account-scroll.has-scroll { max-height: 240px; overflow-x: hidden; overflow-y: auto; }
.sidebar-folder-scroll { flex: 1; min-height: 0; overflow-x: hidden; overflow-y: auto; }
```

- [x] **Step 3: Use a low-profile scrollbar and hide it in the 72px collapsed rail**

```css
.sidebar-account-scroll.has-scroll,
.sidebar-folder-scroll {
  scrollbar-width: thin;
}

.app-shell.sidebar-collapsed .sidebar-account-scroll.has-scroll,
.app-shell.sidebar-collapsed .sidebar-folder-scroll {
  scrollbar-width: none;
}
```

- [x] **Step 4: Verify GREEN**

Run: `cd frontend && node --test tests/mail-sidebar-refactor.test.mjs`
Expected: all focused tests pass.

### Task 3: Regression, visual, and release verification

**Files:**
- Modify: `DESIGN.md`
- Modify: `VERSION`
- Modify: `package.json`
- Modify: `frontend/package.json`
- Modify: `docker-compose.yml`
- Modify: `README.md`

**Interfaces:**
- Consumes: current version `0.0.47`.
- Produces: release `0.0.48` documenting the corrected sidebar behavior.

- [x] **Step 1: Document the stable sidebar rule**

Add a concise DESIGN contract: the outer mail navigation never scrolls, accounts scroll only after five entries, folders own remaining vertical scroll, and custom folders preserve the folder outline with an embedded initial.

- [x] **Step 2: Bump VERSION to `0.0.48` and synchronize package/Compose/README version surfaces**

Expected versions: `0.0.48` in `VERSION`, root `package.json`, `frontend/package.json`, `docker-compose.yml`, and README image references.

- [x] **Step 3: Run source verification**

Run:

```bash
cd frontend && npm test
cd frontend && npm run build
cd backend && python -m unittest discover -s tests -v
bash -n scripts/docker-entrypoint.sh
docker compose config
git diff --check
```

Expected: frontend 169+ tests pass, backend 238 tests pass, build and configuration checks succeed.

- [x] **Step 4: Verify the actual UI**

Check 1440×900, 1920×1080, 390×844, collapsed 72px rail, and 200% zoom. Expected: no horizontal scrollbar, no outer navigation scrollbar, account list scrolls only above five accounts, folder list scrolls independently, and custom folder initial remains inside the standard folder outline.

### Task 4: Build, validate, deploy, and push 0.0.48

**Files:** None beyond release metadata already listed.

**Interfaces:**
- Consumes: verified source and local image `benxianyu/flymail:0.0.48`.
- Produces: running production container `flymail` on `0.0.48` using the existing persistent data bind mount.

- [x] **Step 1: Build the image**

Run: `docker build -t benxianyu/flymail:0.0.48 .`
Expected: successful image build.

- [x] **Step 2: Run isolated temporary-container validation**

Use a unique temporary host data directory and special-character MySQL password. Verify Docker health, `/api/health` `0.0.48`, MySQL 8.0 `/data/mysql/`, `/data/flymail`, DB read/write, restart persistence, log redaction, image metadata secrecy, and graceful shutdown.

- [x] **Step 3: Replace production safely**

Preserve the existing host port, environment, restart policy, network mode, and `/Docker/flymail/data:/data`. Keep the old container as rollback protection until the new container is healthy and persistence checks pass.

- [x] **Step 4: Commit and push**

Run pre-commit diff and staged-secret checks, commit only this task's files with a specific Chinese title, push `origin/main`, and confirm `main...origin/main` plus production health `0.0.48`.
