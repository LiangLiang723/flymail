# 签名图片随邮件内嵌发送设计

## 目标

签名图片属于 FlyMail 签名资产。用户上传或直接粘贴图片后，签名保存内部图片标识；真正发送邮件时，图片二进制作为 MIME inline part 随邮件一起发送，HTML 只引用本封邮件内的 `cid:`，收件人不需要访问 FlyMail、原邮件附件或任何外部图片地址。

## 已确认现状与根因

当前 `/api/signatures/images` 会把图片规范化后保存到 `/data/flymail/files/signature-images/`，但接口只返回公开 URL，Tiptap 将该 URL 原样写入签名 HTML。`/api/messages/compose`、定时发送和各 provider sender 又把 HTML 原样放入 `text/html` MIME part，没有为签名图片创建 `Content-ID` 和 inline MIME part。因此收件客户端必须联网访问 FlyMail 图片 URL。

编辑器目前也没有专门处理粘贴图片；文件选择会上传，但剪贴板图片可能沿用浏览器提供的来源表示，无法保证成为签名资产。

## 方案比较

### 方案 A：Base64 `data:` 直接写进签名 HTML

优点是实现路径短，不需要 SMTP sender 增加 inline part。缺点是图片会把签名 JSON 和数据库 HTML 放大数十到数百 KB，突破当前 10000 字符限制，并且 Gmail、Outlook 等客户端对 `data:` 图片兼容性和清洗策略不稳定。放弃。

### 方案 B：继续使用 FlyMail 公共图片 URL

这是当前实现，编辑简单，但收件人依赖 FlyMail 地址长期可访问，也会暴露加载请求。与本次明确需求相反。放弃。

### 方案 C：内部资产 ID + MIME CID 内嵌图片

采用此方案。签名 HTML 持久化 `flymail-signature-image:<image_id>` 和 `data-flymail-signature-image="<image_id>"`，不持久化收件人可见的外部 URL。编辑器 NodeView 只在浏览器显示时将内部 ID 映射为 FlyMail 预览地址。发送前后端验证图片归属、读取持久化文件、生成本封邮件唯一 CID，将 HTML `src` 改成 `cid:<content-id>` 并把图片二进制放入 `multipart/related`。

## 数据表示

图片文件继续保存在现有用户隔离目录：

`/data/flymail/files/signature-images/<user-bucket>/<random>.webp`

签名 HTML 中的管理图片使用：

```html
<img
  src="flymail-signature-image:<image_id>"
  data-flymail-signature-image="<image_id>"
  width="367"
>
```

`image_id` 已包含用户哈希 bucket 和随机文件名。后端在读取或发送时必须验证 bucket 与当前 `user_uid` 一致，不能跨用户读取。

0.0.38 已存在的 `/api/signature-images/<image_id>` URL 和更早的 `/api/messages/.../attachments/...` 签名图片会在安全确认归属和本地文件存在后转换为上述内部表示。原邮件、附件缓存和签名图片文件不删除。

## 编辑器行为

图片按钮继续支持 JPG、PNG、WebP，服务端仍规范化为 WebP。上传响应新增 `image_id`，保留现有 `url` 字段用于兼容。Tiptap 插入节点时使用内部 scheme 与 `data-flymail-signature-image`，NodeView 使用当前应用 base path 生成预览 URL，`editor.getHTML()` 仍输出内部 ID，不输出预览 URL。

粘贴事件优先检查 `clipboardData.items` 中的图片文件。发现支持的图片文件后阻止浏览器默认插入，调用同一上传逻辑并在原光标位置插入内部图片节点。因此截图、系统剪贴板图片、浏览器“复制图片”等提供图片文件的粘贴都会成为 FlyMail 签名资产，不保留来源 URL。普通文本/HTML 粘贴保持原行为。

图片拖拽缩放和 `25% / 50% / 75% / 100%` 快捷尺寸继续写入 `width`，不改变现有交互。

## 发送前解析

新增独立服务负责将编辑 HTML 准备成邮件 HTML：

