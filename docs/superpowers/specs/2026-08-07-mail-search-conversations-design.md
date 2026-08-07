# 邮件搜索与会话设计

## 目标

在不改变 FlyMail 现有 IMAP/SMTP 架构、不引入新生产依赖的前提下，把当前“关键词 + 已读/附件”的单文件夹搜索升级为成熟邮箱常见的快速筛选，并增加准确、可解释的邮件会话视图。

## 搜索体验

邮件管理页提供统一搜索入口，默认搜索当前账号和当前文件夹的本地缓存。用户可以直接输入自然关键词，也可以使用高级条件快速组合：

- 发件人
- 收件人
- 主题
- 正文关键词
- 开始日期 / 结束日期
- 已读 / 未读
- 有附件
- 已星标

搜索框同时支持 Gmail/Outlook 风格的常用运算符：`from:`、`to:`、`subject:`、`after:`、`before:`、`has:attachment`、`is:unread`、`is:read`、`is:starred`。普通文本仍在主题、发件人、收件人、抄送和正文中模糊匹配。高级筛选和搜索运算符可以组合，且所有条件都在 MySQL 本地缓存上执行，不因搜索触发全量 IMAP 查询。

工具栏保留“全部 / 未读 / 已读 / 附件”快捷按钮，并新增“星标”快捷筛选和“高级筛选”入口。启用任意高级条件后显示已应用条件摘要，并可一键清除。移动端复用同一套条件，不维护第二套搜索逻辑。

聚合收件箱补充关键词搜索，并复用已读、附件、邮箱账号筛选；聚合搜索只查询当前用户允许参与聚合的账号，保持数据隔离。

## 会话模型

会话优先使用 RFC 邮件线程头部：

1. `References` 中最早可识别的 Message-ID 作为线程根；
2. 没有 `References` 时使用 `In-Reply-To`；
3. 没有线程头但自身 `Message-ID` 存在时先视为单封线程；
4. 对缺少标准线程头的历史/第三方邮件，仅在同一用户、同一账号内使用规范化主题作为保守兜底，不跨账号自动合并。

缓存层新增 `in_reply_to`、`references_header`、`thread_key` 三个字段。`thread_key` 在邮件解析/缓存时计算，列表读取时直接使用，避免每次打开列表重新扫描正文。

会话默认作为邮件管理页可切换的显示方式，不改变删除、移动、已读等现有单封邮件语义。会话列表显示最新一封邮件的发件人/主题/时间、会话邮件数、未读数和附件状态。点击会话后读取该账号中同一 `thread_key` 的本地缓存邮件，按时间从旧到新展示；每封邮件可展开查看详情。回复仍以用户当前选中的具体邮件 `Message-ID` 作为 `In-Reply-To`。

为了降低误合并风险，主题兜底只处理常见 `Re:` / `Fwd:` / `Fw:` 前缀并做空白归一化，不做模糊相似度匹配。

## 后端边界

- `backend/providers/base.py`：扩展邮件 DTO 的线程字段。
- `backend/providers/base_imap.py`：从列表头部和详情 MIME 解析 `In-Reply-To` / `References`。
- `backend/services/message_threads.py`：集中规范化 Message-ID、主题并生成 `thread_key`。
- `backend/models/__init__.py`、`backend/db/__init__.py`：持久化线程字段，增加搜索条件和会话查询。
- `backend/routes/messages.py`：扩展搜索参数，增加会话列表和会话详情接口。
- `backend/schemas.py`：扩展列表/会话响应结构。

所有数据库查询必须同时带 `user_uid` 和账号归属条件，聚合查询只能使用当前用户设置允许的账号 ID。

## 前端边界

- `frontend/src/components/mail/MailSearchBar.vue`：搜索输入、高级筛选、条件摘要。
- `frontend/src/types/mail.ts`：搜索筛选和会话类型。
- `frontend/src/views/MailList.vue`：接入搜索组件、星标筛选、会话/单封切换和会话详情。
- `frontend/src/views/UnifiedInbox.vue`：接入关键词搜索。

不新增第三方 UI 依赖，沿用现有设计 token、按钮、输入框、卡片和移动端布局。

## 数据迁移与兼容

`init_db()` 通过可重复执行的 `ALTER TABLE` 补充新列和索引，不删除、不迁移现有邮件数据。已有缓存邮件在下一次列表同步或打开详情时逐步补全标准线程头；缺失线程头的旧记录可以根据现有主题生成兜底 `thread_key`，因此升级后无需重建 `/data/mysql`。

## 测试与验收

1. 搜索解析测试覆盖自由文本、字段运算符、日期、布尔筛选和组合条件。
2. 数据库查询测试覆盖用户隔离、账号隔离、正文/抄送搜索、星标、日期范围。
3. 线程键测试覆盖 References、In-Reply-To、主题兜底和不同账号不串线。
4. API 测试覆盖会话列表计数和会话详情顺序。
5. 前端测试覆盖搜索条件序列化、清除条件和会话模式请求参数。
6. 完整执行后端 unittest、前端测试与构建、Shell 语法、Docker Compose、镜像构建和临时容器持久化验证。
7. 不改动 `/Docker/flymail/data`；临时容器使用独立数据目录。
