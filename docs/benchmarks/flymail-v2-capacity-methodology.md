# FlyMail V2 两千万邮件摘要容量基准方法

## 目标

验证 FlyMail V2 在独立 MySQL 8.0 数据库中保存 2,000 万封合成邮件摘要后，分页、详情、正文、结构化搜索、FULLTEXT、本地操作和 Worker 队列路径仍满足既定 P95 门槛。

本方法只使用合成数据和保留域 `example.test`，不包含真实邮箱地址、主题、正文、附件或凭据。生产目录 `/Docker/flymail/data` 不参与生成或测量。

## 数据模型

默认规模：

```text
users=50
accounts=300
messages=20000000
seed=20260731
body-cache-ratio=0.05
thread-size=4
```

由此生成：

- 5,000,000 个线程
- 20,000,000 条消息摘要
- 20,000,000 条线程成员关系
- 20,000,000 条远端实例及成员关系
- 1,000,000 条缓存正文、正文引用和 FULLTEXT 文档
- 1,200 个持久化 Worker 任务
- Gmail 标签、跨账号线程、冷热账号和固定搜索词

生成器使用真实 V2 迁移和表结构，不建立简化替代表。

## 隔离要求

数据库名必须包含 `capacity`、`benchmark`、`bench` 或 `_test`。生成器拒绝：

- `flymail`
- MySQL 系统数据库
- 不含测试标识的数据库名
- `/Docker/flymail/data` 及其子目录

原始 JSON、日志和临时对象保存在 `.benchmarks/` 或隔离容器 `/tmp`，不提交 Git。

## 生成命令

示例：

```bash
python scripts/generate-v2-benchmark-data.py \
  --database-url "$FLYMAIL_CAPACITY_DATABASE_URL" \
  --users 50 \
  --accounts 300 \
  --messages 20000000 \
  --seed 20260731 \
  --batch-size 250000 \
  --body-cache-ratio 0.05 \
  --object-root /tmp/flymail-v2-capacity-objects \
  --reset \
  --defer-indexes \
  --output /tmp/flymail-v2-capacity-20m.json
```

数据库 URL 通过进程环境提供，不放入命令参数、日志或结果文档。

## 有界内存与导入策略

- Python 不构建按消息增长的对象列表。
- 数据通过数字表和 `INSERT … SELECT` 写入。
- 单次逻辑批次最大 250,000 行。
- 写连接并发固定且不超过 4。
- 生成器可临时移除容量热点表的非主索引，导入后按真实迁移定义恢复唯一索引、普通索引和两套 FULLTEXT。
- InnoDB 一次 `ALTER TABLE` 只能创建一个 FULLTEXT 索引，因此 ngram 与标准全文索引必须依次恢复，不能合并到同一条 DDL。
- 只有索引恢复、`ANALYZE TABLE`、精确计数和状态记录全部成功后才生成最终结果文件。
- 部分失败的数据集不可复用，下一次必须显式 `--reset`。

延迟索引只用于可重建的合成容量数据，不能用于生产迁移。

## 资源配置

结果必须记录：

- CPU 数量
- 可用内存与磁盘
- MySQL 版本
- `innodb_buffer_pool_size`
- `innodb_flush_log_at_trx_commit`
- `innodb_doublewrite`
- `max_connections`
- `tmp_table_size`
- `max_heap_table_size`

导入阶段在隔离、可重建的基准实例临时使用：

```text
innodb_buffer_pool_size=4 GiB
innodb_flush_log_at_trx_commit=2
InnoDB redo log=enabled
innodb_doublewrite=ON
```

导入期间保留 InnoDB redo 和 doublewrite，只把事务提交刷盘策略临时改为 `2`，减少可重建合成数据导入的同步刷盘等待。延迟测量前必须显式恢复 `innodb_flush_log_at_trx_commit=1`；若导入进程或 MySQL 异常退出，整个容量数据库作废并用 `--reset` 重建。

正式延迟测量前必须确认：

```text
InnoDB redo log=enabled
innodb_flush_log_at_trx_commit=1
innodb_doublewrite=ON
```

同时确认全部生产索引存在、`ANALYZE TABLE` 完成且无索引构建任务运行。该加载方式不得用于生产数据迁移或恢复。

## API 门槛

每个场景先预热，再至少采集 30 次：

| 场景 | P95 门槛 |
|---|---:|
| Bootstrap | 300 ms |
| 线程第一页 | 150 ms |
| 线程下一页 | 150 ms |
| 缓存详情结构 | 150 ms |
| 缓存正文首字节 | 200 ms |
| 结构化搜索 | 300 ms |
| 缓存正文 FULLTEXT | 500 ms |

FULLTEXT 使用 schema 17 的混合解析策略：中文、日文、韩文关键词使用 `ft_body_search` ngram 索引；ASCII、数字和其他非 CJK 关键词使用 `ft_body_search_standard` 标准解析器索引。基准关键词必须覆盖标准解析器路径，避免用高频 ngram 候选集合误测英文搜索。
| 本地操作提交 | 200 ms |
| API 数据库连接等待 | 50 ms |

报告 P50、P95、P99、均值和样本数。

## Worker 门槛

使用确定性任务数据和已验证的假 Provider 路径：

| 场景 | P95 门槛 |
|---|---:|
| P0 队列领取 | 500 ms |
| IDLE 事件到本地可见摘要 | 5 秒 |
| 恢复的离线操作开始执行 | 5 秒 |
| Worker 重启恢复 | 30 秒 |

同时确认：

- 慢账号不阻塞其他账号
- 普通附件预取字节数为 0

## EXPLAIN ANALYZE

必须保存规范化计划：

- 线程列表
- 结构化搜索
- Worker 任务领取
- 远端实例定位
- 配额 LRU

计划中的实际时间和循环次数会规范化，避免提交易波动原始值。需要确认承诺的索引路径成立；出现与数据规模成比例的全表扫描或不受控 filesort 时，结果无效。

## 可重复性

小规模自动化测试验证：

- 两次重建计数和指纹完全一致
- 用户隔离
- 跨账号线程
- Gmail 标签
- 冷热账号
- 正文缓存比例
- 关系表精确计数
- 生产目标拒绝
- 固定写并发上限
- 延迟索引恢复完整

最终结果文档只提交汇总、环境、Git SHA、镜像和门槛结论，不提交原始两千万数据或完整日志。
