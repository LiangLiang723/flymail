# FlyMail Apple 风格统一 UI 与认证体验设计

## 目标

在不改变邮件业务能力、用户数据模型和 `/data` 持久化结构的前提下，重构登录启动体验、主应用壳、侧栏动效和前端视觉系统，让 FlyMail 在桌面与移动端具备统一、可复用、可持续扩展的设计规范。

## 用户问题

1. 登录失败时没有任何可见提示，用户无法判断用户名、密码或网络是否有问题。
2. 刷新页面时会短暂显示登录界面，再切回主应用，造成错误的退出感。
3. 侧栏展开和折叠时图标位置变化，动画不连续，交互不稳定。
4. 深色主题存在大量背景、按钮、边框和文本颜色错误。
5. 页面各自定义按钮、输入框、卡片和状态样式，缺少可复用的设计系统。

## 已确认范围

- 同一个用户允许多个浏览器和设备同时保持登录；现有无状态签名 Cookie 已满足该行为，不引入单会话或踢下线机制。
- 可以完整重构用户可见 UI，但保留 FastAPI 后端、Vue 3 业务逻辑和现有邮件功能。
- 主题色、布局、组件外观和动画可重新设计。
- 不修改认证方式、数据库结构、邮箱凭据存储和 `/Docker/flymail/data`。
- 不新增生产依赖；动画优先使用 CSS 的 transform、opacity 和已有 Vue 能力实现。

## 设计原则

### 1. 直接反馈

- 按钮在按下时立即给出视觉反馈。
- 提交、刷新、保存、同步等异步操作必须显示加载状态。
- 错误信息靠近发生位置，不依赖无提示的失败。

### 2. 空间稳定

- 展开和折叠时，侧栏图标轨道坐标保持不变。
- 弹出菜单从触发控件方向出现，关闭时沿同一路径返回。
- 页面切换不改变公共导航和账户入口的位置。

### 3. 克制的层次

- 使用背景层、表面层和浮动层三种主要深度，不叠加过多阴影。
- 颜色主要用于选择、状态和重要操作，不把每个按钮都做成高饱和色块。
- 深浅主题使用同一套语义 token，而不是分别为页面硬编码颜色。

### 4. 可复用

- 新页面优先使用设计 token 和公共组件。
- 组件外观由 variant、size 和 state 决定，不允许页面复制按钮基础样式。
- 兼容现有 class 的过渡层只用于迁移，最终业务页面不再定义重复的按钮和表单基础样式。

### 5. 可访问

- 键盘焦点必须清晰可见。
- 控件最小点击区域桌面 36px，移动端 44px。
- 支持 `prefers-reduced-motion`、`prefers-reduced-transparency` 和高对比度偏好。
- 错误不能只靠颜色表达，必须同时有文本或图标。

## 信息架构与应用壳

### 认证状态

应用启动采用明确三态：

```text
booting -> authenticated
        -> anonymous
```

- `booting`：只显示与主题一致的启动界面，不渲染登录表单。
- `authenticated`：显示主应用壳。
- `anonymous`：只有在 `/api/auth/me` 明确失败后显示登录页。

网络错误与未登录分开处理：会话检查出现临时网络故障时显示重试状态，不误判为退出登录。

### 组件边界

```text
App.vue
├── AuthGate
│   ├── AppBootScreen
│   └── LoginView
└── AppShell
    ├── AppSidebar
    ├── MobileNavigationDrawer
    ├── UserMenu
    ├── NotificationDrawer
    └── PageViewport
```

- `App.vue`：只负责根状态和全局覆盖层。
- `AuthGate.vue`：管理 booting/authenticated/anonymous/error。
- `AppBootScreen.vue`：启动状态与重试入口。
- `AppShell.vue`：桌面和移动布局。
- `AppSidebar.vue`：导航、通知、账户入口和折叠控制。
- `UserMenu.vue`、`NotificationDrawer.vue`：独立浮层，避免继续膨胀 `App.vue`。

## 登录体验

### 错误映射

- 401：`用户名或密码错误`
- 403：`此账号已被禁用，请联系管理员`
- 超时或无响应：`暂时无法连接 FlyMail，请稍后重试`
- 其他服务错误：优先展示后端安全文案，缺失时显示通用错误

### 表单行为

- 错误显示在表单顶部和对应输入区域附近。
- 重新输入用户名或密码时清除旧的凭据错误。
- 登录按钮提交期间禁用并显示加载指示。
- 保留 Enter 提交和浏览器密码管理器兼容。
- 不区分“用户名不存在”和“密码错误”，避免账号枚举。

## 侧栏模型

### 桌面结构

- 展开宽度：248px。
- 折叠宽度：72px。
- 固定 72px 图标轨道，文本区域从第 73px 开始。
- Logo、导航图标、通知图标和头像在两种状态下保持相同 x/y 坐标。
- 折叠只改变外层宽度和文本区域的 `opacity/transform/clip`，不切换布局方向，不重新居中图标。
- 侧栏宽度使用统一的 240ms `cubic-bezier(0.2, 0.8, 0.2, 1)`；文字较快淡出，较慢淡入，减少裁切感。
- 动画期间不锁定点击，允许用户立即反向操作。

### 移动端

- 960px 及以下使用左侧抽屉。
- 抽屉包含主导航；邮件管理页继续包含邮箱账号和文件夹。
- 触发器固定在页面安全区域内，不占用永久顶栏。
- 抽屉打开使用遮罩和轻量背景模糊；减少动态效果时改为短淡入淡出。

