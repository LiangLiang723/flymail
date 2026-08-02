# FlyMail V2 备份、检查与恢复演练

## 两类备份

FlyMail V2 有两种目的不同的备份，不能互相替代。

### 业务备份

管理员在 FlyMail 中创建的 `.flymailbak` 业务备份使用独立密码加密，用于迁移或审查用户、账号配置和业务关系。它不包含 MySQL 物理文件，也不包含所有缓存对象。

包含：

- 用户、个人资料、联系人和设置
- 邮箱账号、发件身份、签名和加密后的邮箱/代理/通知凭据
- 线程、邮件摘要、成员关系和同步游标
- 草稿及其本地附件引用
- 待审查发送和远端操作
- 通知配置、授权存储根和必要业务对象

不包含：

- 登录会话和 OAuth 临时 state
- 可运行 Worker 任务、Outbox 和投递尝试
- 正文、普通附件、内嵌图片和 raw `.eml` 缓存
- 搜索正文文档和运行日志
- MySQL 物理数据文件

恢复演练会把未完成发送和远端操作转换为 `review_required`，不会自动执行远端操作。

### 完整回滚快照

生产切换前对 `/Docker/flymail/data` 做的文件系统快照包含 MySQL 和全部本地文件，用于把整个旧实例恢复到切换前状态。完整回滚必须使用此类快照，不能只依赖 `.flymailbak`。

## 创建业务备份

1. 以管理员登录。
2. 打开“邮件备份”。
3. 选择“创建业务备份”。
4. 输入仅用于该归档的强密码，至少 12 个字符。
5. 等待状态变为完成。
6. 下载归档并存放到受控位置。

备份密码不会保存在 FlyMail 中。遗失密码后无法恢复归档。

对应 API：

```text
POST /api/v2/admin/backups
GET  /api/v2/admin/backups
GET  /api/v2/admin/backups/{backup_id}
GET  /api/v2/admin/backups/{backup_id}/download
```

这些接口要求管理员会话；创建请求同时要求同源 `Origin` 和 CSRF 令牌。不要把会话 Cookie、CSRF 令牌或备份密码写入脚本、日志或 Git。

## 检查归档

在“邮件备份”中选择归档并执行“检查”，输入创建时使用的密码。检查会验证：

- 加密头和密码校验
- AES-GCM 完整性
- 清单格式和版本
- 每个成员的大小和 SHA-256
- 重复成员、未声明成员和未知成员
- 绝对路径、`..`、符号链接、设备文件和路径越界

对应 API：

```text
POST /api/v2/admin/backups/{backup_id}/inspect
```

错误密码返回独立错误，不会修改数据库。损坏或截断归档会被拒绝。

## 隔离恢复演练

恢复演练不会覆盖当前生产数据库。FlyMail 会：

1. 创建随机临时数据库和临时对象目录。
2. 迁移到当前 schema。
3. 解密归档。
4. 将凭据重新加密到当前实例密钥。
5. 验证表数、schema 和业务关系。
6. 确认可运行 Worker 任务数量为零。
7. 把未完成远端操作固定为 `review_required`。
8. 删除临时数据库和临时目录。

对应 API：

```text
POST /api/v2/admin/backups/{backup_id}/restore-rehearsal
```

生产恢复前必须先成功完成一次演练。演练通过不代表真实邮箱凭据仍有效；仍需真实服务商重新验证。

## 已验证的自动化场景

以下回归在隔离 MySQL 8.0 环境中通过：

```bash
cd backend
python -m unittest -v \
  tests.v2.test_api_backups \
  tests.v2.test_api_backup_secure
```

结果：`7/7` 通过，覆盖创建、列表、下载、检查、错误密码、密文损坏、路径穿越、临时恢复、秘密重加密和未完成操作转人工审查。

## 实例密钥注意事项

`FLYMAIL_SESSION_SECRET` 不仅签名登录会话，还派生邮箱、代理和通知配置的实例加密密钥。已有数据存在时直接更换该值会导致现有加密凭据无法解密。它必须和完整回滚快照一起保存，且不得写入业务备份、日志或 Git。

需要迁移到新实例密钥时，应使用业务备份和恢复演练提供的“解密后再加密”流程，而不是直接修改运行中实例的密钥。

## 校验完整回滚快照

生产切换前的文件系统快照至少记录：

```bash
snapshot=/Docker/flymail/snapshots/flymail-YYYYMMDD-HHMMSS
sudo du -sh "$snapshot"
sudo find "$snapshot" -xdev -type f -print0 \
  | sort -z \
  | sudo xargs -0 sha256sum \
  | sha256sum
```

哈希结果只记录最终摘要，不提交文件清单，因为文件名可能包含敏感信息。

快照必须复制到另一个临时目录，并用旧镜像启动一次隔离容器验证健康和数据计数。验证过程不得挂载 `/Docker/flymail/data` 本身。

## 失败处理

- 错误密码：停止，不重试猜测密码。
- 校验失败：保留原归档，重新创建备份；不要修改损坏文件。
- 临时恢复失败：删除仅由演练创建的临时资源，当前生产实例保持不变。
- 实际切换失败：按切换文档恢复完整旧快照和旧镜像；不要让旧版本读取 V2 数据库。
