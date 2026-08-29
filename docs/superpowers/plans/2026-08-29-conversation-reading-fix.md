# 会话阅读修正实施计划

> **目标：** 撤回普通邮件列表的误分页改动，让会话按最新到最旧排列，并在会话阅读时只展示每封邮件自己的新增正文。

## 验收条件

- 普通邮件列表恢复原有页码分页与交互。
- 会话接口返回顺序为日期/UID 从晚到早。
- 打开会话默认选中最新一封。
- 会话导航明确显示“按时间从晚到早”。
- 会话阅读正文移除 FlyMail 回复/转发引用块和常见邮件客户端历史引用块；单独打开普通邮件仍保留完整正文。
- 顶部 From/To/Cc/Bcc 元信息继续按存在性显示。

## 预计修改文件

- `backend/db/__init__.py`：会话查询排序。
- `backend/tests/test_message_search_db.py`：会话排序回归测试。
- `frontend/src/utils/sanitize.ts`：增加仅供会话阅读使用的引用裁剪渲染。
- `frontend/src/views/MailList.vue`：恢复列表分页；会话默认选最新、会话正文使用裁剪渲染。
- `frontend/tests/mail-reading-layout.test.mjs`：UI 契约。
- `frontend/tests/mail-body-theme-contract.test.mjs` 或新增聚焦测试：引用裁剪行为。
- `README.md`：同步会话阅读行为。
- `VERSION`/包版本/Compose：补丁版本同步。

## 测试方式

1. 先写失败测试，确认现有行为不满足验收条件。
2. 运行聚焦后端/前端测试。
3. 运行后端全量 unittest、前端全量 test 和 production build。
4. `bash -n scripts/docker-entrypoint.sh`、`docker compose config`、`git diff --check`。
5. 构建 `benxianyu/flymail:<VERSION>`，使用独立 `/tmp` 数据目录启动临时容器验证健康、MySQL、持久化和日志安全。
6. 在确认正式容器挂载仍为 `/Docker/flymail/data:/data` 后替换当前 `flymail` 容器并验证健康。

## 数据影响

- 不删除、不迁移 `/Docker/flymail/data`。
- 不新增破坏性数据库变更。
- 需要重建镜像并替换当前 `flymail` 容器；保留原持久化目录和端口配置。
