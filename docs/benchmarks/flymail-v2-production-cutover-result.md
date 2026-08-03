# FlyMail 0.1.0 生产切换结果

## 结论

FlyMail 已于 2026-08-03 11:24（UTC+8）从 `0.0.25` 切换到 `0.1.0`。

当前生产容器：

- 容器：`flymail`
- 镜像：`benxianyu/flymail:0.1.0`
- 镜像 ID：`sha256:7300483a42d837894795acf6978e7353bb626a15e6406f70c0031114f12346fb`
- 端口：宿主机 `36080` → 容器 `8080`
- 重启策略：`always`
- 状态：`running / healthy`
- 健康版本：`0.1.0`
- Docker Hub：未上传

## 数据切换

用户明确表示已自行备份，并授权跳过旧版本快照启动验证，直接创建新版本。

旧容器停止前，MySQL `8.0.46` 日志记录了正常 `Shutdown complete`。旧数据没有删除，而是在同一文件系统内原子保留为：

```text
/Docker/flymail/data-pre-v2-20260803T032435Z
```

切换后目录：

| 目录 | 大小 | 状态 |
|---|---:|---|
| `/Docker/flymail/data` | 约 203 MiB | 当前 `0.1.0` 生产数据 |
| `/Docker/flymail/data-pre-v2-20260803T032435Z` | 约 739 MiB | 保留的旧 `0.0.25` 数据 |

两个目录位于同一设备，权限均为 `root:root 0755`。旧目录尚未清理。

运行环境文件保存于：

```text
/Docker/flymail/data/flymail/config/container.env
```

该文件为 `root:root 0600`，包含容器重建所需环境变量。密码和密钥未写入 Git、日志或本文档。

## 生产验收

已验证：

- `/api/health` 返回 `status=ok`、版本 `0.1.0`；
- schema 版本为 `17`；
- MySQL 为 `8.0.46`；
- MySQL 数据目录为 `/data/mysql/`；
- MySQL 只绑定 `127.0.0.1`，未发布宿主机 3306；
- Worker 心跳持续新鲜；
- 初始管理员创建成功；
- 管理员登录、会话读取和 Bootstrap 通过；
- 数据库临时表读写通过；
- 配置、日志和对象目录均已创建；
- 容器重启后管理员、文件和 MySQL 数据保持；
- 重启过程 MySQL 记录正常关闭；
- 日志和镜像未出现会话密钥、管理员密码、数据库密码、未脱敏数据库 URL 或 Authorization 凭据；
- 只发布 `8080/tcp` 到宿主机 `36080`。

当前新数据库包含 1 个活跃管理员和 0 个邮箱账号。旧用户、邮箱账号、缓存邮件和设置仍只保存在旧目录中，没有迁移到新数据库。

## 未执行的外部验收

当前没有把真实邮箱账号重新添加到新数据库，因此以下能力尚未在本次生产切换后使用真实外部资源验证：

- Gmail、Outlook、QQ、网易、iCloud、新浪或通用 IMAP/SMTP；
- OAuth 客户端；
- 用户级 HTTP CONNECT 代理；
- 真实发送、收取、附件、通知和图床；
- 桌面与移动浏览器人工验收；
- 生产 IDLE 长连接。

这些项目需要在管理员登录后重新添加测试邮箱和外部端点。健康检查通过只证明 FlyMail、MySQL、Worker 和本地存储正常。

## 常用命令

查看状态：

```bash
docker ps --filter name=^/flymail$
docker inspect flymail --format 'status={{.State.Status}} health={{.State.Health.Status}} image={{.Config.Image}}'
curl -fsS http://127.0.0.1:36080/api/health
```

查看日志：

```bash
docker logs --tail 200 flymail
docker logs -f --tail 100 flymail
```

安全重启：

```bash
docker restart --time 30 flymail
```

停止和启动：

```bash
docker stop --time 30 flymail
docker start flymail
```

使用现有数据和运行环境文件重建 `0.1.0` 容器：

```bash
docker stop --time 30 flymail || true
docker rm flymail || true

docker run -d \
  --name flymail \
  --restart always \
  -p 36080:8080 \
  -v /Docker/flymail/data:/data \
  --env-file /Docker/flymail/data/flymail/config/container.env \
  benxianyu/flymail:0.1.0
```

重建后等待健康：

```bash
until [ "$(docker inspect flymail --format '{{.State.Health.Status}}')" = healthy ]; do
  sleep 2
done
curl -fsS http://127.0.0.1:36080/api/health
```

旧数据目录不得在确认新版本长期稳定前删除。
