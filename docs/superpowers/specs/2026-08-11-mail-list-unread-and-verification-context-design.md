# FlyMail 邮件列表未读降噪与验证码上下文收紧设计

## 1. 背景与目标

当前 `0.0.52` 已把验证码复制操作收敛为始终可见的纯复制图标，并把桌面发件人列收紧到 165px。实际使用截图继续暴露出三个问题：

1. 未读邮件同时存在左侧蓝点、蓝色未读信封、加粗文字和右侧“未读”徽标，状态表达重复。
2. Jira 更新邮件正文里普通出现英文 `verification` 时，现有验证码识别器会把附近 4–8 位数字误认为验证码；真实缓存中已复现把 Jira 工单号尾部数字误判为验证码。
3. 发件人列与主题列虽然已经收窄，但两列之间视觉留白不足，发件人、未读信封和主题看起来挤在一起。

本轮目标是做最小、可验证的修正：

- 删除右侧“未读”徽标，但保留未读蓝点、未读信封和未读字重。
- 采用用户明确选择的方案 B：只收紧验证码上下文判断，不增加 Jira 或通用工单编号黑名单。
- 桌面保持 165px 发件人列，把右侧 padding 从 12px 精确增加到 24px，即增加 12px 的发件人与主题列间留白。
- 保持复制图标位于日期左侧的固定操作轨道，普通 Jira 更新邮件修复误识别后不再出现复制图标。

本轮不改变搜索位置、主题内容、邮件详情、同步、分页、数据库结构或持久化路径。

## 2. 已确认产品决策

### 2.1 未读状态

删除列表行右侧的文字“未读”徽标，包括：

- 单封邮件的“未读”。
- 会话列表当前通过相同 `.mail-status-tag` 显示的 `未读 N`。

保留以下未读提示：

- 行左侧蓝点。
- 主题前蓝色未读信封。
- 未读发件人/主题的现有字重和主文字色。

用户明确要求未读信封继续保留，因此本轮不得删除、隐藏或弱化该图标。

### 2.2 发件人与主题列

桌面最终 `.mail-sender` 继续保持 `165px`，不再继续压窄。

当前 `0.0.52` 最终覆盖样式为：

```css
.mail-sender {
  width: 165px;
  gap: 9px;
  padding-right: 12px;
}
```

本轮把 `padding-right` 精确增加到 `24px`，即相对当前增加 12px 的列间留白。这样不改变发件人列总宽度，只把头像/姓名内部可用空间与主题起点之间的视觉关系重新分配。

设计要求：

- 不加竖线或额外边框，避免重新强化表格感。
- 头像尺寸保持不变。
- 超长发件人继续 ellipsis。
- 769–1180px 现有 150px 响应式宽度保留，并显式使用 `padding-right: 12px`，避免窄桌面继续压缩发件人姓名。
- 768px 以下移动端继续使用 `width: auto` 与 `padding-right: 0`。
- 移动端保持现有两行 grid，不额外加入 12px 桌面列间留白。

## 3. 验证码误识别根因

### 3.1 当前规则

`backend/services/verification_code.py` 的 `_STRONG_POSITIVE_RE` 当前包含：

```text
\bverification(?:\s+code)?\b
```

其中 `(?:\s+code)?` 是可选项，因此英文单词 `verification` 单独出现也会被认为是强验证码上下文。

候选数字只要距离强上下文不超过 `_NEIGHBORHOOD = 80` 个字符，就会得到高分。当前候选规则允许独立的 4–8 位数字。

### 3.2 真实缓存复现

对正式容器真实 Jira 缓存做只读诊断时，主题本身调用 `extract_verification_code(subject=...)` 均返回空；误识别来自已缓存正文。

已复现代表性结果：

```text
[JIRA] IH831-4575 更新 -> 误识别 4575
[JIRA] IH831-4303 更新 -> 误识别 4303
[JIRA] IH831-4590 更新 -> 误识别 4590
```

这些正文都能在候选数字附近命中普通英文 `verification`，因此现有规则把 Jira 工单尾部数字当成验证码。进一步只读检查这些代表性正文，没有命中现有弱上下文 `your code`、`access code`、`code is` 或 `PIN`，因此移除裸 `verification` 的强触发足以切断当前已复现误判路径。

这说明根因不是复制图标、前端渲染或 Jira 主题格式，而是验证码识别器对裸 `verification` 的上下文权重过宽。

## 4. 验证码识别方案 B

用户明确选择方案 B：**只收紧验证码上下文，不为 Jira 工单号或 `ABC-1234` 等编号建立专门黑名单。**

### 4.1 英文强上下文

将当前：

```regex
\bverification(?:\s+code)?\b
```

收紧为必须明确出现“代码”语义，例如：

```regex
\bverification\s+code\b
```

继续保留现有明确的强上下文：

- `OTP`
- `passcode`
- `security code`
- `authentication code`
- `auth code`
- `login code`
- `sign-in code`
- `confirmation code`
- `confirm code`
- `one-time password`
- `one-time code`