1. 扫描 `<img>`。
2. 识别内部 scheme、`data-flymail-signature-image`、0.0.38 的 FlyMail 签名图片 URL。
3. 验证图片属于当前用户且文件存在。
4. 相同 image_id 在同一封邮件中只读取和附加一次。
5. 生成唯一 `Content-ID`，将 HTML `src` 改为 `cid:<content-id>`，移除 FlyMail 内部 data 属性。
6. 将 WebP 字节、content type、文件名与 content id 作为 inline image 数据返回。
7. 对从 IMAP 草稿重新打开后形成的 `data:image/...;base64,...` 图片也转换为 inline image，保证草稿再次发送不会退回 data URI。

已识别为 FlyMail 管理图片但文件缺失或归属错误时，不降级成外链发送，而是明确拒绝发送/保存草稿并提示重新插入图片。普通非 FlyMail 外部图片不在本次自动抓取范围内，避免服务器端 URL 抓取带来的 SSRF 风险。

## MIME 结构

所有 SMTP provider 与本地“已发送”回写统一采用：

```text
multipart/mixed
└─ multipart/alternative
   ├─ text/plain
   └─ multipart/related
      ├─ text/html  (src="cid:...")
      └─ image/webp
         Content-ID: <...>
         Content-Disposition: inline
└─ 普通附件（如有）
```

没有 inline image 时保持当前 MIME 结构和行为。

为避免 7 个 sender 各自实现一套 CID 逻辑，抽取标准库 MIME 构建 helper；Gmail、Outlook、QQ、网易、iCloud、新浪、自定义 SMTP 都调用同一 helper，只保留各自连接认证与 `sendmail`。

## 各发送路径

- **立即发送**：compose route 在调用 provider 前解析 inline images，并把结果交给 sender。
- **定时发送**：调度任务保存内部 HTML，不保存大图片字节；任务实际执行时重新按 `user_uid` 解析图片并组装 CID。
- **保存草稿**：IMAP APPEND 时同样写成 multipart/related，草稿在其他客户端打开也能看到签名图片。
- **再次编辑草稿**：如果接收解析将 CID 转成 data URI，发送准备服务会再次转换为 inline MIME。
- **已发送回写**：`build_outgoing_message_bytes` 使用相同 inline MIME helper，确保 fallback APPEND 的已发送邮件与真正发出的邮件结构一致。
- **本地已发送 fallback 缓存**：CID 图片转换成 data URI 仅供 FlyMail 本地阅读，不要求外部地址。

## 安全与数据边界

- 不新增生产依赖。
- 不改变 MySQL 表结构。
- 不删除已有签名图片、邮件、附件或缓存对象。
- 只允许当前用户 bucket 的签名图片参与发送。
- 图片仍受单张 5 MB 上传限制和现有 Pillow 解码/规范化限制。
- 不从服务器主动抓取任意外部图片 URL，避免 SSRF。
- 日志不记录图片二进制、完整正文、密码、token 或会话密钥。

## 验收条件

- 新上传签名图片保存后的 HTML 不包含 `http://`/`https://` FlyMail 图片 URL，而包含内部 image id。
- 直接粘贴剪贴板图片会上传并保存为签名资产。
- 立即发送邮件的 raw MIME 包含 `Content-ID`、`Content-Disposition: inline` 和图片字节，HTML 使用 `cid:`，不包含 `/api/signature-images/`。
- Gmail、Outlook、QQ、网易、iCloud、新浪和自定义 SMTP 均走统一 inline image MIME helper。
- 定时发送、保存草稿、草稿再次发送、已发送 APPEND 均保留 inline image。
- 普通附件仍是 attachment，不会把签名图片显示为普通附件。
- 图片宽度在编辑、保存和发送 HTML 中保持。
- 0.0.38 现有稳定 URL 签名自动兼容为内部 ID，现有文件不丢失。
- 完整后端测试、前端测试/build、Shell、Compose、Docker 镜像、隔离容器真实 SMTP MIME 检查和生产容器重启验证通过。
- 目标发布版本：`0.0.39`。
