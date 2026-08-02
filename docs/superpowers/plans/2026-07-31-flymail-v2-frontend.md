# FlyMail V2 响应式前端 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 基于冻结的 V2 OpenAPI 和实时事件契约，重建桌面、平板和移动端同等完整的 Vue 3 邮件客户端，覆盖会话列表、详情、写信、搜索、本地操作、同步冲突、设置、管理员和备份恢复，并达到包体、可访问性和响应性能门槛。

**Architecture:** 新前端直接使用最终目录 `app`、`layouts`、`features`、`entities` 与 `shared`，开发阶段通过 `v2-main.ts` 和独立 Vite HTML 入口运行，现有 `main.ts` 保持生产入口。服务器状态由统一 Query Cache 管理，局部交互状态留在功能模块；实时事件只更新受影响投影，不复制整套服务器状态到多个 Store。

**Tech Stack:** Vue 3、TypeScript、Pinia、Vue Router、Axios、Tiptap、DOMPurify、Vite、vue-tsc、Node.js test runner；允许为 V2 测试增加经过兼容验证的 Vitest、Vue Test Utils 与 DOM 测试环境作为开发依赖，不新增运行时生产依赖。

## Global Constraints

- 必须先完成 API 计划 Gate 3，并使用冻结的 `backend/tests/v2/fixtures/openapi-v2.json`。
- 继承总路线图全部约束。
- 当前 `frontend/src/main.ts` 和当前生产 UI 在 Gate 4 前保持不变。
- 浏览器不持久化邮件正文、附件、凭证或离线操作。
- 桌面、平板和移动端必须拥有完整核心功能，不建立独立移动应用。
- 默认会话优先；跨账号会话保留来源身份和逐封状态。
- API 服务器状态只有一个权威 Query Cache，禁止同一列表同时复制到多个 Pinia Store。
- 打开邮件、已读、星标、移动或删除不能触发完整页面或整页列表重载。
- 邮件 HTML 必须经过 DOMPurify 严格配置并在隔离容器中展示；默认阻止远程图片。
- 动画可中断，支持 `prefers-reduced-motion`，列表批量更新不逐项长动画。
- 初始 JS gzip 目标不超过 `180 KB`；Tiptap、设置、管理员、备份和同步管理必须动态拆包。
- 不依赖 hover 才能完成任何功能。
- 所有错误状态必须有文本和可执行下一步，不能只靠颜色或无限加载动画。

## File Map

**Create:**

- `frontend/v2.html`
- `frontend/src/v2-main.ts`
- `frontend/src/app/AppV2.vue`
- `frontend/src/app/router.ts`
- `frontend/src/app/bootstrap.ts`
- `frontend/src/app/error-boundary.ts`
- `frontend/src/layouts/DesktopMailLayout.vue`
- `frontend/src/layouts/TabletMailLayout.vue`
- `frontend/src/layouts/MobileMailLayout.vue`
- `frontend/src/entities/account/types.ts`
- `frontend/src/entities/thread/types.ts`
- `frontend/src/entities/message/types.ts`
- `frontend/src/entities/operation/types.ts`
- `frontend/src/entities/job/types.ts`
- `frontend/src/shared/api/client.ts`
- `frontend/src/shared/api/query-cache.ts`
- `frontend/src/shared/api/generated.ts`
- `frontend/src/shared/api/errors.ts`
- `frontend/src/shared/realtime/client.ts`
- `frontend/src/shared/realtime/events.ts`
- `frontend/src/shared/ui/*`：V2 通用组件，只创建实际使用的组件。
- `frontend/src/shared/accessibility/focus.ts`
- `frontend/src/shared/formatting/*`
- `frontend/src/features/auth/*`
- `frontend/src/features/navigation/*`
- `frontend/src/features/threads/*`
- `frontend/src/features/message-viewer/*`
- `frontend/src/features/operations/*`
- `frontend/src/features/compose/*`
- `frontend/src/features/search/*`
- `frontend/src/features/profile/*`
- `frontend/src/features/accounts/*`
- `frontend/src/features/contacts/*`
- `frontend/src/features/notifications/*`
- `frontend/src/features/account-customization/*`
- `frontend/src/features/sync-center/*`
- `frontend/src/features/settings/*`
- `frontend/src/features/admin/*`
- `frontend/src/features/backup/*`
- `frontend/src/features/about/*`
- `frontend/src/features/pwa/*`
- `frontend/src/styles/v2-tokens.css`
- `frontend/src/styles/v2-base.css`
- `frontend/src/styles/v2-layout.css`
- `frontend/tests/v2/*.test.ts`

**Modify:**

- `frontend/package.json`：增加 V2 测试和构建脚本；仅在兼容探针通过后增加测试开发依赖。
- `frontend/package-lock.json`：锁定开发依赖。
- `frontend/vite.config.ts`：支持 V2 独立入口、拆包和包体报告，不改变当前生产入口输出。
- `README.md`：增加 V2 前端开发入口和测试说明，不切换现有部署。

---

### Task 1: 建立 V2 类型生成、API 客户端、Query Cache 和测试环境

**Files:**

- Create: `frontend/v2.html`
- Create: `frontend/src/v2-main.ts`
- Create: `frontend/src/shared/api/generated.ts`
- Create: `frontend/src/shared/api/client.ts`
- Create: `frontend/src/shared/api/query-cache.ts`
- Create: `frontend/src/shared/api/errors.ts`
- Create: `frontend/tests/v2/api-client.test.ts`
- Modify: `frontend/package.json`
- Modify: `frontend/vite.config.ts`