裸 `verification`、`verification request`、`verification status`、`verification completed` 等普通验证语义不得单独触发验证码候选。

### 4.2 中文上下文

明确的以下词继续作为强验证码上下文：

- 验证码
- 验证代码
- 校验码
- 动态码
- 动态密码
- 一次性密码
- 安全码
- 认证码
- 登录码
- 确认码

当前已有 `验证(?:自己|您|你)?的?身份` 属于“验证动作”而非“验证码本身”。为了降低类似误报，本轮不能继续仅凭“验证身份”就把附近任意数字识别成验证码。

但之前真实 Atlassian 身份验证正文必须继续支持，因此采用组合上下文：只有“验证身份”附近同时存在明确的代码提示，例如：

- `请输入以下代码`
- `输入以下代码`
- `请输入代码`
- `输入代码`
- `代码如下`

才允许附近 4–8 位数字进入验证码结果。

该组合规则仍属于“上下文收紧”，不依赖 Jira、服务商名称或工单编号格式。

### 4.3 通用弱上下文

现有 `_GENERIC_POSITIVE_RE` 如 `your code`、`code is`、`PIN` 保留，但继续使用当前较低权重和 80 字符距离限制。除非测试证明有新的实际误报，本轮不扩大或重写通用弱上下文。

### 4.4 不做的事情

本轮明确不做：

- 不新增 Jira、Atlassian、`IH831-` 等服务商/工单特例。
- 不新增 `[A-Z]+-\d+` 通用编号黑名单。
- 不改变候选数字长度 4–8 位。
- 不引入机器学习、OCR、第三方识别服务或新依赖。
- 不把验证码持久化到数据库。

## 5. 邮件列表右侧操作轨道

移除 `.mail-status-tag` 后，桌面邮件行右侧从：

```text
主题 … 复制图标 / 未读徽标 / 日期
```

收敛为：

```text
主题 … 复制图标 / 日期
```

设计要求：

- `verification-code-copy` 继续 `flex-shrink: 0`。
- 日期继续固定宽度并保持最右对齐。
- 复制图标继续放在日期左侧。
- 没有验证码时不保留空的复制按钮占位；主题可以使用释放出的空间。
- 去掉未读徽标后不新增替代文字状态列。

移动端继续沿用现有 grid：日期在右上、复制图标在右下。不得为了强行让复制图标“横向位于日期左侧”而破坏现有移动布局。

## 6. 前端实现范围

预计只修改 `frontend/src/views/MailList.vue`：

1. 删除 `.mail-status-tag` 的 `UiBadge` 模板。
2. 删除已经不再使用的 `.mail-status-tag` 页面样式，包括旧断点覆盖。
3. 保留未读信封 SVG 和 `.mail-item.unread` 状态逻辑。
4. 把最终桌面 `.mail-sender` 的 `padding-right` 从 12px 精确提升到 24px；宽度继续 165px。
5. 769–1180px 显式保持 `width: 150px; padding-right: 12px`；768px 以下保持 `width: auto; padding-right: 0`，不引入横向滚动。

不预计修改 `app-shell.css` 或公共组件。

## 7. 后端实现范围

预计只修改：

- `backend/services/verification_code.py`
- `backend/tests/test_verification_code.py`

必要时补充 `backend/tests/test_message_folder_resolution.py` 的集成契约，但不改变 `routes/messages.py`、数据库读取范围或 API schema。

`_decorate_verification_codes()` 的数据流保持：

1. 先检查主题。
2. 主题未命中时读取当前页、当前用户、当前账号、当前文件夹的限定正文片段。
3. 调用同一个 `extract_verification_code()`。
4. 只把结果作为 API 临时字段返回，不写数据库。

## 8. TDD 回归测试

### 8.1 验证码 RED 用例

先增加失败测试，至少覆盖：

```text
普通 Jira 更新正文包含裸 verification + IH831-4575 -> ""
普通 Jira 更新正文包含裸 verification + IH831-4303 -> ""
普通 Jira 更新正文包含裸 verification + IH831-4590 -> ""
Your verification request 123456 is being processed -> ""
Verification completed for request 654321 -> ""
```

同时锁定正确识别：

```text
Your verification code is 654321 -> "654321"
Security code: 778899 -> "778899"
OTP 445566 -> "445566"
验证码：112233 -> "112233"
24681357 是您的验证代码 -> "24681357"
验证自己的身份 + 请输入以下代码 + 24681357 -> "24681357"
```

必须先看到新误识别测试在当前 `0.0.52` 上失败，再修改实现。

### 8.2 前端 RED 用例

更新 `frontend/tests/mail-list-visual-hierarchy.test.mjs` / `mail-verification-code.test.mjs`，锁定：

