# FlyMail 0.1.0 发布候选验证证据

## 发布结论

FlyMail V2 已形成最终本地发布候选版本 `0.1.0`。源码入口、前端构建、MySQL schema 17、完整 Worker/Provider 运行时、容量、故障恢复、单容器生命周期和秘密扫描均通过自动化验证。

本证据不代表真实 Gmail、Outlook、QQ、网易、iCloud、新浪或通用 IMAP/SMTP 账号已经验收，也不代表生产容器已经切换。当前生产容器和 `/Docker/flymail/data` 在本发布任务中未被替换、迁移或清空。

## 版本与构建产物

| 项目 | 结果 |
|---|---|
| 最终版本 | `0.1.0` |
| 实现基线 | `da8be49`，加本发布版本、锁文件、OpenAPI 快照和证据改动 |
| 本地镜像 | `benxianyu/flymail:0.1.0` |
| 镜像 ID | `sha256:7300483a42d837894795acf6978e7353bb626a15e6406f70c0031114f12346fb` |
| 镜像大小 | `530,903,360` bytes |
| Docker Hub | 未上传 |
| OpenAPI | 90 paths、114 operations、138 schemas |
| OpenAPI SHA-256 | `a8273c8e23a6a981bd3eec84a4d470d3c9019f28d78c4f4e1eb320237c2067e2` |
| V2 schema | `17` |

`VERSION`、根目录 `package.json`、`frontend/package.json`、`frontend/package-lock.json`、`docker-compose.yml` 和 README 镜像示例均为 `0.1.0`。前端锁文件只同步根包版本字段，依赖版本与完整性哈希未改变。

## 后端验证

在独立容器和独立数据库 `flymail_v2_release_test` 中执行：

```bash
python -m unittest discover -s tests -v
```

结果：

- `653/653` 通过；
- 失败：0；
- 跳过：0；
- 用时：`355.912` 秒；
- MySQL：8.0.46；
- 测试数据库和对象目录均位于隔离容器及 `/tmp`，未使用生产目录。

第一次发布回归运行时，已完成容量验证但仍驻留的临时容器占用约 6 GiB 内存，导致宿主机 Swap 满载，并污染 SIGTERM 与数据库故障时序测试。该临时容器被确认属于本任务后清理；受影响的故障注入和 SIGTERM 用例分别单独通过，随后从重建的测试数据库完成上述 `653/653` 全量重跑。未通过放宽超时或删除测试来迁就环境。

## 前端验证

使用同步后的 `frontend/package-lock.json` 执行 `npm ci`，审计结果为 0 个已知漏洞。随后执行：

| 检查 | 结果 |
|---|---|
| 兼容前端测试 | `96/96` 通过 |
| V2 前端测试 | `64/64` 通过 |
| TypeScript / Vue 类型检查 | 通过 |
| Vite 生产构建 | 通过，`265` modules transformed |
| 初始 JavaScript | `66.70 KiB gzip`，预算 `180 KiB` |
| 最大异步 chunk | `65.64 KiB gzip`，预算 `120 KiB` |

## 静态、Shell 与 Compose 检查

以下检查均通过：

- Python `compileall`；
- `bash -n scripts/docker-entrypoint.sh`；
- `bash -n scripts/test-v2-container.sh`；
- `bash -n scripts/check-v2-secrets.sh`；
- `docker compose --env-file .env.example config`；
- `git diff --check`；
- Compose 仅发布容器 `8080`，未发布 MySQL `3306`。

## 最终镜像与隔离容器

`scripts/test-v2-container.sh benxianyu/flymail:0.1.0` 通过：

- 容器达到 `healthy`；
- `/api/health` 返回版本 `0.1.0`；
- MySQL 为 8.0，数据目录 `/data/mysql/`；
- MySQL 只绑定 `127.0.0.1`；
- `/data/flymail` 正常创建；
- schema 17 迁移完成；
- Worker 心跳正常；
- 空数据库首次管理员创建正常；
- 数据库标记、对象文件和任务状态在容器重启后仍存在；
- 过期租约可以恢复；
- 容器停止时 MySQL 安全关闭；
- 使用包含引号、反斜杠、`@`、`:`、`/` 和 `%` 的数据库测试密码通过。

## 安全与秘密扫描

`scripts/check-v2-secrets.sh benxianyu/flymail:0.1.0` 通过：

