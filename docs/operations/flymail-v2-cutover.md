# FlyMail V2 生产切换与回滚

## 当前边界

生产数据目录固定为：

```text
/Docker/flymail/data
```

生产容器固定为：

```text
flymail
```

旧版和 V2 的数据库结构不兼容。旧镜像不能读取 V2 数据，V2 也不能被当作旧版数据的原地升级程序。

在以下条件全部满足前，不得切换：

- 最终版本号已由用户明确确认并同步。
- 发布候选镜像已经完成完整测试、容器 smoke 和秘密扫描。
- 正式 Worker 已装配全部生产任务处理器与真实协议网关。
- 至少一个真实测试邮箱、代理/通知端点和目标浏览器矩阵已完成。
- `/Docker/flymail/data` 已创建并验证完整回滚快照。
- 用户已针对“替换 `/Docker/flymail/data`”给出单独、明确的破坏性确认。

健康检查通过不等于以上条件通过。

## 1. 只读生产盘点

不得在盘点阶段修改生产数据。

```bash
docker inspect flymail --format '{{.Config.Image}} {{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}'
curl -fsS http://127.0.0.1:8080/api/health
sudo du -sh /Docker/flymail/data
sudo du -sh /Docker/flymail/data/mysql /Docker/flymail/data/flymail
```

记录：

- 当前版本和镜像
- 容器状态和健康状态
- MySQL 版本、`/data/mysql/` 和 `127.0.0.1` 绑定
- 用户、账号、邮件摘要和活动任务的安全聚合计数
- 当前 Git SHA
- 目标镜像 digest

不要记录用户名、邮箱地址、主题、正文、文件名、数据库密码或连接 URL。

## 2. 创建回滚快照

快照应位于同一宿主机但不在生产目录内部，例如：

```bash
timestamp=$(date +%Y%m%d-%H%M%S)
snapshot=/Docker/flymail/snapshots/flymail-$timestamp
```

先正常停止容器，确认 MySQL 安全关闭：

```bash
docker stop --time 30 flymail
docker logs flymail 2>&1 | tail -n 200
```

如果日志显示强制终止、恢复失败或损坏，立即停止切换并调查。

创建保留副本：

```bash
sudo mkdir -p /Docker/flymail/snapshots
sudo cp -a --reflink=auto /Docker/flymail/data "$snapshot"
```

记录大小和最终清单摘要：

```bash
sudo du -sh "$snapshot"
sudo find "$snapshot" -xdev -type f -print0 \
  | sort -z \
  | sudo xargs -0 sha256sum \
  | sha256sum
```

之后立即重新启动旧生产容器，直到正式切换窗口：

```bash
docker start flymail
```

## 3. 验证旧快照

禁止直接把验证容器挂载到生产目录。先复制快照到独立临时目录：

```bash
verify_dir=$(mktemp -d /tmp/flymail-old-verify.XXXXXX)
sudo cp -a --reflink=auto "$snapshot"/. "$verify_dir"/
```

使用旧镜像、不同容器名和不同宿主机端口启动验证。环境密钥和数据库密码必须从现有安全配置注入，不写入命令历史或 Git。

至少验证：

- 旧容器达到 healthy。
- `/api/health` 返回旧版本。
- MySQL 8.0、数据目录和绑定地址正确。
- 安全聚合用户/账号/摘要计数与生产盘点一致。
- 容器重启后计数仍一致。
- 停止时 MySQL 安全关闭。

验证结束只清理临时验证容器和 `verify_dir`，不得删除 `snapshot`。

## 4. 单独请求破坏性确认

正式替换前的确认信息必须包含：

- 要替换的精确目录：`/Docker/flymail/data`
- 当前版本和当前镜像
- 目标版本、目标镜像和 digest
- 已验证快照的精确路径、大小和摘要
- 明确说明用户、邮箱账号、缓存邮件和设置将被重置
- 明确说明回滚需要停止 V2 并恢复旧快照
- 明确说明旧版本不能读取 V2 数据

通用的“继续”“部署”或“完成所有任务”不等于此项确认。

## 5. 切换窗口

获得明确确认后：

```bash
docker stop --time 30 flymail
```

再次检查安全关闭日志。然后优先原子保留旧目录，而不是删除：

```bash
preserved=/Docker/flymail/data.pre-v2-$timestamp
sudo mv /Docker/flymail/data "$preserved"
sudo mkdir -p /Docker/flymail/data
```

确认新目录为空且位于预期文件系统：

```bash
sudo find /Docker/flymail/data -mindepth 1 -maxdepth 1 -print
findmnt -T /Docker/flymail/data
```

使用已批准的本地镜像启动 `flymail`。不得临时改用未验证标签，也不得上传 Docker Hub 作为切换步骤。

## 6. V2 生产验收

启动后先验证基础运行时：

- 容器 `healthy`
- `/api/health` 返回目标版本
- MySQL 8.0，`/data/mysql/`，绑定 `127.0.0.1`
- Worker 心跳新鲜
- `/data/flymail/config`、对象目录和日志目录存在
- 日志无数据库密码、会话密钥、邮箱凭据和完整数据库 URL

然后执行真实功能验收：

1. 用一次性初始化管理员登录，并立即修改为受控密码。
2. 更新资料和头像。
3. 添加指定测试邮箱并完成密码/OAuth/代理验证。
4. 验证摘要同步、正文、CID 图片、附件、搜索和邮件操作。
5. 验证发送、Message-ID 和已发送副本策略。
6. 验证联系人、写信、浏览器上传和授权 `/data` 路径导入。
7. 验证通知渠道、图床降级和秘密脱敏。
8. 验证桌面和移动浏览器、PWA、图片查看和 PDF。
9. 创建业务备份，执行检查和隔离恢复演练。
10. 创建安全测试状态，重启 `flymail`，确认用户、任务、数据库和对象持久化。

任何一项失败都不能以健康接口通过为理由继续上线。

## 7. 回滚

若 V2 验收失败：

```bash
docker stop --time 30 flymail
```

保留失败的 V2 数据供诊断：

```bash
failed=/Docker/flymail/data.failed-v2-$timestamp
sudo mv /Docker/flymail/data "$failed"
```

恢复旧目录：

```bash
sudo mv "$preserved" /Docker/flymail/data
```

使用原旧镜像重新创建或启动 `flymail`，验证旧健康和聚合数据计数。不要让旧镜像读取 `$failed`。

若 `preserved` 不可用，使用已验证快照复制恢复，但先保留任何现存目录，不要直接覆盖。

## 8. 观察与清理

旧快照和 `preserved` 至少保留到稳定观察窗口结束。删除旧数据属于新的破坏性操作，必须再次获得用户明确确认。

默认不登录或上传 Docker Hub，也不直接删除 `/Docker/flymail/data`。旧生产目录只能在明确确认后原子改名保留；任何后续永久删除都属于新的破坏性操作。
