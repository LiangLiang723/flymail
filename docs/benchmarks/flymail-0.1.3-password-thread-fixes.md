# FlyMail 0.1.3 密码与线程列表修复验证

日期：2026-08-03

## 修复内容

### 线程列表契约

后端 `/api/v2/threads` 的正式响应为 `items` 和 `next_cursor`。旧前端手工类型错误地读取 `threads`，空邮箱登录后把 `undefined` 写入分页缓存，随后触发 `Cannot read properties of undefined (reading 'map')`。

`0.1.3` 保持后端公开契约不变，在前端 API 边界把 `ThreadListItem` 转换为内部 `ThreadProjection`：

- `latest_message_at` 转换为 `latest_at`；
- `latest_snippet` 转换为 `snippet`；
- `pending_operation_count` 转换为 `pending_state`；
- 空 `items` 明确转换为空线程数组；
- 分页缓存只保存归一化后的内部模型。

### 用户密码规则

以下用户输入秘密统一为只要求非空，不设置 FlyMail 最短或最大长度，也不自动去除首尾空格：

- 首次管理员密码；
- 登录密码；
- 用户修改密码；
- 管理员创建用户和重置密码；
- 邮箱密码、授权码和账号凭据；
- HTTP CONNECT 代理密码；
- 业务备份密码。

`FLYMAIL_SESSION_SECRET`、游标签名密钥和凭据加密主密钥属于内部安全密钥，继续保持至少 16 个字符等既有要求。`MYSQL_PASSWORD` 继续要求非空并禁止换行。

## 发布事实

| 项目 | 结果 |
|---|---|
| 版本 | `0.1.3` |
| OpenAPI | 90 个路径、114 个操作、138 个 schema |
| OpenAPI SHA-256 | `3add2d91006ebc27db08f16e4add2ae2bffa5b41ebf036a72dabe1ba110131c7` |
| 本地镜像 | `benxianyu/flymail:0.1.3` |
| 镜像 ID | `sha256:62c0018193765510fb118309701574ff91f2eb84d8f76ddf50117837d6dc99c1` |
| Docker Hub | 未上传 |

## 自动化验证

### 后端

独立 MySQL 8.0 测试数据库完整运行：

```text
Ran 659 tests in 335.989s
OK
```

新增覆盖包括：

- 单字符首次管理员密码；
- 单字符用户创建、登录、改密和管理员重置；
- 精确保留单个空格密码；
- 超过 10,000 字符的登录、邮箱、代理和备份秘密不被应用层截断；
- 空密码仍被拒绝；
- 单字符备份密码可创建和检查加密归档。

### 前端

```text
兼容前端：96/96
V2 前端：65/65
```

线程列表回归先以真实 `{ items: [] }` 响应复现 `undefined !== []`，修复后覆盖空列表、字段转换、分页追加和邮箱切换取消。

生产构建通过：

```text
初始 JavaScript：66.83 KiB gzip / 180 KiB
最大异步 chunk：65.64 KiB gzip / 120 KiB
```

### 静态与容器

- Python `compileall`：通过；
- `docker-entrypoint.sh`、容器烟测和秘密扫描脚本语法：通过；
- Docker Compose 渲染：通过，MySQL 3306 未发布；
- `git diff --check`：通过；
- 标准隔离容器健康、首次管理员、Worker 心跳、重启持久化、租约恢复和 MySQL 安全关闭：通过；
- 镜像配置、容器日志、数据库 URL、Authorization、会话密钥和 Git 差异秘密扫描：通过。

### 镜像级用户流程

使用全新临时数据和以下短秘密启动实际镜像：

- `MYSQL_PASSWORD`：单字符；
- 首次管理员密码：单字符；
- 普通用户密码：单字符；
- 业务备份密码：单字符。

验证结果：

```text
health=passed
root=html
admin_one_character_login=passed
user_one_character_login=passed
empty_threads_items=passed
backup_one_character_password=passed
restart_persistence=passed
mysql_shutdown=passed
version=0.1.3
mysql_version=8.0.46
mysql_datadir=/data/mysql/
mysql_bind=127.0.0.1
schema=17
production_data_touched=no
```

## 能力边界

本次验证没有真实 Gmail、Outlook、QQ、网易、iCloud、新浪或通用 IMAP/SMTP 账号，也没有真实 OAuth 客户端、代理、通知端点和移动浏览器。密码规则和本地页面契约已通过自动化与真实容器验证，但第三方服务商仍可能对密码或授权码施加自身限制。

## 生产部署结果

生产容器已使用现有 `/Docker/flymail/data` 从 `0.1.2` 替换为 `0.1.3`，未删除、重建或迁移数据库。首次替换的新容器已通过核心功能检查，但最后的只读统计 SQL 误用了不存在的 `accounts` 表，自动回滚逻辑随即恢复旧 `0.1.2` 容器并确认健康。修正统计表为 `mail_accounts` 后重新执行完整替换，结果如下：

```text
image=benxianyu/flymail:0.1.3
image_id=sha256:62c0018193765510fb118309701574ff91f2eb84d8f76ddf50117837d6dc99c1
health=healthy
root=html
asset=passed
existing_admin_login=passed
bootstrap=passed
thread_items_contract=passed
restart_persistence=passed
logs_secret_scan=passed
mysql_version=8.0.46
mysql_datadir=/data/mysql/
mysql_bind=127.0.0.1
schema=17
enabled_users=1
mail_accounts=0
data_dir=/Docker/flymail/data
rollback_container=removed
```

MySQL 3306 未发布到宿主机，生产端口仍为 `36080 → 8080`。Docker Hub 未上传。
