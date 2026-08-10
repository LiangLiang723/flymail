# Filter Dismiss And NPM Audit Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让邮件高级筛选浮层支持点击外部/按 Esc 收起，并把当前前端 npm 审计从 4 个漏洞降到 0。

**Architecture:** 搜索浮层复用项目已有 UserMenu/Tiptap 的 window `pointerdown` + root `contains()` 模式，不引入新依赖；组件挂载时注册、卸载时清理，并保留内部点击不关闭。安全告警通过最小兼容依赖升级解决：DOMPurify 至少 3.4.13，Vite 升到未受当前告警影响的 6.4.3，`@vitejs/plugin-vue` 升到与 Vite 6 兼容的 5.2.4；不使用 `npm audit fix --force`，不升级到 Vite 8。

**Tech Stack:** Vue 3, TypeScript, Node test runner, Vite, DOMPurify, npm audit, Docker.

## Global Constraints

- 只在 `/home/chatgpt/flymail` 的 `main` 分支工作。
- 不删除、迁移或覆盖 `/Docker/flymail/data`。
- 不修改后端 API、认证、数据库结构或邮件同步逻辑。
- UI 修改遵守根目录 `DESIGN.md`；浮层不参与主布局，必须有明确关闭路径。
- 不新增生产依赖；仅升级解决已确认漏洞所需的现有依赖。
- 版本从 `0.0.44` 升级到 `0.0.45`。
- 构建本地 `benxianyu/flymail:0.0.45`，默认不上传 Docker Hub。

---

### Task 1: 高级筛选支持点击外部和 Esc 关闭

**Files:**
- Modify: `frontend/tests/mail-search-layout.test.mjs`
- Modify: `frontend/src/components/mail/MailSearchBar.vue`

**Interfaces:**
- Keeps: `MailSearchBar` 现有 `v-model`、`search`、`clear` 事件和筛选字段语义。
- Produces: `searchRoot: Ref<HTMLElement | null>`；窗口级 `pointerdown`/`keydown` 只控制 `advancedOpen`。

- [x] **Step 1: 写失败的筛选关闭契约测试**

在 `mail-search-layout.test.mjs` 新增一条测试，要求组件：

```js
assert.match(source, /ref="searchRoot"/);
assert.match(source, /const searchRoot = ref<HTMLElement \| null>\(null\)/);
assert.match(source, /!searchRoot\.value\.contains\(event\.target as Node\)[^\n]*advancedOpen\.value = false/);
assert.match(source, /event\.key === 'Escape'[^\n]*advancedOpen\.value = false/);
assert.match(source, /window\.addEventListener\('pointerdown', handlePointerDown\)/);
assert.match(source, /window\.removeEventListener\('pointerdown', handlePointerDown\)/);
assert.match(source, /window\.addEventListener\('keydown', handleKeydown\)/);
assert.match(source, /window\.removeEventListener\('keydown', handleKeydown\)/);
```

- [x] **Step 2: 运行测试确认 RED**

Run: `cd frontend && node --test tests/mail-search-layout.test.mjs`

Expected: FAIL，因为当前组件没有根 ref、外部 pointerdown 和 Esc 关闭逻辑。

- [x] **Step 3: 实现最小关闭逻辑**

`MailSearchBar.vue`：

```ts
import { computed, onMounted, onUnmounted, ref } from 'vue';

const searchRoot = ref<HTMLElement | null>(null);

function handlePointerDown(event: PointerEvent) {
  if (advancedOpen.value && searchRoot.value && !searchRoot.value.contains(event.target as Node)) {
    advancedOpen.value = false;
  }
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') advancedOpen.value = false;
}

onMounted(() => {
  window.addEventListener('pointerdown', handlePointerDown);
  window.addEventListener('keydown', handleKeydown);
});

onUnmounted(() => {
  window.removeEventListener('pointerdown', handlePointerDown);
  window.removeEventListener('keydown', handleKeydown);
});
```

并把根节点改为 `<div ref="searchRoot" class="mail-search-bar">`。内部点击由 `contains()` 保持打开；触发按钮仍由原 click 自己切换。

- [x] **Step 4: 运行聚焦测试确认 GREEN**

Run: `cd frontend && node --test tests/mail-search-layout.test.mjs tests/ui-layout.test.mjs`

Expected: all PASS。

---

### Task 2: 清除 npm audit 4 个漏洞

**Files:**
- Modify: `frontend/package.json`
- Runtime lock (gitignored but used by Docker build): `frontend/package-lock.json`

**Interfaces:**
- Upgrades: `dompurify` to `^3.4.13`; `vite` to `^6.4.3`; `@vitejs/plugin-vue` to `^5.2.4`.
- Keeps: Vue 3 and all application runtime APIs unchanged.

