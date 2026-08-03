# FlyMail V2 生产切换前只读盘点

## 盘点时间与边界

- 日期：2026-08-03
- 操作类型：只读
- 生产容器：未停止、未重启、未替换
- `/Docker/flymail/data`：未写入、未移动、未清空
- 用户名、邮箱地址、邮件主题、正文、凭据和密钥：未读取或记录

## 当前生产状态

| 项目 | 当前值 |
|---|---|
| 容器 | `flymail` |
| 镜像引用 | `benxianyu/flymail:0.0.25` |
| 镜像 ID | `sha256:5540609a9f6f6eb34b38466677152184be35caa1d636c6d35b3462f3243d31be` |
| 容器状态 | `running` |
| 健康状态 | `healthy` |
| 重启次数 | `0` |
| 启动时间 | `2026-08-02T00:25:04.905300549Z` |
| 宿主机端口 | `36080` → 容器 `8080` |
| `/api/health` | `status=ok`, `version=0.0.25` |

## 当前持久化数据

| 路径 | 大小 |
|---|---:|
| `/Docker/flymail/data` | `752 MiB` |
| `/Docker/flymail/data/mysql` | `711 MiB` |
| `/Docker/flymail/data/mysql-files` | `4 KiB` |
| `/Docker/flymail/data/flymail` | `42 MiB` |
| `/Docker/flymail/data/flymail/config` | `8 KiB` |
| `/Docker/flymail/data/flymail/files` | `41 MiB` |
| `/Docker/flymail/data/flymail/logs` | `16 KiB` |

## 当前 MySQL

| 项目 | 当前值 |
|---|---|
| 版本 | `8.0.46-0ubuntu0.24.04.3` |
| 数据目录 | `/data/mysql/` |
| 绑定地址 | `127.0.0.1` |
| 宿主机 MySQL 端口 | 未发布 |

## 当前聚合业务计数

以下值只来自 `COUNT(*)` 和状态分组，没有读取业务行内容。

| 项目 | 数量 |
|---|---:|
| 用户 | 2 |
| 活跃用户 | 2 |
| 管理员 | 2 |
| 邮箱账号 | 4 |
| 已连接邮箱账号 | 4 |
| 缓存邮件 | 6,718 |
| 附件元数据 | 9,449 |
| 历史同步任务 | 12 |
| 通知 | 2 |
| 联系人 | 0 |

## 目标发布候选

| 项目 | 目标值 |
|---|---|
| Git HEAD | `e1bf10ef1b307eb771947c61276e896fef03bf55` |
| 版本 | `0.1.0` |
| 镜像 | `benxianyu/flymail:0.1.0` |
| 镜像 ID | `sha256:7300483a42d837894795acf6978e7353bb626a15e6406f70c0031114f12346fb` |
| Docker Hub | 未上传，仅本地镜像 |

## 下一步审批边界

创建可验证的完整回滚快照需要暂停生产写入并安全停止当前 `flymail`，复制 `/Docker/flymail/data` 到带时间戳的保留目录，再使用快照副本启动旧版临时容器验证。该操作会造成短时服务中断，但不会删除原数据。

完成并验证快照后，仍需第二次、针对以下破坏性操作的明确批准：

- 将当前 `/Docker/flymail/data` 替换为全新的 V2 数据目录；
- 重置当前用户、邮箱账号、缓存邮件、附件状态和设置；
- 使用 `benxianyu/flymail:0.1.0` 启动生产；
- 失败时通过恢复旧目录快照和旧镜像回滚。

旧版 `0.0.25` 不能读取 V2 schema 17 数据，V2 数据也不能直接交给旧版容器使用。
