# FlyMail V2 容器验证方法

## 目的

本方法验证本地候选镜像的单容器启动、安全关闭、持久化、租约恢复和秘密边界。所有运行数据使用唯一 `/tmp` 目录，禁止使用 `/Docker/flymail/data`。候选镜像只保存在当前 DevSpace 的本地 Docker，不上传 Docker Hub。

## 命令

```bash
IMAGE="benxianyu/flymail:v2-rc-$(git rev-parse --short HEAD)"
docker build -t "$IMAGE" .
scripts/check-v2-secrets.sh "$IMAGE"
```

底层 smoke 也可独立运行：

```bash
scripts/test-v2-container.sh "$IMAGE"
```

## 隔离和秘密

- 每次运行创建唯一容器名、临时数据目录和空闲宿主机端口。
- 会话签名密钥由 `openssl rand -hex 32` 生成。
- 数据库密码包含引号、反斜杠、`@`、`:`、`/` 和 `%`，用于覆盖 URL 编码与 SQL 转义边界。
- 脚本拒绝 `/Docker/flymail/data` 及其子目录。
- 扫描镜像配置与标签、完整镜像历史、容器日志、应用日志、未暂存差异和已暂存差异。
- 精确匹配生成的测试秘密，并额外匹配未脱敏 MySQL URL、Authorization 凭证和会话密钥模式。
- 报告只保存状态、版本和不含秘密的运行元数据；不保存密码、令牌、原始日志或临时路径到 Git。

## 验证范围

1. 容器达到 Docker `healthy`。
2. `/api/health` 返回仓库 `VERSION`。
3. Worker 数据库心跳不超过 30 秒。
4. MySQL 为 8.0，数据目录为 `/data/mysql/`，绑定地址为 `127.0.0.1`。
5. `/data/flymail/config`、`/data/flymail/logs` 和 `/data/flymail/objects/sha256` 可用。
6. 受控数据库标记和内容对象在容器重启后仍存在。
7. 受控过期任务租约通过生产仓储逻辑转换为 `retry_wait`，租约字段被清空并记录 `LeaseExpired`。
8. 容器停止后 MySQL 错误日志包含安全关闭记录。
9. 容器和 Compose 仅暴露/发布 `8080/tcp`，不发布 MySQL 3306。
10. 镜像元数据、历史和运行日志不包含测试秘密。

## 数据规模与资源边界

本 smoke 只创建一个持久化标记、一份小型内容对象和一个过期租约任务；它验证生命周期和安全边界，不代表容量或吞吐性能。2,000 万邮件摘要容量、查询延迟和磁盘占用由独立容量基准记录。真实 IMAP、SMTP、OAuth、代理和浏览器兼容性也不属于本方法。

## 结果模式

Smoke 报告字段：

```text
image
version
container_name
data_dir
container_health
mysql_version
mysql_data_dir
mysql_bind_address
worker_heartbeat
restart_persistence
lease_recovery
mysql_shutdown
production_data_touched
```

秘密扫描报告字段：

```text
image
smoke
exact_secret_scan
database_url_scan
authorization_scan
session_secret_scan
published_ports
production_data_touched
```

## 2026-08-02 实测

候选镜像 `benxianyu/flymail:v2-rc-bb21c3d` 完成上述检查。健康、MySQL 8.0、本地绑定、Worker 心跳、重启持久化、过期租约恢复和安全关闭均通过；秘密扫描未发现测试密码、会话密钥、未脱敏数据库 URL 或 Authorization 凭证；仅发布 `8080/tcp`。生产数据目录未触碰。
