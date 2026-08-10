# Fix Conversation MySQL Alias Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复邮件管理切换到“会话”模式时 MySQL 8.0 因保留关键字别名导致的 1064 查询失败。

**Architecture:** 保持现有会话 CTE、窗口函数、线程键和前端接口不变，只把 `ROW_NUMBER()` 的输出别名从 MySQL 8.0 保留关键字 `row_number` 改为普通别名。用单元回归测试锁定生成 SQL，并在真实 MySQL 8.0 容器中执行会话查询验证兼容性。

**Tech Stack:** FastAPI, aiomysql, MySQL 8.0, Python unittest, Docker.

## Global Constraints

- 只在 `/home/chatgpt/flymail` 的 `main` 分支修改。
- 不删除、重建或迁移 `/Docker/flymail/data`。
- 不改变用户、账号、文件夹隔离条件。
- 不新增或升级生产依赖。
- 版本从 `0.0.42` 升级到 `0.0.43`。

---

### Task 1: MySQL 保留关键字回归测试

**Files:**
- Modify: `backend/tests/test_message_search_db.py`

**Interfaces:**
- Consumes: `db.get_message_conversations(user_uid, account_id, folder, ...)`。
- Produces: 对会话列表 SQL 的别名兼容性回归保护。

- [x] **Step 1: 写失败测试**

断言会话列表 SQL 不包含 `AS row_number` / `WHERE row_number = 1`，并要求使用 `conversation_rank`。

- [x] **Step 2: 验证红灯**

Run: `cd backend && python -m unittest tests.test_message_search_db.MessageSearchDbTests.test_conversation_list_does_not_use_mysql_reserved_row_number_alias -v`

Expected: FAIL，因为 0.0.42 使用 `row_number` 别名。

### Task 2: 最小 SQL 修复

**Files:**
- Modify: `backend/db/__init__.py`

**Interfaces:**
- Keeps: `/api/messages/conversations` 参数、返回结构和线程聚合语义不变。

- [x] **Step 1: 修改别名**

把 `ROW_NUMBER() ... AS row_number` 改为 `AS conversation_rank`，并同步外层 `WHERE conversation_rank = 1`。

- [x] **Step 2: 验证绿灯**

Run: `cd backend && python -m unittest tests.test_message_search_db -v`

Expected: PASS。

- [x] **Step 3: 验证真实 MySQL 别名**

Run inside the FlyMail MySQL 8.0 container: `SELECT 1 AS conversation_rank;`

Expected: 返回 `1`；对应的 `SELECT 1 AS row_number;` 在 MySQL 8.0 返回 1064，证明根因。

### Task 3: 发布验证与部署

**Files:**
- Modify: `VERSION`
- Modify: `package.json`
- Modify: `frontend/package.json`
- Modify: `docker-compose.yml`
- Modify: `README.md`

**Interfaces:**
- Produces: `benxianyu/flymail:0.0.43` 本地镜像和运行中的 `flymail` 容器。

- [x] **Step 1: 确认 0.0.43 版本一致**

检查 `VERSION`、根/前端 `package.json`、Compose 和 README 镜像标签一致。

- [x] **Step 2: 运行完整验证**

Run backend unittest、frontend test/build、`bash -n scripts/docker-entrypoint.sh`、`docker compose config`、`git diff --check`。

- [x] **Step 3: 构建并启动独立临时容器**

使用独立临时数据目录，不挂载 `/Docker/flymail/data`；验证 healthy、`/api/health`、MySQL 8.0、数据库读写/重启持久化、日志脱敏和安全关闭。

- [x] **Step 4: 无损替换当前容器**

保持 `/Docker/flymail/data:/data` 和现有端口/环境不变，替换为 `benxianyu/flymail:0.0.43`；验证健康接口和真实数据库行数不变。

- [ ] **Step 5: 提交和推送**

提交标题使用 `🐛 修复会话查询使用 MySQL 保留关键字导致加载失败`，推送 `origin/main`，不上传 Docker Hub。