**Interfaces:**

- Produces: `apiClient.request<T>(request: ApiRequest<T>) -> Promise<T>`
- Produces: `QueryCache.get`, `fetch`, `invalidate`, `patch`, `cancel`, `clearUserData`.
- Produces: generated API response/request types from frozen OpenAPI.
- Produces V2 dev command: `npm run dev:v2` and test command: `npm run test:v2`.

- [x] **Step 1: Add a test-tool compatibility probe**

Run in `frontend` without modifying package files first:

```bash
npm view vitest peerDependencies --json
npm view @vue/test-utils peerDependencies --json
npm view happy-dom engines --json
npm view openapi-typescript engines --json
```

Confirm compatibility with Node 20, Vue 3.4, TypeScript 5.3 and Vite 5. Add only these development dependencies, lock resolved versions in `package-lock.json`, and record the resolved versions in the commit diff.

- [x] **Step 2: Write failing API and cache tests**

Tests cover:

- credentials and CSRF headers are applied centrally;
- 401 clears user-scoped cache and emits auth-expired event;
- same query key shares one in-flight Promise;
- cancellation aborts the HTTP request;
- stale cached data returns immediately while background refresh runs;
- patch updates only affected thread;
- logout clears all user server data;
- error envelope maps to typed `ApiError`.

- [x] **Step 3: Run tests and verify failure**

```bash
cd frontend
npm run test:v2 -- api-client
```

Expected: FAIL because V2 client and cache do not exist.

- [x] **Step 4: Generate API types**

Use `openapi-typescript` against `../backend/tests/v2/fixtures/openapi-v2.json` and write deterministic `src/shared/api/generated.ts`. Add a script that regenerates and a test that fails if regeneration changes committed output.

- [x] **Step 5: Implement API client**

Use one Axios instance with:

- `withCredentials: true`;
- CSRF token from in-memory Bootstrap state;
- `AbortSignal` support;
- request ID capture;
- no retry for unsafe commands;
- one controlled retry for idempotent GET after transient connection failure;
- response error normalization.

- [x] **Step 6: Implement Query Cache**

Cache entries contain data, status, updatedAt, staleAt, subscribers, inFlight Promise and AbortController. Key serialization must be stable and user-scoped. Do not persist cache to localStorage or IndexedDB.

- [x] **Step 7: Create V2 independent entry**

`v2.html` loads `src/v2-main.ts`. Current `index.html` and `src/main.ts` remain unchanged. Vite dev command serves V2 entry explicitly.

- [x] **Step 8: Run tests and commit**

```bash
npm run test:v2 -- api-client
npm run build
git add frontend/package.json frontend/package-lock.json frontend/vite.config.ts frontend/v2.html frontend/src/v2-main.ts frontend/src/shared/api frontend/tests/v2/api-client.test.ts
git commit -m "🧱 建立 V2 前端 API 类型与查询缓存"
```

**Measured verification:** API/client/cache tests `5/5` passed; frozen contract fingerprint check passed for OpenAPI `0.0.25` SHA `1552538e3c7cd1062d1ce51b9c6f99b8829fef0b6abc9c4a9add88c33971c6ac`; V2 independent production build passed at approximately `24.52 KB` initial JavaScript gzip including Vue core; legacy production build remained green. The frozen backend fixture is a reviewed contract fingerprint summary rather than a complete OpenAPI document, so the curated generated type surface carries and verifies the frozen version/SHA instead of silently fabricating schemas from incomplete input.

---

### Task 2: 实现认证门、Bootstrap 和响应式应用壳

**Files:**

- Create: `frontend/src/app/AppV2.vue`
- Create: `frontend/src/app/router.ts`
- Create: `frontend/src/app/bootstrap.ts`
- Create: `frontend/src/app/error-boundary.ts`
- Create: `frontend/src/layouts/DesktopMailLayout.vue`
- Create: `frontend/src/layouts/TabletMailLayout.vue`
- Create: `frontend/src/layouts/MobileMailLayout.vue`
- Create: `frontend/src/features/auth/LoginPage.vue`
- Create: `frontend/src/features/auth/auth-state.ts`
- Create: `frontend/src/styles/v2-tokens.css`
- Create: `frontend/src/styles/v2-base.css`
- Create: `frontend/src/styles/v2-layout.css`
- Create: `frontend/tests/v2/app-shell.test.ts`

**Interfaces:**

- Produces: `useBootstrap()` and `AuthState`.
- Layout breakpoints: mobile `< 768px`, tablet `768–1199px`, desktop `>= 1200px`.
- Produces routes for inbox shell, search, compose, settings, sync, admin and backup with dynamic imports.

- [x] **Step 1: Write shell tests**

Tests cover:

- unauthenticated app renders login and does not request thread list;
- authenticated startup calls Bootstrap exactly once;
- notification details and editor chunks are not loaded at startup;
- desktop renders three regions, tablet two, mobile page stack;
- browser back and mobile detail back produce same route result;
- app-wide error boundary preserves navigation and offers retry;
- 401 moves to login without a reload loop.

- [x] **Step 2: Verify failure**

Expected: FAIL.

- [x] **Step 3: Implement Bootstrap state**

