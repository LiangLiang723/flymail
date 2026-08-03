# FlyMail V2 真实服务商与多端浏览器验收结果

## 执行信息

- 日期：2026-08-02
- 基线提交：`02b68c2`
- 运行环境：Ubuntu 24.04 DevSpace
- 结果规则：真实外部环境未执行时标记为 `blocked`，自动化合同仅标记为 `contract-pass`
- 生产数据目录：`/Docker/flymail/data` 未触碰
- Docker Hub：未上传

## 可用资源检查

本次环境未发现 Gmail、Google、Outlook、Microsoft、QQ、网易、iCloud、新浪、通用 IMAP/SMTP、HTTP CONNECT 代理、Bark、Telegram、企业微信、钉钉、飞书、Webhook 或图床测试凭据的环境变量。

宿主机未发现 Chrome、Chromium、Edge、Firefox、Safari、WebKit、Playwright 或移动设备浏览器运行时；仅存在 Node.js 的 `npx` 命令。没有远程浏览器服务或真实移动设备连接。

因此不能安全执行真实授权、收取、发送、通知投递或人工浏览器验收，相关项均为 `blocked`，不能据此声称真实服务商已经可用。

## 自动化证据

### Provider、协议与代理合同

执行：

```bash
python -m unittest -v \
  tests.v2.test_provider_contracts \
  tests.v2.test_protocol_worker_integration \
  tests.test_gmail_proxy \
  tests.test_gmail_proxy_settings
```

结果：`79/79` 通过。

覆盖内容：

- Gmail、Outlook、QQ、网易、iCloud、新浪和 Generic 插件键、端点、能力与特殊邮箱合同。
- Provider 错误分类与递归秘密脱敏。
- 确定性假 Provider 的摘要、正文、CID、附件、邮件操作、可靠发送、已发送副本和通知纵向流程。
- 慢账号和限流账号不阻塞其他账号。
- Worker 重启后租约释放与任务恢复。
- Gmail OAuth、刷新、IMAP、SMTP、IDLE 和通知代理配置传播。
- 代理认证信息不写入错误日志。

### 前端 PWA 与无障碍静态合同

执行：

```bash
node --test \
  tests/v2/accessibility-performance.test.ts \
  tests/v2/personal-notifications-pwa.test.ts
```

结果：`10/10` 通过。

覆盖内容：键盘快捷键保护、焦点恢复栈、可见错误和触控目标、主题与减少动画、异步页面拆分、头像裁剪、OAuth 回调状态、联系人自动完成、通知配置秘密显示规则，以及 PWA 只缓存静态资源。

这些测试不等于真实浏览器或屏幕阅读器验收。

## 服务商结果

| 服务商 | 合同 | 真实账号 | 结果 | 说明 |
|---|---|---|---|---|
| Gmail | `contract-pass` | 无 | `blocked` | 无隔离 Google 账号、OAuth 客户端和代理凭据 |
| Outlook / Microsoft | `contract-pass` | 无 | `blocked` | 无隔离 Microsoft 账号和 OAuth 客户端 |
| QQ 邮箱 | `contract-pass` | 无 | `blocked` | 无隔离账号和授权码 |
| 网易邮箱 | `contract-pass` | 无 | `blocked` | 无 163/126/188/yeah.net 隔离账号和授权码 |
| iCloud 邮箱 | `contract-pass` | 无 | `blocked` | 无隔离 Apple ID 和应用专用密码 |
| 新浪邮箱 | `contract-pass` | 无 | `blocked` | 无隔离账号和授权码 |
| Generic IMAP/SMTP | `contract-pass` | 无 | `blocked` | 无受控通用 IMAP/SMTP 服务器和账号 |

## 代理、通知与图床结果

| 项目 | 自动化合同 | 真实端点 | 结果 |
|---|---|---|---|
| HTTP CONNECT 代理 | `contract-pass` | 无 | `blocked` |
| Bark | 假传输覆盖 | 无 | `blocked` |
| Telegram | 假传输覆盖 | 无 | `blocked` |
| 企业微信 | 假传输覆盖 | 无 | `blocked` |
| 钉钉 | 假传输覆盖 | 无 | `blocked` |
| 飞书 | 假传输覆盖 | 无 | `blocked` |
| 通用 Webhook | 假传输覆盖 | 无 | `blocked` |
| `flymail-imgbed` / 通用 HTTPS 图床 | 假发布器覆盖 | 无 | `blocked` |

## 浏览器与无障碍结果

| 平台 | 运行时 | 结果 |
|---|---|---|
| 桌面 Chrome | 不存在 | `blocked` |
| 桌面 Edge | 不存在 | `blocked` |
| 桌面 Firefox | 不存在 | `blocked` |
| 桌面 Safari | Linux 环境不可用 | `blocked` |
| iOS Safari | 无真实设备或远程设备 | `blocked` |
| Android Chrome | 无真实设备或模拟器 | `blocked` |
| 桌面屏幕阅读器 | 不存在 | `blocked` |
| 键盘/焦点/减少动画静态合同 | Node 测试 | `contract-pass` |

## 发布阻断

正式 Worker 已改为装配完整 17 类任务处理器，并已接通 V2 原生 PKCE OAuth、用户级 HTTP CONNECT、IMAP/SMTP TLS、摘要与 BODYSTRUCTURE 摄取、精确 MIME 抓取、邮件操作、可靠发送、Sent 查验/追加副本和周期轮询。新增运行时与既有 Provider/代理合同组合回归累计 `100/100` 通过，空 Dispatcher 阻断已解除。

仍不能据此声称真实服务商已经通过：

1. 本次没有任何隔离邮箱账号、OAuth 客户端、测试代理或通知端点，所有真实外部行仍为 `blocked`。
2. 生产 IDLE 长连接尚未装配；当前持续同步依赖每账号可配置的服务器端轮询。
3. 必须提供隔离服务商账号、OAuth 客户端、测试代理、通知端点和真实浏览器/设备后重新执行本手册。
4. 在真实外部验收和生产切换的单独破坏性确认完成前，生产容器和 `/Docker/flymail/data` 必须保持不变。
