# FlyMail 0.1.4 V1 全量 UI 恢复验证

日期：2026-08-03
版本：`0.1.4`
镜像：`benxianyu/flymail:0.1.4`
镜像 ID：`sha256:5fdefe8f608de6a691cbaf9a94b00a3dfc8d5a0a0007975eb7b7dc81eec80c1f`

## 目标与边界

本次发布保留 V2 的 FastAPI API、服务端会话、CSRF、Vue Router、Pinia 状态、实时事件、线程、正文与附件异步缓存、搜索、写信、同步、通知、管理员和备份能力，只恢复 V1 的完整展示层。

正式入口仍为 `frontend/src/app/AppV2.vue`，所有业务请求仍使用 `/api/v2`。未切回旧 `App.vue`，未修改数据库 schema、Worker、对象存储、认证方式或 `/data` 路径。

## UI 恢复范围

- 恢复 V1 登录卡片、Logo、表单、错误提示和主按钮。
- 恢复 248px 展开 / 72px 折叠的全局侧栏、移动抽屉、主导航、通知、用户菜单和版本信息。
- 恢复紫色强调色、系统字体、细边框、紧凑圆角、克制阴影和浅色/深色语义 token。
- 邮件路由保留 V2 的账号/文件夹、会话列表、会话详情三栏能力，并恢复 V1 列表、选中态、正文卡片、附件和图片查看器外观。
- 写信、搜索、联系人、账号、同步、通知、资料、设置、管理员、备份和关于页面恢复为 V1 独立工作区，不再被强制放入邮件三栏。
- V2 弹窗、向导、冲突处理、定时发送、服务器路径选择、通知详情、头像裁剪和高级筛选统一使用 V1 视觉层。

## 自动化验证

### 前端

- V2 测试：`70/70` 通过。
- 兼容前端测试：`96/96` 通过。
- TypeScript 类型检查与 Vite 生产构建：通过。
- 初始 JavaScript：`74.85 KiB gzip / 180 KiB`。
- 最大异步 chunk：`65.64 KiB gzip / 120 KiB`。
- Playwright 视口烟测：通过。
  - 匿名登录页恢复 V1 Logo 和欢迎卡片。
  - 1440×900 桌面邮件页显示全局侧栏和导航/列表/详情三栏。
  - 设置页不渲染邮件三栏。
  - 390×844 移动端无横向溢出，移动侧栏可打开。
  - 所有检查页面无非预期控制台错误。

### 后端

完整测试在候选镜像内部使用独立 MySQL 8.0 测试库、root socket 和临时对象目录执行：

```text
Ran 659 tests in 312.250s
OK
```

OpenAPI 路径、操作和 schema 数量保持不变：

```text
version=0.1.4
paths=90
operations=114
schemas=138
sha256=bcdcff8e5b4e4a90621d832b41e75b3930044b95f854e22e1732e6278afaf813
```

### 静态与构建检查

- `bash -n scripts/docker-entrypoint.sh`：通过。
- `docker compose config -q`：使用一次性必填变量通过。
- `git diff --check`：通过。
- Docker 镜像构建：通过。
- Docker Hub：未上传。

## 隔离容器验证

项目官方 `scripts/test-v2-container.sh` 与 `scripts/check-v2-secrets.sh` 均通过。测试使用包含引号、反斜杠、`@`、`:`、`/` 和 `%` 的数据库密码，并使用独立临时容器和数据目录。

```text
container_health=passed
mysql_version=8.0
mysql_data_dir=/data/mysql/
mysql_bind_address=127.0.0.1
worker_heartbeat=passed
initial_admin=passed
restart_persistence=passed
lease_recovery=passed
mysql_shutdown=passed
exact_secret_scan=passed
database_url_scan=passed
authorization_scan=passed
session_secret_scan=passed
published_ports=8080/tcp-only
production_data_touched=no
```

测试容器和测试数据在验证后已删除。

## 正式部署

部署前只读基线：

```text
image=benxianyu/flymail:0.1.3
health=healthy
version=0.1.3
schema_version=17
users=1
accounts=0
schema_rows=17
mount=/Docker/flymail/data:/data
host_port=36080
```

首次尝试使用当前仓库 Compose 原位重建时，Docker 发现旧 `flymail` 容器不属于当前 Compose 项目并拒绝创建同名容器。保护逻辑恢复了 `0.1.3`，但恢复命令错误地把未导出的 `FLYMAIL_SESSION_SECRET` 写成了只有变量名、没有值的环境条目，入口脚本因此在 MySQL 启动前拒绝运行。数据库在旧容器停止时已记录 `Shutdown complete`，数据目录未删除、未迁移、未重建。

原会话签名密钥随旧容器删除后无法恢复，因此生成新的 64 位随机签名密钥，并使用保留的 MySQL 密码、原端口和原数据挂载直接启动 `0.1.4`。这会使已有浏览器会话失效并要求重新登录，但不影响用户、邮件、附件或数据库数据。

最终正式状态：

```text
image=benxianyu/flymail:0.1.4
container=flymail
container_status=running
container_health=healthy
health_status=ok
health_version=0.1.4
schema_version=17
users=1
accounts=0
schema_rows=17
mount=/Docker/flymail/data:/data
host_port=36080
restart_policy=always
mysql_version=8.0.46-0ubuntu0.24.04.3
mysql_data_dir=/data/mysql/
mysql_bind_address=127.0.0.1
restart_persistence=passed
production_secret_scan=passed
database_url_redaction=passed
published_ports=8080/tcp-only
mysql_shutdown_log=passed
```

根页面返回 HTTP 200，并包含应用根节点和生产 CSS 资源。正式容器完成一次真实重启后，用户、邮箱账号和 schema 计数仍为 `1:0:17`。

## 仍需外部确认

当前生产数据库没有真实邮箱账号。IMAP、SMTP、OAuth、代理、通知端点、图床、真实附件、浏览器手工视觉细节和移动设备触控仍需使用真实账号、网络和设备确认；健康检查通过不代表所有第三方邮箱服务商均可连接。