Keep user, permissions, account navigation, preferences, CSRF token, realtime cursor and version in memory. Bootstrap failure states: unauthenticated, retryable network error, maintenance, incompatible version.

- [x] **Step 4: Implement responsive layout selection**

Use `matchMedia` listener and semantic regions. Do not render all three layouts hidden simultaneously. Preserve route and selected thread when breakpoint changes.

- [x] **Step 5: Implement CSS tokens and density**

Define light/dark color tokens, spacing, typography, focus rings, comfortable/compact density and motion durations. Reuse accessible primitives from current project only where contracts fit; do not import legacy global styles wholesale.

- [x] **Step 6: Add route-level dynamic imports**

Dynamically load compose/Tiptap, advanced search, settings, admin, backup and sync center. Core shell, navigation, thread list and basic detail remain initial.

- [x] **Step 7: Run tests and commit**

```bash
npm run test:v2 -- app-shell
npm run build
git add frontend/src/app frontend/src/layouts frontend/src/features/auth frontend/src/styles/v2-* frontend/tests/v2/app-shell.test.ts
git commit -m "🖥️ 建立 V2 认证启动与响应式应用壳"
```

**Measured verification:** V2 API and app-shell contracts `10/10` passed; V2 and legacy production builds passed. Bootstrap is single-flight, keeps identity/permissions/accounts/preferences/CSRF/realtime cursor/version only in memory and classifies unauthenticated, network, maintenance and incompatible states. Only one desktop/tablet/mobile layout is mounted for the active breakpoint, and compose/search/settings/sync/admin/backup routes remain dynamic. Current initial V2 JavaScript is approximately `59.61 KB` gzip across the entry, Axios and Vue core chunks.

---

### Task 3: 实现账号、统一文件夹、原生标签和移动导航

**Files:**

- Create: `frontend/src/entities/account/types.ts`
- Create: `frontend/src/features/navigation/NavigationPanel.vue`
- Create: `frontend/src/features/navigation/AccountSection.vue`
- Create: `frontend/src/features/navigation/MobileNavigationDrawer.vue`
- Create: `frontend/src/features/navigation/navigation-state.ts`
- Create: `frontend/tests/v2/navigation.test.ts`

**Interfaces:**

- Produces semantic routes for inbox, sent, drafts, trash, junk, archive, saved search, account mailbox and native label.
- Navigation selection serializes to route; local state only tracks expansion and drawer visibility.

- [x] **Step 1: Write navigation tests**

Tests cover:

- semantic folders appear once in unified section;
- Gmail native labels remain under account;
- disabled/auth-required accounts show actionable state;
- mobile drawer closes after selection and restores focus;
- account expansion preference persists as non-sensitive UI preference;
- navigation badges update from realtime event without full Bootstrap reload;
- no mailbox path is inserted as raw HTML.

- [x] **Step 2: Verify failure**

Expected: FAIL.

- [x] **Step 3: Implement route model**

Use typed location objects, not hand-built URL strings. Native mailbox keys are encoded as route params and decoded only by router schema.

- [x] **Step 4: Implement accessible tree/navigation**

Use proper buttons and links, visible focus, `aria-expanded`, and text labels. Nested labels are keyboard reachable; no hover-only reveal.

- [x] **Step 5: Run tests and commit**

```bash
npm run test:v2 -- navigation
git add frontend/src/entities/account frontend/src/features/navigation frontend/tests/v2/navigation.test.ts
git commit -m "🧭 实现 V2 统一文件夹与账号标签导航"
```

**Measured verification:** cumulative V2 contracts `15/15` passed and the V2 production build passed. Semantic folders are aggregated once, native labels remain account-scoped, disabled/auth-required/pending-verification accounts expose explicit actions, mobile selection closes the modal drawer and restores focus, and expansion state is serialized only as a non-sensitive server preference. Badge patches preserve unrelated account object identity. Current initial V2 JavaScript is approximately `62.20 KB` gzip.

---

### Task 4: 实现会话列表、稳定游标缓存和批量选择

**Files:**

- Create: `frontend/src/entities/thread/types.ts`
- Create: `frontend/src/features/threads/ThreadListPage.vue`
- Create: `frontend/src/features/threads/ThreadList.vue`
- Create: `frontend/src/features/threads/ThreadRow.vue`
- Create: `frontend/src/features/threads/thread-query.ts`
- Create: `frontend/src/features/threads/thread-selection.ts`
- Create: `frontend/tests/v2/thread-list.test.ts`

**Interfaces:**

- Produces query key from user, mailbox, filters and cursor.
- Produces `patchThreadProjection(threadId, patch)`.
- Produces list selection API for desktop keyboard and mobile selection mode.

- [x] **Step 1: Write list tests**

Tests cover:

- cached first page renders immediately and refreshes in background;
- same query deduplicates;
- switching mailbox aborts old request;
- stale old response cannot replace new mailbox data;
- next cursor appends without duplicates;
- read/star patch modifies one row only;
- removed thread disappears with focus moved predictably;
- desktop keyboard Up/Down/Enter works outside text inputs;
- mobile long-press or explicit select enters selection mode without hover;
- screen reader text includes unread count, attachment and pending state.

- [x] **Step 2: Verify failure**

Expected: FAIL.

- [x] **Step 3: Implement list query adapter**

Read only API projection fields. Preserve object identity for unchanged rows so Vue does not rerender the entire list. Cursor cache is LRU-bounded in memory.