- `MailList.vue` 不再渲染 `class="mail-status-tag"` 的 `UiBadge`。
- 不再存在可见 `未读 N` / `未读` 状态徽标模板。
- 未读信封 `v-if="!noReadStateFolder && !msg.is_read"` 仍存在。
- `.mail-item.unread::before` 蓝点仍存在。
- `.mail-item.unread .mail-from` / `.mail-subject` 字重仍存在。
- 最终桌面 `.mail-sender` 仍为 `width: 165px; padding-right: 24px`；769–1180px 为 `width: 150px; padding-right: 12px`；移动端仍为 `width: auto; padding-right: 0`。
- 搜索仍在 `toolbar-right`，桌面 `flex-wrap: nowrap`。
- 390px 移动端 `.list-items` 继续 `overflow-x: hidden`。

## 9. 浏览器验收

使用真实 production build + API 拦截的安全测试数据验证：

### 1440×900

- 未读行有左侧蓝点、未读信封、加粗文字。
- 右侧没有“未读”徽标。
- 发件人与主题之间比 `0.0.52` 明显更松，但发件人列计算宽度仍约 165px。
- Jira 普通更新邮件没有复制图标。
- 真正验证码邮件有纯复制图标，位于日期左侧。
- 搜索仍在桌面工具栏右侧同一行。

### 1920×1080

同上，并确认宽屏下列对齐稳定、主题不会因为取消未读徽标产生异常空洞。

### 390×844

- 无“未读”文字徽标。
- 未读信封仍存在。
- 日期与复制图标保持现有两行 grid，不重叠。
- 页面 `scrollWidth <= viewport width`。

### 200% 等价场景

- 工具栏不重叠。
- 发件人、未读信封、主题仍能区分。
- 复制图标和日期仍可访问。
- 无页面级横向溢出。

## 10. README 与版本

行为变化需要同步 README：

- 未读邮件改为“蓝点 + 未读信封 + 字重”表示，不再显示文字未读徽标。
- 验证码识别说明从宽泛的“验证身份上下文”收紧为明确代码语义；普通 `verification` 不再单独触发。
- 复制图标和搜索位置说明保持。

实现完成后补丁版本从 `0.0.52` 升级到 `0.0.53`，由 `VERSION` 作为事实来源，同步：

- `VERSION`
- 根 `package.json`
- `frontend/package.json`
- `docker-compose.yml`
- README 镜像标签

## 11. 数据与安全边界

- 不修改数据库 schema。
- 不迁移、不删除正式邮件或附件。
- 不写入或清理 `/Docker/flymail/data` 中的业务数据。
- 不修改认证、管理员账号、邮箱凭据、OAuth、session secret 或数据库密码。
- 不新增环境变量，因此 `.env.example` 预计无需修改。
- 识别调试和回归测试不得把真实邮件正文写入 Git、日志或测试 fixture；使用最小化、脱敏的代表文本。

## 12. 验证与部署门槛

实现后按项目完整门槛执行：

1. 验证码聚焦后端测试和前端视觉契约测试。
2. 后端完整 unittest。
3. 前端完整 `npm test` + production build。
4. `bash -n scripts/docker-entrypoint.sh`。
5. Docker Compose 结构校验。
6. `git diff --check`。
7. 构建 `benxianyu/flymail:0.0.53`。
8. 独立临时容器验证 health、MySQL 8.0、`/data/mysql/`、数据库读写、重启持久化、日志脱敏、镜像元数据和安全停止。
9. 浏览器验证 1440×900、1920×1080、390×844 和 200% 等价场景。
10. 完整验证后只提交本次任务文件并推送 `origin/main`。
11. 安全替换正式 `flymail` 容器，继续挂载 `/Docker/flymail/data:/data`，升级前后业务计数一致并再次重启确认持久化。
12. 默认不上传 Docker Hub。

## 13. 验收标准

1. 右侧“未读”/`未读 N` 徽标从邮件列表和会话列表移除。
2. 左侧蓝点、蓝色未读信封和未读字重全部保留。
3. 桌面发件人列仍为 165px，`padding-right` 从 12px 增加到 24px，精确增加 12px 视觉留白；769–1180px 继续使用 150px / 12px，移动端使用 auto / 0。
4. 裸 `verification` 不再使附近数字被识别为验证码。
5. 真实 Jira 更新类正文不再把 `4575`、`4303`、`4590` 等工单尾号识别为验证码。
6. `verification code`、`security code`、`OTP`、`passcode`、中文“验证码/验证代码”等明确验证码上下文继续正常识别。
7. Atlassian“验证身份 + 请输入以下代码”的真实场景继续正常识别。
8. 不引入 Jira/工单号黑名单或服务商特例。
9. 真正验证码邮件显示纯复制图标；普通 Jira 更新邮件不显示。
10. 复制图标仍不打开邮件，日期仍在最右固定列。
11. 搜索仍在当前桌面工具栏右侧同一行。
12. 390×844 和 200% 等价场景无页面级横向滚动。
13. 正式 `/Docker/flymail/data` 不发生迁移、删除或数据丢失。
