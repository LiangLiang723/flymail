# 侧边栏与全局页面间距一致性 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复侧边栏折叠动画重叠和折叠态图标偏心，并让所有桌面一级页面遵循统一的 20px 内容起始线。

**Architecture:** 保留现有 Vue 3 侧栏与 PageFrame 结构，只集中修正侧栏图标轨道几何和模板级外层间距。侧栏所有内缩按钮共用 56px 图标列；页面外层空间由 PageFrame 模板唯一负责，视图根节点不再自行添加 padding。

**Tech Stack:** Vue 3、TypeScript、CSS Grid、Node test、Vite、Docker。

## Global Constraints

- 只修改 `/home/chatgpt/flymail`。
- 不修改认证、数据库业务逻辑或用户数据隔离。
- 不新增或升级生产依赖。
- 不删除、迁移或覆盖 `/Docker/flymail/data`。
- 桌面侧栏宽度保持展开 `248px`、折叠 `72px`。
- 桌面一级页面外层间距统一为 `20px`；移动工作区继续边到边。
- 版本更新到 `0.0.20`，默认不上传 Docker Hub。

---

### Task 1: 锁定侧栏折叠几何回归

**Files:**
- Modify: `frontend/tests/ui-layout.test.mjs`
- Modify: `frontend/src/styles/app-shell.css`

**Interfaces:**
- Consumes: `.app-shell.sidebar-collapsed`、`.nav-item`、`.sidebar-bottom`、`.sidebar-profile-trigger`。
- Produces: 72px 图标轨道内统一的 56px 内缩图标列和无重叠的顶部折叠状态。

- [ ] **Step 1: 写失败测试**

在 `responsive shell keeps a stable 72px icon rail and uses a mobile drawer` 后增加测试，断言：

```js
test('collapsed sidebar keeps every control centered and separates brand from collapse action', async () => {
  const shellCss = await readSource('src/styles/app-shell.css');

  assert.match(shellCss, /--sidebar-item-inset:\s*8px/);
  assert.match(shellCss, /--sidebar-item-icon-column:\s*56px/);
  assert.match(shellCss, /\.nav-item\s*\{[^}]*grid-template-columns:\s*var\(--sidebar-item-icon-column\) minmax\(0,\s*1fr\)/s);
  assert.match(shellCss, /\.sidebar-bottom \.sidebar-row\s*\{[^}]*grid-template-columns:\s*var\(--sidebar-item-icon-column\) minmax\(0,\s*1fr\)/s);
  assert.match(shellCss, /\.app-shell\.sidebar-collapsed \.sidebar-profile-trigger\s*\{[^}]*grid-template-columns:\s*var\(--sidebar-item-icon-column\) 0 0/s);
  assert.match(shellCss, /\.app-shell\.sidebar-collapsed \.brand-icon\s*\{[^}]*opacity:\s*0/s);
  assert.match(shellCss, /\.app-shell\.sidebar-collapsed \.sidebar-header-action\s*\{[^}]*right:\s*17px/s);
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && npm test -- --test-name-pattern="collapsed sidebar"`

Expected: FAIL，缺少统一列宽变量和折叠态规则。

- [ ] **Step 3: 最小实现侧栏几何**

在 `app-shell.css`：

```css
:root {
  --sidebar-item-inset: 8px;
  --sidebar-item-icon-column: calc(var(--app-icon-rail) - (2 * var(--sidebar-item-inset)));
}

.nav-item,
.sidebar-bottom .sidebar-row {
  grid-template-columns: var(--sidebar-item-icon-column) minmax(0, 1fr);
}

.nav-item .sidebar-row-icon,
.sidebar-bottom .sidebar-row-icon {
  width: var(--sidebar-item-icon-column);
}

.sidebar-profile-trigger {
  grid-template-columns: var(--sidebar-item-icon-column) minmax(0, 1fr) 28px;
}

.app-shell.sidebar-collapsed .sidebar-profile-trigger {
  grid-template-columns: var(--sidebar-item-icon-column) 0 0;
}

.app-shell.sidebar-collapsed .profile-chevron {
  visibility: hidden;
  opacity: 0;
}
```

顶部品牌和折叠按钮增加互斥透明度与中心位置，展开时延迟品牌出现，折叠时立即隐藏品牌。

- [ ] **Step 4: 运行定向测试确认通过**

Run: `cd frontend && npm test -- --test-name-pattern="collapsed sidebar"`

Expected: PASS。

### Task 2: 建立统一页面外层间距契约

**Files:**
- Modify: `frontend/tests/page-templates.test.mjs`
- Modify: `frontend/src/styles/layout-system.css`
- Modify: `frontend/src/styles/page-system.css`
- Modify: `frontend/src/views/ComposeEmail.vue`
- Modify: `frontend/src/views/MailList.vue`
- Modify: `frontend/src/views/ContactList.vue`

**Interfaces:**
- Consumes: `PageFrame` 的 `workspace`、`split`、`management`、`document` 模板。
- Produces: 桌面工作区/分栏模板 20px 外层间距，移动端工作区边到边。

- [ ] **Step 1: 写模板间距失败测试**

在 `page-templates.test.mjs` 增加：

```js
test('desktop page templates share one outer content gutter', async () => {
  const layout = await read('src/styles/layout-system.css');

  assert.match(layout, /\.page-frame--workspace > \.page-frame__body,[\s\S]*\.page-frame--split > \.page-frame__body\s*\{[^}]*margin:\s*var\(--page-padding\)/s);
  assert.match(layout, /@media \(max-width:\s*960px\)[\s\S]*\.page-frame--workspace > \.page-frame__body,[\s\S]*\.page-frame--split > \.page-frame__body\s*\{[^}]*margin:\s*0/s);
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && npm test -- --test-name-pattern="outer content gutter"`