- [x] **Step 4: Implement list rendering**

Use an independent `ThreadRow` with stable key. Start without virtualization because page size is bounded; add a performance test for 100 rows. Virtualization is introduced only if measured rendering misses target.

- [x] **Step 5: Implement selection and keyboard behavior**

Selection state is route/list scoped and clears on user or mailbox change. Batch actions call one command endpoint and display per-message partial results when returned.

- [x] **Step 6: Run tests and commit**

```bash
npm run test:v2 -- thread-list
git add frontend/src/entities/thread frontend/src/features/threads frontend/tests/v2/thread-list.test.ts
git commit -m "📥 实现 V2 会话列表游标缓存与批量选择"
```

**Measured verification:** cumulative V2 contracts `20/20` passed and the V2 production build passed. User/mailbox/filter/cursor keys are stable, the LRU cursor cache is memory-only, mailbox switches abort old requests and generation checks prevent late replacement, cursor pages append without duplicate IDs, and precise patches preserve unchanged row identity. Keyboard and mobile selection contracts move focus predictably after removal. The async thread-list chunk is `3.79 KB` gzip and the initial core remains approximately `62.22 KB` gzip.

---

### Task 5: 实现会话详情、正文状态、HTML 安全和附件

**Files:**

- Create: `frontend/src/entities/message/types.ts`
- Create: `frontend/src/features/message-viewer/ThreadDetail.vue`
- Create: `frontend/src/features/message-viewer/MessageTimelineItem.vue`
- Create: `frontend/src/features/message-viewer/MessageBody.vue`
- Create: `frontend/src/features/message-viewer/AttachmentList.vue`
- Create: `frontend/src/features/message-viewer/RemoteImageControl.vue`
- Create: `frontend/src/features/message-viewer/ImageViewer.vue`
- Create: `frontend/src/features/message-viewer/export-pdf.ts`
- Create: `frontend/src/features/message-viewer/body-sanitizer.ts`
- Create: `frontend/tests/v2/message-viewer.test.ts`

**Interfaces:**

- Produces body states: not_requested, queued, fetching, ready, evicted, failed, unavailable.
- Produces `requestBody(messageId)` and `requestAttachment(attachmentId)` task-aware actions.

- [ ] **Step 1: Write detail and security tests**

Tests cover:

- detail structure renders before body;
- latest unread message expands by default;
- old messages remain folded until user expands;
- ready body is fetched and rendered;
- queued/fetching shows explicit task state;
- unavailable state explains account connectivity and retry action;
- repeated open reuses one body task;
- script, event attributes, forms, dangerous URLs and application CSS are removed;
- remote images are blocked until user allows them;
- links display target domain and open safely;
- attachment miss shows progress and later local download;
- dangerous SVG/HTML attachment is not embedded same-origin;
- clicking a body image opens a viewer with desktop zoom/keyboard navigation and mobile pinch/drag/swipe behavior;
- PDF export uses the sanitized rendered content, preserves light/dark-independent readable colors, and does not mutate original message or reply/forward source;
- one failed message does not crash thread view.

- [ ] **Step 2: Verify failure**

Expected: FAIL.

- [ ] **Step 3: Implement sanitizer policy**

Configure DOMPurify with reviewed allowlist. Rewrite links through safe component, remove remote resource URLs by default, and render body inside a scoped isolated wrapper. Apply existing color-contrast concepts through a V2 utility with separate tests, not by importing legacy page state.

- [ ] **Step 4: Implement state-aware body loading**

Body query key is message ID plus content version. A `202` response subscribes to `message.body_state`; ready event invalidates only that body query. Avoid polling while realtime works; use bounded fallback polling after disconnect.

- [ ] **Step 5: Implement attachment flow**

Metadata displays immediately. Clicking cache miss requests task and shows cancel-safe progress. Browser download starts only after API confirms authenticated object availability.

- [ ] **Step 6: Implement image viewer and PDF export**

Build the image list from authenticated inline/body image references only. Desktop supports wheel/buttons and arrow-key navigation; mobile supports pointer-event pinch, drag and horizontal switching with interruptible transforms. PDF export clones the sanitized message DOM, removes application controls and remote placeholders, normalizes printable contrast, then calls the existing project PDF mechanism or a reviewed replacement without embedding unsafe original HTML.

- [ ] **Step 7: Run tests and commit**

```bash
npm run test:v2 -- message-viewer
git add frontend/src/entities/message frontend/src/features/message-viewer frontend/tests/v2/message-viewer.test.ts
git commit -m "📖 实现 V2 会话详情安全正文与附件体验"
```

---

### Task 6: 实现实时连接、精准投影更新和断线恢复

**Files:**

- Create: `frontend/src/shared/realtime/events.ts`
- Create: `frontend/src/shared/realtime/client.ts`
- Create: `frontend/tests/v2/realtime.test.ts`

**Interfaces:**

- Produces: `RealtimeClient.connect(afterSequence: number)`.
- Produces event handlers that patch or invalidate exact query scopes.
- Produces connection states: connecting, online, reconnecting, offline, resync_required.

- [ ] **Step 1: Write realtime tests**

Tests prove:

