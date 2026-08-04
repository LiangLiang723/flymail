# 历史同步正文补全实时进度 Implementation Plan

**Goal:** 修复历史同步在正文和附件补全阶段看起来长期卡住的问题，让页面显示与后台真实处理一致的实时正文进度。

**Architecture:** 数据库层按现有正文补全筛选口径聚合每个文件夹的总数、已处理数和剩余数；设置路由把该进度附加到现有 `folder_progress`；前端只在 `current_page = 2` 时展示当前文件夹的正文计数。无需数据库迁移，不改变同步算法和持久化目录。

**Tech Stack:** Python 3、FastAPI、MySQL 8.0、Vue 3、TypeScript、Node.js `node:test`、Docker。

## 验收条件

- 生产问题可由现有数据证明为“任务推进但界面无进度”，而不是死锁。
- 正文补全阶段显示 `正文 已处理 / 总数`，数值随三秒轮询变化。
- 正文真实为空但已完成远端检查的邮件计入已处理。
- 用户和账号数据隔离保持不变。
- `/Docker/flymail/data` 不删除、不迁移；正式容器替换后数据和同步任务记录仍存在。
- 完整测试、镜像和临时容器验证通过后才提交、推送和部署。

## 预计修改文件

- `backend/db/__init__.py`：增加正文检查进度聚合查询。
- `backend/routes/settings.py`：在文件夹同步进度接口中返回正文计数。
- `backend/tests/test_cached_body_progress.py`：覆盖数据库聚合结果和筛选条件。
- `backend/tests/test_history_sync_progress.py`：覆盖路由正文进度字段。
- `frontend/src/views/HistorySync.vue`：显示当前文件夹正文补全实时计数。
- `frontend/tests/page-templates.test.mjs`：覆盖正文阶段计数文案契约。
- `README.md`：同步说明正文阶段的实时进度口径。
- `VERSION`、`package.json`、`frontend/package.json`、`docker-compose.yml`：发布版本同步。
- 本设计和计划文档。

## Task 1：先建立失败回归测试

1. 在数据库测试中模拟聚合查询返回 `total=12`、`remaining=8`，期望函数返回 `checked=4`，并验证 SQL 使用空正文和 `cached_message_empty_body_checks` 条件。
2. 在路由测试桩中提供正文进度函数，期望 `_build_folder_progress` 返回 `body_total_count`、`body_checked_count`、`body_remaining_count`。
3. 更新前端静态测试，要求 `HistorySync.vue` 使用正文计数字段并在正文阶段显示“正文”。
4. 运行聚焦测试并确认因生产实现缺失而失败。

## Task 2：实现数据库和接口

1. 在 `db` 增加 `get_cached_body_check_progress(account_id, folder)`，复用 `_expand_folder_aliases`。
2. 聚合总缓存邮件数与仍需检查的邮件数；所有结果规范为非负整数。
3. 在设置路由导入该函数，并在每个 `folder_progress` 项中附加三个正文字段。
4. 运行后端聚焦测试，确认由红转绿。

## Task 3：实现前端实时显示

1. 扩展 `FolderProgressItem` 类型。
2. 增加当前文件夹正文进度解析函数。
3. 在 `syncPhaseText` 的 `current_page === 2` 分支追加 `正文 checked / total`；无法匹配时保留原文案。
4. 运行前端聚焦测试，确认通过。

## Task 4：文档、版本和完整验证

1. README 说明摘要与正文是不同进度口径，正文阶段显示实时计数。
2. 将 `VERSION` 提升到 `0.0.28`，执行 `npm run sync-version` 并核对三个版本来源一致。
3. 执行：
   - `cd backend && python -m unittest discover -s tests -v`
   - `cd frontend && npm install && npm test && npm run build`
   - `bash -n scripts/docker-entrypoint.sh`
   - `docker compose config`
   - `git diff --check`
4. 构建 `benxianyu/flymail:0.0.28`。
5. 使用独立临时目录和临时容器验证健康接口、MySQL 8.0、`/data/mysql/`、数据库读写、重启持久化、日志脱敏、镜像元数据无密钥、SIGTERM 安全关闭。
6. 不影响现有生产同步任务的前提下，安全替换 `flymail`，复用 `/Docker/flymail/data:/data`，验证健康、版本和数据存在。
7. 只暂存本次任务文件，保留现有 `.benchmarks` 暂存文件不变；提交并推送 `origin/main`。Docker Hub 不上传。

## Docker 与数据影响

- 需要重建镜像和当前 `flymail` 容器，才能让接口和前端生效。
- 不需要数据库表结构迁移。
- 不写入或删除 `/Docker/flymail/data` 之外的运行数据。
- 临时验证使用独立目录，不挂载生产数据。