- 生成的会话密钥、管理员密码、数据库密码和 OAuth 客户端秘密未出现在镜像配置、镜像历史、容器日志、应用日志、Compose 渲染或 Git 差异中；
- 未发现未脱敏数据库 URL；
- 未发现 Authorization 凭证；
- 未发现会话签名密钥；
- 镜像端口仅为 `8080/tcp`；
- `/Docker/flymail/data` 未参与烟测或秘密扫描。

## Provider、Worker 与协议证据

正式 Worker 装配精确的 17 类持久化任务处理器，覆盖账号验证/清理、同步、正文/附件、邮件操作、可靠发送、通知和缓存清理。正式运行时已经接通：

- Gmail/Outlook PKCE OAuth 与刷新；
- 用户级 HTTP CONNECT 代理；
- IMAP/SMTP TLS、STARTTLS、密码与 XOAUTH2；
- 摘要、BODYSTRUCTURE、正文、CID、附件和 raw EML；
- 已读、星标、标签、移动、归档和删除；
- SMTP 部分接受、不确定结果、Sent 查验与追加副本；
- 周期轮询、退避、去重和断点恢复；
- 账号删除的有界数据库清理和延迟物理对象回收。

Provider、协议、代理和正式 Worker/OAuth/网络/同步/轮询自动化合同累计 `100/100` 通过。生产 IDLE 长连接尚未装配，持续同步依赖每账号轮询间隔。

## 故障恢复证据

故障矩阵结果：

- 顶层矩阵 `3/3` 通过；
- 14 类必需故障场景全部覆盖；
- 实际重跑 17 个生产路径测试；
- 双 Worker `SKIP LOCKED` 并发领取连续 20 次通过；
- 不变量失败 0；
- 未增加生产混沌环境变量或后门。

覆盖事务提交前后终止、MySQL 不可用、对象写入失败/缺失、IMAP/SMTP 中断、远端结果不确定、Outbox、IDLE 半开、限流、SIGTERM 和时钟调整。

## 两千万容量证据

独立容量数据库生成并验证：

- 50 用户；
- 300 邮箱账号；
- 20,000,000 邮件摘要；
- 5,000,000 线程与线程投影；
- 1,000,000 正文搜索文档；
- 1,200 Worker 任务。

13 个 30 样本 API/Worker P95 指标全部通过。最慢 API 路径为结构化搜索 `24.754 ms` P95；混合 FULLTEXT 为 `13.966 ms` P95。线程列表、结构化搜索、任务领取、远端实例和配额 LRU 的 5 个 `EXPLAIN ANALYZE` 均使用承诺索引且没有全表扫描。

容量测试发现 MySQL ngram 处理长 ASCII 关键词时会扩大候选缓存；schema 17 增加标准与 ngram 双 FULLTEXT 索引，ASCII/数字走标准解析器，CJK 关键词走 ngram。

## 文档一致性

以下内容已与当前行为同步：

- README 部署、版本、能力和限制；
- `.env.example`；
- Docker Compose；
- 单容器入口脚本；
- V2 容器、容量、真实服务商、备份恢复和生产切换文档；
- OpenAPI 冻结快照。

`FLYMAIL_SESSION_SECRET` 同时保护业务凭据，已有实例不能直接轮换；MySQL 密码通过容器启动时 `ALTER USER` 轮换；首次管理员变量只对空数据库生效。

## 外部验收与能力边界

本 DevSpace 没有隔离邮箱账号、OAuth 客户端、测试代理、通知端点、图床、桌面浏览器、移动设备或屏幕阅读器。因此以下项目仍为 `blocked`，没有冒充通过：

- Gmail、Outlook、QQ、网易、iCloud、新浪和 Generic IMAP/SMTP 真实收发；
- 真实 OAuth 授权与刷新；
- 真实 HTTP CONNECT 代理；
- Bark、Telegram、企业微信、钉钉、飞书、Webhook 和图床投递；
- Chrome、Edge、Firefox、Safari、iOS、Android 和屏幕阅读器人工验收；
- 生产 IDLE 长连接；
- 宿主机断电和生产负载长时间混沌演练。

健康检查只证明 FlyMail、MySQL、schema、Worker 心跳和对象目录可用，不证明第三方邮箱连接可用。

## 生产状态

- 当前生产容器 `flymail` 尚未替换；
- 当前生产 `/Docker/flymail/data` 尚未迁移或清空；
- 最终镜像只存在于本地 Docker；
- 未执行 `docker login` 或 `docker push`；
- 生产切换必须先读取当前状态、创建并验证完整回滚快照，再取得针对 `/Docker/flymail/data` 替换的单独明确批准。