- monotonic events apply once;
- duplicate sequence ignored;
- sequence gap triggers backlog request;
- `resync_required` invalidates only supplied scopes;
- thread update patches list and open detail;
- body-ready invalidates one message body;
- auth/session event returns to login;
- version change prompts safe refresh;
- reconnect backoff is bounded and reset after stable connection;
- disconnect falls back to low-frequency status refresh without page reload.

- [ ] **Step 2: Verify failure**

Expected: FAIL.

- [ ] **Step 3: Implement event decoder**

Validate event type and schema before dispatch. Unknown event types are ignored with safe diagnostics and trigger no broad cache mutation.

- [ ] **Step 4: Implement connection lifecycle**

Use Bootstrap cursor, heartbeat timeout, exponential reconnect with jitter and visibility awareness. Do not reconnect aggressively while browser is hidden.

- [ ] **Step 5: Run tests and commit**

```bash
npm run test:v2 -- realtime
git add frontend/src/shared/realtime frontend/tests/v2/realtime.test.ts
git commit -m "🔔 实现 V2 实时事件与精准缓存更新"
```

---

### Task 7: 实现本地操作反馈、撤销和冲突中心

**Files:**

- Create: `frontend/src/entities/operation/types.ts`
- Create: `frontend/src/features/operations/ThreadActions.vue`
- Create: `frontend/src/features/operations/UndoToast.vue`
- Create: `frontend/src/features/operations/PendingState.vue`
- Create: `frontend/src/features/operations/ConflictCenter.vue`
- Create: `frontend/src/features/operations/operation-actions.ts`
- Create: `frontend/tests/v2/operations.test.ts`

**Interfaces:**

- Produces actions for read, star, archive, move, labels, trash, permanent delete, query-scoped mark-all-read and undo.
- Produces conflict views for draft versions, uncertain send, missing mailbox and operation conflict.

- [ ] **Step 1: Write operation UI tests**

Tests cover:

- command success immediately patches returned projection;
- pending marker remains until realtime synced event;
- command failure restores authoritative server projection, not guessed old state;
- undo cancels pending or sends compensation command;
- partial thread failure displays per-message/account details;
- permanent delete requires explicit confirmation and typed target name;
- conflict center allows only API-supported resolutions;
- repeat click while command in flight deduplicates or disables safely;
- mailbox/filter mark-all-read shows affected-count confirmation, submits one bulk operation group and reports batch progress without freezing the list;
- mobile actions available in bottom toolbar.

- [ ] **Step 2: Verify failure**

Expected: FAIL.

- [ ] **Step 3: Implement command adapter**

Use server-returned projection and operation IDs as the only optimistic state. Do not predict provider-specific outcomes in the browser.

- [ ] **Step 4: Implement undo lifecycle**

Toast has bounded visible timer but operation undo capability comes from server expiry, not animation timer alone. Keyboard focus can reach Undo.

- [ ] **Step 5: Run tests and commit**

```bash
npm run test:v2 -- operations
git add frontend/src/entities/operation frontend/src/features/operations frontend/tests/v2/operations.test.ts
git commit -m "↩️ 实现 V2 邮件操作反馈撤销与冲突中心"
```

---

### Task 8: 实现写信、草稿版本、附件和发送队列

**Files:**

- Create: `frontend/src/features/compose/ComposePage.vue`
- Create: `frontend/src/features/compose/RecipientFields.vue`
- Create: `frontend/src/features/compose/IdentitySelector.vue`
- Create: `frontend/src/features/compose/ComposeEditor.vue`
- Create: `frontend/src/features/compose/DraftAttachments.vue`
- Create: `frontend/src/features/compose/ServerPathPicker.vue`
- Create: `frontend/src/features/compose/ScheduleSendDialog.vue`
- Create: `frontend/src/features/compose/DraftConflictDialog.vue`
- Create: `frontend/src/features/compose/compose-state.ts`
- Create: `frontend/tests/v2/compose.test.ts`

**Interfaces:**

- Produces autosave with expected version and conflict handling.
- Produces immediate/scheduled send queue status.
- Tiptap chunk loads only when compose route or reply panel opens.

- [ ] **Step 1: Write compose tests**

Tests cover:

- new message defaults to selected/default identity;
- reply uses receiving account identity;
- switching identity updates signature and Reply-To after confirmation if content changed;
- autosave is debounced and versioned;
- conflict preserves both versions and does not overwrite;
- upload progress, size validation and cancel work;
- authorized NAS/server path picker shows only API-provided logical roots, supports keyboard/touch navigation and imports the selected file into draft attachments;
- path picker never displays unrestricted host paths and handles permission/path-change errors;
- page navigation waits for final save or offers explicit discard;
- send returns queued state immediately;
- scheduled date is sent as absolute timestamp with timezone;
- queued send can cancel before delivery;
- editor crash does not destroy server draft;
- mobile compose uses full page and safe keyboard viewport.

- [ ] **Step 2: Verify failure**

Expected: FAIL.

- [ ] **Step 3: Implement dynamic editor module**

Import Tiptap only inside `ComposeEditor.vue`. Keep recipient and draft metadata usable while editor chunk loads. Handle chunk failure with retry and preserved draft.

- [ ] **Step 4: Implement autosave state machine**

States: clean, dirty, saving, saved, conflict, failed. One save in flight; further edits schedule the next version. Before unload only show native warning when unsaved changes remain after attempted save.

- [ ] **Step 5: Implement streaming attachment UX**

Use `XMLHttpRequest` or supported upload progress transport without reading whole file into JS memory. Display per-file server validation errors.

