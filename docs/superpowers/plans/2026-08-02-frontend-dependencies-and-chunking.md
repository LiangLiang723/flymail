# Frontend Dependencies and Chunking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the current frontend npm security audit findings and remove the production build's oversized JavaScript chunk warning without changing FlyMail page behavior.

**Architecture:** Upgrade the existing Vite toolchain to the smallest security-patched Vite 6 line with the matching Vue plugin, commit the frontend lockfile, approve only reviewed install scripts, and make Docker consume the lockfile with `npm ci`. Keep the anonymous login view eager, but load authenticated top-level views with Vue async components so ComposeEmail and its Tiptap dependency graph are fetched only when needed; rely on Vite/Rollup's native dynamic-import splitting instead of manual vendor chunk rules.

**Tech Stack:** Vue 3, TypeScript, Vite 6.4.3, @vitejs/plugin-vue 5.2.4, Node test runner, npm audit.

## Global Constraints

- Keep `VERSION`, root `package.json`, `frontend/package.json`, `docker-compose.yml`, and README image version at `0.0.25`; this task does not change the application version.
- Do not use `npm audit fix --force`.
- Do not upgrade unrelated runtime dependencies.
- Do not suppress the chunk warning by raising `build.chunkSizeWarningLimit`.
- Do not add manual vendor chunk rules unless dynamic imports fail to keep every minified JavaScript chunk below 500 kB.
- Do not touch `/Docker/flymail/data`.

---

### Task 1: Upgrade the frontend build toolchain and resolve audit findings

**Files:**
- Modify: `.gitignore`
- Modify: `Dockerfile`
- Modify: `frontend/package.json`
- Create: `frontend/package-lock.json`
- Test: npm audit and frontend test/build commands

**Interfaces:**
- Consumes: existing Vite configuration in `frontend/vite.config.ts`
- Produces: Vite `^6.4.3`, `@vitejs/plugin-vue` `^5.2.4`, and a lockfile resolving `brace-expansion` to a non-vulnerable version

- [x] **Step 1: Record the failing security baseline**

Run:

```bash
cd frontend
npm audit --json
```

Expected: exit code 1 with four findings: Vite, esbuild, @vitejs/plugin-vue, and brace-expansion.

- [x] **Step 2: Upgrade only the affected direct build dependencies**

Run:

```bash
cd frontend
npm install --save-dev vite@^6.4.3 @vitejs/plugin-vue@^5.2.4
npm audit fix
```

Expected: `frontend/package.json` and `frontend/package-lock.json` change; unrelated runtime dependency ranges remain unchanged.

- [x] **Step 3: Verify dependency integrity and security**

Run:

```bash
cd frontend
npm audit
npm ls vite @vitejs/plugin-vue brace-expansion
npm test
```

Expected: audit reports zero vulnerabilities, dependency tree is valid, and all frontend tests pass.

---

### Task 2: Split authenticated page bundles with async components

**Files:**
- Create: `frontend/tests/build-splitting.test.mjs`
- Modify: `frontend/src/App.vue`
- Modify: `README.md`

**Interfaces:**
- Consumes: the existing `currentView` conditional rendering contract in `App.vue`
- Produces: eager `LoginView` plus async component definitions for all authenticated top-level views

- [x] **Step 1: Write the failing source contract**

Create a Node test that reads `src/App.vue` and asserts:

```js
assert.match(source, /import \{[^}]*defineAsyncComponent[^}]*\} from 'vue'/)
for (const view of authenticatedViews) {
  assert.match(source, new RegExp(`const ${view} = defineAsyncComponent\\(\\(\\) => import\\('\\./views/${view}\\.vue'\\)\\)`))
  assert.doesNotMatch(source, new RegExp(`import ${view} from '\\./views/${view}\\.vue'`))
}
assert.match(source, /import LoginView from '\.\/views\/LoginView\.vue'/)
```

- [x] **Step 2: Verify the contract fails before implementation**

Run:

```bash
cd frontend
node --test tests/build-splitting.test.mjs
```

Expected: FAIL because authenticated views are still synchronously imported.

- [x] **Step 3: Implement minimal async view loading**

In `frontend/src/App.vue`, import `defineAsyncComponent` from Vue, retain the eager `LoginView` import, remove eager imports for authenticated views, and define each removed view with:

```ts
const ComposeEmail = defineAsyncComponent(() => import('./views/ComposeEmail.vue'));
```

Apply the same pattern to About, AccountList, Backup, ContactList, HistorySync, MailList, NotificationSettings, Profile, Settings, UnifiedInbox, and UserManagement.

- [x] **Step 4: Verify contract, full frontend tests, audit, and build output**

Run:

```bash
cd frontend
npm test
npm audit
npm run build
```

Expected: all tests pass, audit reports zero vulnerabilities, the build succeeds, no oversized chunk warning appears, and every emitted minified JavaScript chunk is below 500 kB.

- [x] **Step 5: Document the build-tool and loading behavior change**

Add a short README development note stating that the frontend uses the security-patched Vite 6 toolchain and lazily loads authenticated top-level pages to keep the initial bundle bounded; do not change deployment or environment-variable documentation.

- [x] **Step 6: Run repository and Docker verification**

Run:

```bash
bash -n scripts/docker-entrypoint.sh
docker compose config
git diff --check
docker build -t benxianyu/flymail:0.0.25 .
```

Start an isolated temporary container with a `/tmp` data directory, verify `/api/health`, MySQL 8.0, persistence across restart, secret-safe logs and metadata, then clean it up. The production container and `/Docker/flymail/data` remain untouched unless final deployment is explicitly required.