## 视觉系统

### 色彩角色

采用低饱和蓝紫作为品牌强调色，语义颜色保持独立：

- Accent：选择、主要操作、焦点。
- Success：成功和在线。
- Warning：需注意但可继续。
- Danger：删除、失败、不可逆操作。
- Neutral：背景、表面、边框和文本层级。

不在业务页面直接写十六进制颜色，统一使用语义 token：

```text
--ui-canvas
--ui-surface-1
--ui-surface-2
--ui-surface-floating
--ui-fill-muted
--ui-fill-hover
--ui-fill-selected
--ui-text-1
--ui-text-2
--ui-text-3
--ui-border
--ui-border-strong
--ui-accent
--ui-accent-hover
--ui-success
--ui-warning
--ui-danger
--ui-focus-ring
```

深浅主题只替换 token 值。现有变量保留为别名，逐页迁移后移除页面硬编码。

### 字体

- 使用系统字体栈和 `font-optical-sizing: auto`。
- 页面标题 22px/700，紧凑行高和轻微负字距。
- 区块标题 16px/650。
- 正文 14px/400。
- 辅助文字 12px/400，保持足够对比度。
- 数字和代码信息可使用现有等宽字体 token。

### 间距与尺寸

使用 4px 基础栅格：4、8、12、16、20、24、32、40、48。

- 小控件：32px。
- 常规控件：40px。
- 移动主控件：44px。
- 小圆角：8px。
- 常规圆角：12px。
- 卡片圆角：16px。
- 浮层圆角：18px。

### 深度

- 页面画布无阴影。
- 卡片使用边框加极轻阴影。
- Popover、Drawer、Dialog 使用浮动表面和较深阴影。
- 透明材质只用于侧栏、浮层或移动抽屉，不在同一区域堆叠多层玻璃效果。

## 公共组件

### UiButton

Variants：`primary`、`secondary`、`ghost`、`danger`。
Sizes：`sm`、`md`、`lg`、`icon`。
States：默认、hover、pressed、focus-visible、disabled、loading。

### UiIconButton

用于纯图标操作，必须传入 `aria-label` 和可选 tooltip。图标轨道和圆角统一。

### UiField / UiInput / UiSelect / UiTextarea

统一标签、帮助文字、错误文字、焦点环、禁用状态和深色背景。输入错误时同时展示图标/文本和边框状态。

### UiCard / UiSection

统一页面区块的背景、边框、圆角、标题和操作区，不允许各页面重复定义 card shell。

### UiBadge / UiStatus

区分中性数量、成功、警告、错误和信息状态。

### UiModal / UiDrawer / UiPopover

统一遮罩、层级、焦点样式、进入和退出路径。保持现有业务弹窗行为，不新增复杂焦点管理依赖。

### UiEmptyState / UiSkeleton / UiSpinner

统一空状态、启动状态和异步加载反馈，避免页面用纯文本或整页闪烁。

## 页面迁移策略

### 第一批：应用壳与认证

- LoginView
- App 启动状态
- Sidebar、UserMenu、NotificationDrawer

### 第二批：高频页面

- MailList
- UnifiedInbox
- ComposeEmail
- AccountList

### 第三批：管理与设置

- Settings
- NotificationSettings
- HistorySync
- Backup
- ContactList
- UserManagement
- About
- TiptapEditor
- NasPathPicker

每一批迁移后运行前端测试、类型检查和构建。最终扫描硬编码颜色和重复基础控件 class，确保显著收敛。

## 动画规范

- 默认无弹跳，强调连续、可逆和可打断。
- 按压反馈：80–100ms，`scale(0.98)`。
- hover/focus 颜色：120–160ms。
- 侧栏宽度：240ms，强调平稳而非炫技。
- Drawer/Popover：180–240ms，沿触发来源进入和退出。
- Toast：短位移加淡入；减少动态效果时只淡入淡出。
- 只动画 `transform`、`opacity` 和必要的容器宽度；避免同时动画大量阴影和布局属性。

## 错误处理

- `/auth/me` 的 401/403 才进入 anonymous。
- 网络异常保留 boot error 状态并允许重试。
- 登录表单捕获 Axios 归一化错误并映射为用户文案。
- 公共按钮加载状态禁止重复提交。
- 主题 token 缺失时由根层默认值兜底，不允许产生透明文字或不可读按钮。

## 测试与验收

### 自动化

- 登录失败文案映射测试。
- 启动期间不渲染 LoginView 的结构测试。
- 多独立 Cookie 会话同时有效的后端测试。
- 侧栏展开/折叠图标轨道固定的源码/CSS 回归测试。
- 公共组件 variant 和 loading/disabled 行为测试。
- 主题 token 完整性和硬编码颜色扫描测试。
- 现有前端测试、`vue-tsc`、Vite build 和 98 项后端测试全部通过。

### 浏览器可见验收

- 刷新已登录页面时不会闪登录表单。
- 错误密码显示明确提示并可重新提交。
- 侧栏反复快速切换时图标位置不跳动。
- 浅色、深色和系统主题下主要页面文字、按钮、输入框和浮层均可读。
- 桌面 1366px/1920px 和移动窄屏均无横向溢出。
- `prefers-reduced-motion` 下不播放大幅滑动动画。

## 部署与数据影响

- 需要构建新镜像并替换当前 `flymail` 容器。
- 不修改数据库表、不迁移邮件和附件。
- `/Docker/flymail/data` 继续挂载到 `/data`，升级和重启后数据必须保持。
- Docker Hub 默认不上传。