- [ ] **Step 6: Implement authorized server-path picker**

Request logical roots/directories from the storage API, keep only root IDs and relative paths in component state, and submit the selected file through the compose import endpoint. The picker cannot accept arbitrary typed absolute paths. Import progress and errors join the same draft attachment list as browser uploads.

- [ ] **Step 7: Run tests and commit**

```bash
npm run test:v2 -- compose
npm run build
git add frontend/src/features/compose frontend/tests/v2/compose.test.ts
git commit -m "📝 实现 V2 写信草稿附件与发送队列界面"
```

---

### Task 9: 实现快速搜索、高级条件、保存搜索和搜索边界提示

**Files:**

- Create: `frontend/src/features/search/SearchPage.vue`
- Create: `frontend/src/features/search/SearchBar.vue`
- Create: `frontend/src/features/search/AdvancedFilters.vue`
- Create: `frontend/src/features/search/SearchResults.vue`
- Create: `frontend/src/features/search/search-state.ts`
- Create: `frontend/tests/v2/search.test.ts`

**Interfaces:**

- Produces structured filter request matching frozen OpenAPI.
- Produces route-serializable search state.

- [ ] **Step 1: Write search tests**

Tests cover:

- typing is debounced and prior request aborted;
- structured conditions serialize/deserialise from route;
- suggestions are keyboard accessible;
- results aggregate by thread and show matching message/field;
- UI explicitly states when only cached bodies are searchable;
- body-evicted result remains searchable by metadata but not body term;
- saved search restores exact validated filters;
- clear history updates UI without affecting saved searches;
- mobile filters use accessible sheet and apply/cancel semantics.

- [ ] **Step 2: Verify failure**

Expected: FAIL.

- [ ] **Step 3: Implement quick and advanced search**

Quick keyword uses same structured endpoint. Advanced filters produce removable chips and do not generate raw query syntax. Empty search returns to current mailbox view.

- [ ] **Step 4: Implement result cursor cache**

Search cache key includes normalized filter object and cursor. Search results do not share list cache with normal mailbox views, but realtime thread patches update matching visible projections.

- [ ] **Step 5: Run tests and commit**

```bash
npm run test:v2 -- search
git add frontend/src/features/search frontend/tests/v2/search.test.ts
git commit -m "🔍 实现 V2 快速与高级组合搜索体验"
```

---

### Task 10: 实现设置、同步中心、管理员和备份恢复页面

**Files:**

- Create: `frontend/src/features/settings/*`
- Create: `frontend/src/features/sync-center/*`
- Create: `frontend/src/features/admin/*`
- Create: `frontend/src/features/backup/*`
- Create: `frontend/src/features/about/AboutPage.vue`
- Create: `frontend/tests/v2/settings-admin-backup.test.ts`

**Interfaces:**

- Produces dynamic routes for settings, sync, admin, backup and About/version information.
- Produces role-gated admin navigation.

- [ ] **Step 1: Write feature tests**

Tests cover:

- body quota default 5 GB and `0` unlimited copy;
- lowering quota shows cleanup task and logical/physical release separately;
- sync page separates summary/body/index/state phases;
- local refresh does not trigger remote sync;
- manual sync explicitly creates task;
- auth-required account has reauthorize action;
- conflict center integrates with operation feature;
- non-admin cannot render or route to admin pages;
- user create/disable/reset flows show destructive confirmations;
- backup scope clearly excludes remote cache;
- backup password never persists in component or browser storage;
- restore inspect displays counts before confirmation;
- restored pending sends and remote operations display explicit `review_required` state, cannot offer automatic execution, and route the user to revalidation or cancellation actions;
- About page displays the backend-reported version, product/build information, license and documentation links without exposing environment secrets, filesystem paths or dependency internals.

- [ ] **Step 2: Verify failure**

Expected: FAIL.

- [ ] **Step 3: Implement settings modules**

Separate appearance, cache quota, remote images and compose preferences. Personal profile, contacts, account icons and notification configuration are implemented in the next task. Save each card independently; one error does not discard another card's saved state.

- [ ] **Step 4: Implement sync center**

Use local status endpoints and realtime events. Display account runtime, next reconcile, current phase, pending operations, recent safe errors and actions. Avoid raw provider responses.

- [ ] **Step 5: Implement admin, backup and About modules**

Lazy load admin and backup. Require role at router and API. High-risk operations use exact confirmation. Backup password inputs clear immediately after request submission. About loads the public version endpoint, shows product/version/license/documentation and provides a safe update-state message; it never renders raw server environment or health diagnostics.

- [ ] **Step 6: Run tests and commit**

```bash
npm run test:v2 -- settings-admin-backup
npm run build
git add frontend/src/features/settings frontend/src/features/sync-center frontend/src/features/admin frontend/src/features/backup frontend/src/features/about frontend/tests/v2/settings-admin-backup.test.ts
git commit -m "🛠️ 实现 V2 设置同步管理与备份页面"
```

---

### Task 11: 实现个人资料、账号管理、联系人、通知与 PWA 壳

**Files:**