Expected: FAIL，workspace/split 尚未定义统一外边距。

- [ ] **Step 3: 最小实现模板间距**

在 `layout-system.css` 增加：

```css
.page-frame--workspace > .page-frame__body,
.page-frame--split > .page-frame__body {
  margin: var(--page-padding);
}

@media (max-width: 960px) {
  .page-frame--workspace > .page-frame__body,
  .page-frame--split > .page-frame__body {
    margin: 0;
  }
}
```

写信页面将 `page-frame__body` 设为带边框、圆角和阴影的统一面板，页面根背景回归 canvas。删除 `MailList.vue`、`ComposeEmail.vue`、`ContactList.vue` 中不再表达真实语义的 `ui-page--edge` 类。

- [ ] **Step 4: 运行定向测试确认通过**

Run: `cd frontend && npm test -- --test-name-pattern="outer content gutter"`

Expected: PASS。

### Task 3: 清理所有页面根节点的重复间距

**Files:**
- Modify: `frontend/tests/page-templates.test.mjs`
- Modify: `frontend/src/views/UnifiedInbox.vue`
- Modify: `frontend/src/views/HistorySync.vue`
- Modify: `frontend/src/views/AccountList.vue`
- Modify: `frontend/src/views/UserManagement.vue`
- Modify: `frontend/src/views/Settings.vue`
- Modify: `frontend/src/views/About.vue`

**Interfaces:**
- Consumes: PageFrame 统一间距契约。
- Produces: 所有一级视图根节点不再拥有 padding、margin 或纵向滚动。

- [ ] **Step 1: 写全视图扫描失败测试**

在 `page-templates.test.mjs` 增加：

```js
test('page roots never override template-owned outer spacing', async () => {
  const files = [
    'MailList.vue', 'ComposeEmail.vue', 'Backup.vue', 'UnifiedInbox.vue',
    'HistorySync.vue', 'AccountList.vue', 'UserManagement.vue', 'ContactList.vue',
    'Settings.vue', 'NotificationSettings.vue', 'About.vue', 'Profile.vue',
  ];
  const rootPattern = /\.(mail-view|compose-page|backup-page|unified-page|history-sync-page|account-page|user-page|contact-page|settings-page|notify-page|about-page|profile-page)\s*\{([^}]*)\}/g;

  for (const file of files) {
    const source = await read(`src/views/${file}`);
    const styles = [...source.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/gi)].map((match) => match[1]).join('\n');
    for (const match of styles.matchAll(rootPattern)) {
      assert.doesNotMatch(match[2], /(?:^|[;\s])(padding|margin|overflow-y)\s*:/, `${file}: ${match[1]}`);
    }
  }
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && npm test -- --test-name-pattern="page roots never"`

Expected: FAIL，列出当前仍在根节点设置移动端 padding 的页面。

- [ ] **Step 3: 删除重复根间距**

只删除以下根节点 padding，不调整页面内部卡片间距：

- `UnifiedInbox.vue` 移动端 `.unified-page`。
- `HistorySync.vue` 移动端 `.history-sync-page`。
- `AccountList.vue` 移动端 `.account-page`。
- `UserManagement.vue` 移动端 `.user-page`。
- `Settings.vue` 移动端 `.settings-page`。
- `About.vue` 移动端 `.about-page`。

- [ ] **Step 4: 运行定向测试确认通过**

Run: `cd frontend && npm test -- --test-name-pattern="page roots never"`

Expected: PASS。

### Task 4: 文档、版本与完整验证

**Files:**
- Modify: `README.md`
- Modify: `VERSION`
- Modify via `npm run sync-version`: `package.json`, `frontend/package.json`, `docker-compose.yml`, `README.md`

**Interfaces:**
- Produces: `benxianyu/flymail:0.0.20` 本地镜像和已部署的 `flymail` 容器。

- [ ] **Step 1: 更新版本和 README**

将 `VERSION` 更新为 `0.0.20`，执行 `npm run sync-version`。README 说明桌面一级页面统一 20px 外层间距、侧栏折叠图标居中且动画不重叠。

- [ ] **Step 2: 运行完整前端验证**

Run:

```bash
cd frontend
npm test
npm run build
```

Expected: 全部测试通过，构建退出码 0。

- [ ] **Step 3: 运行后端和配置验证**

Run:

```bash
cd backend
python -m unittest discover -s tests -v
cd ..
bash -n scripts/docker-entrypoint.sh
docker compose --env-file .env.example config
git diff --check
```

Expected: 后端测试全部通过，其余检查退出码 0。

- [ ] **Step 4: 构建和临时容器验证**

构建：

```bash
docker build -t benxianyu/flymail:0.0.20 .
```

使用独立临时目录和临时容器名验证健康接口版本、MySQL 8.0、`/data/mysql/`、数据库读写和重启持久化、日志脱敏、镜像元数据无密钥、SIGTERM 安全关闭。测试密码必须包含引号、反斜杠、`@`、`:`、`/` 或 `%`。

- [ ] **Step 5: 安全替换当前容器**

确认当前 `flymail` 容器挂载 `/Docker/flymail/data:/data` 后，用 `0.0.20` 镜像重建，保持原环境、端口、重启策略和挂载；验证健康和 MySQL 数据目录。不删除 `/Docker/flymail/data`。

- [ ] **Step 6: 提交和推送**

检查 `git status`、`git diff`、暂存差异和密钥扫描，仅提交本次文件。提交标题使用：

```text
🎨 统一侧边栏折叠与页面外层间距
```

推送 `origin/main`，不上传 Docker Hub。