- [x] **Step 1: 保存当前审计 RED 证据**

Run: `cd frontend && npm audit --json`

Expected: exit 1，报告 4 个漏洞：DOMPurify 1 个 moderate；Vite/esbuild/plugin-vue 链路产生 2 个 additional moderate + 1 high。

- [x] **Step 2: 确认兼容版本边界**

Run:

```bash
cd frontend
npm view vite@6.4.3 version engines --json
npm view @vitejs/plugin-vue@5.2.4 version peerDependencies engines --json
npm view dompurify@3.4.13 version --json
```

Expected: Vite 6.4.3 支持 Node 20；plugin-vue 5.2.4 支持 Vite 5/6；DOMPurify 3.4.13 存在。

- [x] **Step 3: 用 npm 更新现有依赖并生成 lock**

Run:

```bash
cd frontend
npm install --save dompurify@^3.4.13
npm install --save-dev vite@^6.4.3 @vitejs/plugin-vue@^5.2.4
```

只接受由 npm 产生的 `frontend/package.json` 和 gitignored `frontend/package-lock.json` 依赖解析变化；不执行 `--force`。

- [x] **Step 4: 验证 audit GREEN 和依赖树**

Run:

```bash
cd frontend
npm audit --audit-level=low
npm ls vite @vitejs/plugin-vue dompurify esbuild --depth=1
```

Expected: audit 0 vulnerabilities；Vite >= 6.4.3、plugin-vue >= 5.2.4、DOMPurify >= 3.4.13，esbuild 不再落入告警范围。

- [x] **Step 5: 运行前端全量测试和生产构建**

Run:

```bash
cd frontend
npm test
npm run build
```

Expected: all tests PASS and `vue-tsc + vite build` PASS。

---

### Task 3: 浏览器、版本、README 和完整验证

**Files:**
- Modify: `README.md`
- Modify: `VERSION`
- Modify via version sync: `package.json`
- Modify via version sync: `frontend/package.json`
- Modify via version sync: `docker-compose.yml`

**Interfaces:**
- Produces release `0.0.45` and local image `benxianyu/flymail:0.0.45`.

- [x] **Step 1: 真实浏览器验证筛选关闭行为**

使用 production build + mock authenticated APIs：

- 1440×900 打开筛选，点击面板内部字段，面板仍存在。
- 点击邮件列表等 `.mail-search-bar` 外部区域，面板立即消失。
- 再次打开，按 Escape，面板消失。
- 390×844 重复点击外部关闭；页面仍无横向溢出。

- [x] **Step 2: 更新 README 与版本**

README 的邮件搜索说明补充“点击筛选浮层外部或按 Esc 可关闭”。将 `VERSION` 改为 `0.0.45`，执行 `npm run sync-version`，并检查根/前端 package 与 Compose/README 镜像标签一致。

- [x] **Step 3: Fresh 完整验证**

Run:

```bash
cd backend && python -m unittest discover -s tests -v
cd ../frontend && npm install && npm audit --audit-level=low && npm test && npm run build
cd ..
bash -n scripts/docker-entrypoint.sh
# 使用 .env.example 的临时非敏感环境校验 docker compose config
git diff --check
git status --short
git diff
```

Expected: 后端、前端、audit、构建、Shell、Compose、diff checks 全通过。

---

### Task 4: Docker 临时验证、生产替换和 Git 交付

**Files:**
- No additional production file expected.

**Interfaces:**
- Produces: healthy `benxianyu/flymail:0.0.45` local image and production container `flymail` with original `/Docker/flymail/data:/data`.

- [x] **Step 1: 构建独立临时容器并验证**

Build: `docker build -t benxianyu/flymail:0.0.45 .`

使用独立临时容器和临时数据目录；测试密码包含引号、反斜杠、`@`、`:`、`/` 或 `%`。验证 healthy、`/api/health` 版本、MySQL 8.0 `/data/mysql/`、`/data/flymail`、DB 读写、restart 持久化、日志脱敏、镜像元数据无密码/密钥、停止时 MySQL 安全关闭，然后清理临时资源。

- [x] **Step 2: 无损替换生产容器**

记录现有 `flymail` 镜像/端口/restart/mount 和缓存邮件行数；保留旧容器回滚，使用 0.0.45 复用同一环境与 `/Docker/flymail/data:/data`。确认 healthy、版本正确、MySQL/datadir 正确、缓存邮件行数不减少、重启后仍 healthy 后删除回滚容器。

- [x] **Step 3: 最终审查、提交、推送**

只暂存本任务文件；检查 `.env`、日志、运行数据、密钥不在 staged diff。提交：

```text
🔒 修复筛选浮层关闭并清除前端依赖漏洞
```

Push `origin main` without force；确认工作区 clean 且与 `origin/main` 一致。Docker Hub 不上传。