- Create: `frontend/src/features/profile/ProfilePage.vue`
- Create: `frontend/src/features/profile/AvatarCropDialog.vue`
- Create: `frontend/src/features/accounts/AccountListPage.vue`
- Create: `frontend/src/features/accounts/AccountSetupWizard.vue`
- Create: `frontend/src/features/accounts/AccountProxyForm.vue`
- Create: `frontend/src/features/accounts/OAuthCallbackPage.vue`
- Create: `frontend/src/features/contacts/ContactListPage.vue`
- Create: `frontend/src/features/contacts/ContactEditor.vue`
- Create: `frontend/src/features/contacts/ContactAutocomplete.vue`
- Create: `frontend/src/features/notifications/NotificationCenter.vue`
- Create: `frontend/src/features/notifications/NotificationSettingsPage.vue`
- Create: `frontend/src/features/account-customization/AccountIconEditor.vue`
- Create: `frontend/src/features/account-customization/image-crop.ts`
- Create: `frontend/src/features/pwa/register.ts`
- Create: `frontend/public/manifest.webmanifest`
- Create: `frontend/public/flymail-sw.js`
- Create: `frontend/tests/v2/personal-notifications-pwa.test.ts`

**Interfaces:**

- Produces profile/avatar, full account CRUD/OAuth/proxy management, contacts/autocomplete, identity signature, account icon and notification UI.
- Produces installable PWA shell that caches only same-origin static application assets and never caches API responses, mail bodies, attachment downloads or credentials.

- [ ] **Step 1: Write feature and PWA tests**

Tests cover:

- profile username/nickname/avatar update uses the authenticated user, enforces uniqueness errors and updates Bootstrap projection;
- account setup supports provider presets, generic IMAP/SMTP, password/authorization code and OAuth without storing credentials in browser state;
- OAuth popup/callback state handles success, cancel, expiry and mismatched session, then refreshes only the affected account;
- proxy form never re-displays saved password/token and clearly distinguishes account traffic from internal FlyMail traffic;
- account disable, reauthorize and delete flows show task/progress and exact destructive confirmation;
- avatar and account-icon crop output is square, preserves orientation and submits crop coordinates/source, while backend remains final `256 × 256 WebP` authority;
- provider default, built-in icon and uploaded icon display consistently in navigation, thread source badges and account settings;
- contacts are user-scoped, support add/edit/delete, quick-add from a message and keyboard-accessible compose autocomplete;
- signature editor is identity-specific and switching identity previews the correct signature;
- notification center displays in-app events, unread count, action links and dismiss/read state;
- notification settings cover Bark, Telegram, enterprise WeChat, DingTalk, Feishu and generic Webhook, never echo saved tokens/secrets, and expose opt-in proxy reuse;
- optional notification-image settings support the maintained `flymail-imgbed` contract or generic reviewed HTTPS publisher, show configured-state only for secrets, and offer text fallback when publishing fails;
- PWA manifest is valid and installable;
- service worker bypasses every `/api/`, WebSocket, body, attachment, backup and upload request;
- service worker uses network-first navigation and caches only static same-origin assets after successful response;
- no mail data is written to localStorage, IndexedDB or Cache Storage;
- PWA offline state shows the application shell and a server-unreachable message, not stale mail content.

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
cd frontend
npm run test:v2 -- personal-notifications-pwa
```

Expected: FAIL because these feature modules and PWA files do not exist.

- [ ] **Step 3: Implement profile, contacts and identity signature UI**

Use generated API types and Query Cache. Image crop remains an interaction helper; submit the source and crop parameters to the backend normalization endpoint. Contact autocomplete debounces, cancels stale requests and merges exact typed addresses without silently replacing user input. Signature HTML is edited through the same safe editor policy as compose.

- [ ] **Step 4: Implement account management and icon customization**

Build provider-first setup with generic advanced fields, async verification status, OAuth popup/callback route, encrypted-secret placeholder semantics, user proxy form, disable/reauthorize/delete flows and identity/signature management. The frontend never retains password, authorization code, OAuth token or proxy password after request submission. Offer provider default, reviewed built-in presets and user upload for account icons; update all visible icon projections through one realtime/query patch.

- [ ] **Step 5: Implement notification center and channel settings**

Notification drawer/details load after the mail shell. Saved channel and image-publisher secrets render as “configured” state only. Test-delivery displays queued/running/success/failure from Worker events. Channel forms use provider-specific public fields but a shared safe secret-input component. Image publisher controls are optional, explain text fallback, and never expose local object paths or public URLs from previous private messages.

- [ ] **Step 6: Implement static-only PWA service worker**

Register only in production build. The service worker must execute an early bypass:

```javascript
if (url.origin !== self.location.origin || url.pathname.startsWith('/api/')) {
  return;
}
```

Also bypass WebSocket upgrades, downloads, uploads and non-GET requests. Cache successful hashed JS/CSS/font/image application assets; never cache authenticated HTML responses containing user state. On logout/version change, clear FlyMail static caches.

- [ ] **Step 7: Run tests and commit**

```bash
npm run test:v2 -- personal-notifications-pwa
npm run build:v2
git add frontend/src/features/profile frontend/src/features/accounts frontend/src/features/contacts frontend/src/features/notifications frontend/src/features/account-customization frontend/src/features/pwa frontend/public/manifest.webmanifest frontend/public/flymail-sw.js frontend/tests/v2/personal-notifications-pwa.test.ts
git commit -m "📱 实现 V2 资料联系人通知图标与 PWA 壳"
```

---

### Task 12: 完成主题、无障碍、动画和前端性能预算

**Files:**

- Create: `frontend/src/shared/accessibility/focus.ts`
- Create or modify: actual V2 shared UI components and styles.
- Create: `frontend/tests/v2/accessibility-performance.test.ts`
- Modify: `frontend/vite.config.ts`

**Interfaces:**

- Produces theme: system, light, dark.
- Produces density: comfortable, compact.
- Produces bundle budget checker.

- [ ] **Step 1: Write accessibility and performance contract tests**

Tests assert:

- interactive controls have accessible names;
- dialogs trap and restore focus;
- dynamic status uses bounded `aria-live`;
- error state includes text/icon, not color only;
- reduced-motion disables nonessential transitions;
- keyboard shortcuts ignore text inputs/editor;
- touch targets meet minimum dimensions in component CSS contracts;
- no initial import path reaches Tiptap, admin, backup or sync chunks;
- generated build manifest reports initial gzip <= 180 KB and async page chunks <= 120 KB.

- [ ] **Step 2: Verify current failures**

Run tests and build budget command; expected failures until styles/splitting complete.

- [ ] **Step 3: Implement focus and shortcut utilities**

Provide focus return stack, roving list selection and shortcut guard. Do not create a general framework beyond current components.

- [ ] **Step 4: Implement motion policy**

Use CSS custom properties around 150–220 ms. All transitions respond to reduced-motion. Bulk realtime update disables per-row transitions.

- [ ] **Step 5: Configure explicit chunks**

Split Vue/vendor core, Tiptap editor, settings/admin/backup/sync features. Avoid one giant manual vendor chunk that still loads at startup.

- [ ] **Step 6: Measure and reduce**

Run build, inspect manifest and gzip sizes. Remove accidental imports or defer modules until budget passes. Do not mark complete from source inspection.

- [ ] **Step 7: Run tests and commit**

```bash
npm run test:v2 -- accessibility-performance
npm run build:v2
git add frontend/src/shared/accessibility frontend/src/shared/ui frontend/src/styles frontend/vite.config.ts frontend/tests/v2/accessibility-performance.test.ts
git commit -m "♿ 达成 V2 无障碍主题与前端性能预算"
```

---

### Task 13: 完成前端全流程集成和 Gate 4

**Files:**

- Create: `frontend/tests/v2/full-workflow.test.ts`
- Modify: `README.md`
- Keep: `frontend/src/main.ts` unchanged until validation/cutover plan.

**Interfaces:**

- Produces: Gate 4 evidence and final frontend entry ready for switch.

- [ ] **Step 1: Add mocked API full workflows**

Desktop and mobile workflow tests cover:

1. login and Bootstrap;
2. navigate unified inbox and native label;
3. paginate thread list;
4. open uncached body and receive ready event;
5. read/star/move/trash/undo and mailbox mark-all-read;
6. image viewer and PDF export;
7. cross-account reply identity and signature;
8. draft autosave conflict;
9. browser upload, authorized NAS-path import and schedule send;
10. advanced search and saved search;
11. profile/avatar, account password/OAuth/proxy setup, contacts/autocomplete and account icon;
12. notification center, channel configuration and test delivery;
13. auth-required account recovery view;
14. settings quota cleanup;
15. admin user operation;
16. backup create/inspect/restore validation;
17. About/version information boundary;
18. PWA install shell and server-unreachable state;
19. WebSocket disconnect and resume.

- [ ] **Step 2: Run all V2 frontend tests**

```bash
cd frontend
npm run test:v2
npm run build:v2
```

Expected: PASS and bundle budgets pass.

- [ ] **Step 3: Run legacy frontend tests and build**

```bash
npm test
npm run build
```

Expected: PASS. Current production UI remains buildable.

- [ ] **Step 4: Verify dynamic import boundaries**

Inspect build manifest and network dependency graph. Confirm initial entry does not include editor, admin, backup or sync feature code.

- [ ] **Step 5: Update README development instructions**

Document V2 URL/command, test commands, responsive breakpoints, server-only offline boundary and that production `main.ts` is not switched yet.

- [ ] **Step 6: Commit and push Gate 4**

```bash
git add frontend/tests/v2/full-workflow.test.ts README.md
git commit -m "✅ 验证 V2 桌面移动端完整前端体验"
git push origin main
```

## Gate 4 Completion Checklist

- [ ] Bootstrap executes once and initial screen is bounded.
- [ ] Desktop, tablet and mobile layouts preserve route/state across breakpoints.
- [ ] Unified folders and native labels are complete.
- [ ] Thread list uses cursor cache, cancellation and precise patches.
- [ ] Thread detail handles every body cache state and safe HTML.
- [ ] Realtime resumes by sequence and never reloads whole page unnecessarily.
- [ ] Local operations, undo and conflicts provide accurate feedback.
- [ ] Compose, draft versioning, upload, immediate and scheduled send are complete.
- [ ] Advanced search communicates cached-body boundary.
- [ ] Settings, sync, admin, backup and About/version are complete and appropriately lazy-loaded.
- [ ] Profiles, contacts, signatures, account icons, notifications, image viewer, PDF export, authorized storage import and static-only PWA are complete.
- [ ] Keyboard, screen reader, reduced motion and touch contracts pass.
- [ ] Initial JS gzip is <= 180 KB and async page chunks <= 120 KB.
- [ ] Legacy frontend tests/build remain green.
- [ ] Current production entry and data remain unchanged.
