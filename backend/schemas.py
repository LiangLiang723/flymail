from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class HealthResponse(BaseModel):
    status: str = Field(description="服务状态")
    app: str = Field(description="应用名称")
    version: str = Field(description="版本号")


class UserResponse(BaseModel):
    uid: str = Field(description="飞牛OS 用户ID")
    username: str = Field(description="飞牛OS 用户名")


class AttachmentCacheCleanupResponse(BaseModel):
    before_bytes: int = Field(default=0, ge=0)
    after_bytes: int = Field(default=0, ge=0)
    cleared_references: int = Field(default=0, ge=0)
    evicted_user_objects: int = Field(default=0, ge=0)
    deleted_shared_objects: int = Field(default=0, ge=0)
    freed_physical_bytes: int = Field(default=0, ge=0)


class SettingsResponse(BaseModel):
    uploads_cleanup_weekday: int = Field(default=0, ge=0, le=6, description="Upload cleanup weekday, 0=Monday")
    uploads_cleanup_time: str = Field(default="02:00", description="Upload cleanup time, HH:MM")
    attachment_cache_limit_mb: int = Field(default=2048, ge=0, description="当前用户普通附件缓存上限，0 表示不限制")
    attachment_cache_usage_bytes: int = Field(default=0, ge=0, description="当前用户普通附件逻辑用量")
    attachment_cache_shared_physical_bytes: int = Field(default=0, ge=0, description="全局共享附件对象物理用量")
    gmail_client_id: str = Field(description="Gmail OAuth 客户端ID（完整）")
    gmail_client_secret: str = Field(description="Gmail OAuth 客户端密钥（脱敏，仅显示首尾4位）")
    gmail_redirect_uri: str = Field(description="Gmail OAuth 回调地址")
    has_credentials: bool = Field(description="是否已配置完整的 Gmail 凭据")
    gmail_proxy_enabled: bool = Field(default=False, description="当前用户是否启用 Gmail HTTP 代理")
    gmail_proxy_url: str = Field(default="", description="当前用户的 Gmail HTTP 代理地址")
    outlook_client_id: str = Field(default="", description="Microsoft OAuth 客户端ID（完整）")
    outlook_client_secret: str = Field(default="", description="Microsoft OAuth 客户端密钥（脱敏，仅显示首尾4位）")
    outlook_redirect_uri: str = Field(default="", description="Microsoft OAuth 回调地址")
    has_outlook_credentials: bool = Field(default=False, description="是否已配置完整的 Microsoft 凭据")


class SettingsUpdateResponse(BaseModel):
    success: bool = Field(description="是否保存成功")
    message: str = Field(description="结果消息")
    attachment_cache_cleanup: Optional[AttachmentCacheCleanupResponse] = None


class SettingsUpdateRequest(BaseModel):
    """更新应用设置请求模型，所有字段可选。"""

    uploads_cleanup_weekday: Optional[int] = Field(default=None, ge=0, le=6, description="Upload cleanup weekday, 0=Monday")
    uploads_cleanup_time: Optional[str] = Field(default=None, pattern=r"^\d{2}:\d{2}$", description="Upload cleanup time, HH:MM")
    attachment_cache_limit_mb: Optional[int] = Field(default=None, ge=0, description="普通附件缓存上限 MB，0 表示不限制")
    gmail_client_id: Optional[str] = Field(default=None, max_length=500, description="Gmail OAuth 客户端ID")
    gmail_client_secret: Optional[str] = Field(default=None, max_length=500, description="Gmail OAuth 客户端密钥")
    gmail_redirect_uri: Optional[str] = Field(default=None, max_length=500, description="Gmail OAuth 回调地址")
    gmail_proxy_enabled: Optional[bool] = Field(default=None, description="是否启用 Gmail HTTP 代理")
    gmail_proxy_url: Optional[str] = Field(default=None, max_length=500, description="Gmail HTTP 代理地址")
    outlook_client_id: Optional[str] = Field(default=None, max_length=500, description="Microsoft OAuth 客户端ID")
    outlook_client_secret: Optional[str] = Field(default=None, max_length=500, description="Microsoft OAuth 客户端密钥")
    outlook_redirect_uri: Optional[str] = Field(default=None, max_length=500, description="Microsoft OAuth 回调地址")

    @field_validator("attachment_cache_limit_mb")
    @classmethod
    def validate_attachment_cache_limit_mb(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and 0 < value < 100:
            raise ValueError("非零容量不能低于 100 MB")
        return value


class ProxyTestRequest(BaseModel):
    proxy_url: str = Field(min_length=1, max_length=500, description="HTTP 代理地址")


class ProxyTestResponse(BaseModel):
    success: bool = Field(description="代理是否可用")
    message: str = Field(description="测试结果")
    latency_ms: int = Field(default=0, description="探测耗时毫秒")
    target: str = Field(default="", description="实际探测目标")


class AuthUrlResponse(BaseModel):
    auth_url: str = Field(description="第三方授权页面 URL")
    provider: str = Field(description="邮箱平台类型")


class AuthUrlRequest(BaseModel):
    provider: str = Field(default="gmail", description="邮箱平台类型：gmail / outlook")
    redirect_uri: str = Field(default="", description="OAuth 回调地址")


class AuthCodeAccountRequest(BaseModel):
    email: str = Field(description="邮箱地址")
    auth_code: str = Field(description="邮箱授权码或应用专用密码")
    is_exmail: bool = Field(default=False, description="是否为腾讯企业邮箱")


class CustomAccountRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320, description="邮箱地址")
    username: str = Field(default="", max_length=320, description="登录用户名，空值时使用邮箱地址")
    auth_code: str = Field(min_length=1, max_length=2048, description="授权码或登录密码")
    imap_host: str = Field(min_length=1, max_length=253, description="IMAP 服务器主机")
    imap_port: int = Field(default=993, ge=1, le=65535, description="IMAP 端口")
    imap_ssl: str = Field(default="ssl", pattern=r"^(ssl|starttls)$", description="IMAP 加密方式")
    smtp_host: str = Field(min_length=1, max_length=253, description="SMTP 服务器主机")
    smtp_port: int = Field(default=465, ge=1, le=65535, description="SMTP 端口")
    smtp_ssl: str = Field(default="ssl", pattern=r"^(ssl|starttls)$", description="SMTP 加密方式")
    fetch_history: bool = Field(default=False, description="是否同步历史邮件")


class AccountInfo(BaseModel):
    id: str = Field(description="账号唯一ID")
    email: str = Field(description="邮箱地址")
    provider: str = Field(description="邮箱平台")
    status: str = Field(description="连接状态")
    remark: str = Field(description="备注名")
    group_name: str = Field(description="分组名称")
    hide_email: bool = Field(description="是否隐藏邮箱地址")
    sort_order: int = Field(default=0, description="排序序号")
    poll_interval_seconds: int = Field(default=10, description="新邮件后台轮询间隔（秒）")
    icon_type: str = Field(default="default", description="账号图标模式")
    icon_value: str = Field(default="", description="内置图标 ID")
    icon_url: str = Field(default="", description="上传图标的受保护地址")
    created_at: float = Field(description="创建时间戳")


class AccountListResponse(BaseModel):
    accounts: List[AccountInfo] = Field(description="账号列表")


class AccountAddResponse(BaseModel):
    success: bool = Field(description="是否添加成功")
    account: AccountInfo = Field(description="新创建的账号信息")


class AccountTestResponse(BaseModel):
    success: bool = Field(description="连接是否成功")
    status: str = Field(description="连接状态")
    error: str = Field(default="", description="错误信息（连接失败时）")


class AccountUpdateRequest(BaseModel):
    remark: str = Field(default="", description="备注名")
    group_name: str = Field(default="", description="分组名称")
    hide_email: bool = Field(default=False, description="是否隐藏邮箱地址")
    poll_interval_seconds: int = Field(default=10, ge=5, le=3600, description="新邮件后台轮询间隔（秒）")


class AccountIconPresetRequest(BaseModel):
    preset_id: str = Field(min_length=1, max_length=64, description="内置图标 ID")


class AccountIconResponse(BaseModel):
    success: bool = Field(default=True, description="是否成功")
    icon_type: str = Field(description="账号图标模式")
    icon_value: str = Field(default="", description="内置图标 ID")
    icon_url: str = Field(default="", description="上传图标的受保护地址")


class StatusResponse(BaseModel):
    success: bool = Field(default=True, description="是否成功")


class DeleteResponse(BaseModel):
    success: bool = Field(description="是否删除成功")


class MessageResponse(BaseModel):
    success: bool = Field(description="是否成功")
    message: str = Field(default="", description="结果消息")


class OAuthDiagnosticResponse(BaseModel):
    status: str = Field(description="诊断状态")
    issues: List[str] = Field(description="发现的问题列表")
    runtime: Dict[str, str] = Field(description="运行时 OAuth 配置（已脱敏）")
    stored: Dict[str, str] = Field(description="持久化 OAuth 配置（已脱敏）")
    log_dir: str = Field(description="日志目录")
    tip: str = Field(description="排查建议")


class FolderItem(BaseModel):
    name: str = Field(description="文件夹显示名")
    path: str = Field(description="IMAP 文件夹路径")
    unread_count: int = Field(default=0, description="未读邮件数")
    total_count: int = Field(default=0, description="邮件总数")


class FolderCountItem(BaseModel):
    total: int = Field(description="邮件总数")
    unread: int = Field(description="未读邮件数")


class FolderResponse(BaseModel):
    folders: List[FolderItem] = Field(description="文件夹列表")
    account_id: str = Field(default="", description="账号ID")
    error: str = Field(default="", description="错误信息")
    reconnecting: bool = Field(default=False, description="邮箱连接异常时是否正在重连")


class FolderCountsResponse(BaseModel):
    counts: Dict[str, FolderCountItem] = Field(description="文件夹计数，key 为文件夹路径")
    account_id: str = Field(default="", description="账号ID")
    error: str = Field(default="", description="错误信息")
    reconnecting: bool = Field(default=False, description="邮箱连接异常时是否正在重连")


class AttachmentItem(BaseModel):
    filename: str = Field(default="", description="附件文件名")
    content_type: str = Field(default="", description="MIME 类型")
    size: int = Field(default=0, description="文件大小（字节）")
    part_number: int = Field(default=0, description="IMAP part 编号")
    content_id: str = Field(default="", description="Content-ID")
    is_inline: bool = Field(default=False, description="是否为内嵌附件")


class MessageItem(BaseModel):
    id: str = Field(description="邮件ID")
    uid: int = Field(description="IMAP UID")
    subject: str = Field(default="", description="邮件主题")
    from_addr: str = Field(default="", description="发件人")
    to_addr: str = Field(default="", description="收件人")
    cc: str = Field(default="", description="抄送人")
    date: str = Field(default="", description="邮件日期")
    is_read: bool = Field(default=False, description="是否已读")
    is_starred: bool = Field(default=False, description="是否星标")
    folder: str = Field(default="INBOX", description="文件夹路径")
    body_text: str = Field(default="", description="纯文本正文")
    body_html: str = Field(default="", description="HTML 正文")
    attachments: List[AttachmentItem] = Field(default=[], description="附件列表")
    has_attachments: bool = Field(default=False, description="是否包含附件")
    message_id: str = Field(default="", description="RFC Message-ID")
    account_id: str = Field(default="", description="账号ID")
    account_email: str = Field(default="", description="账号邮箱")
    account_provider: str = Field(default="", description="邮箱平台")


class MessageListResponse(BaseModel):
    messages: List[MessageItem] = Field(description="邮件列表")
    total: int = Field(description="邮件总数")
    unread_total: int = Field(default=0, description="未读邮件总数")
    page: int = Field(description="当前页码")
    page_size: int = Field(description="每页数量")
    account_id: str = Field(default="", description="账号ID")
    error: str = Field(default="", description="错误信息")
    reconnecting: bool = Field(default=False, description="邮箱连接异常时是否正在重连")
    no_accounts: bool = Field(default=False, description="聚合收件箱是否未选择账号")
    filter_counts: dict = Field(default={}, description="筛选计数")


class PrefetchMessagesRequest(BaseModel):
    message_ids: List[str] = Field(default=[], max_length=50, description="需要预取正文的邮件ID列表")
    account_id: str = Field(default="", description="账号ID")
    folder: str = Field(default="INBOX", description="文件夹路径")


class PrefetchMessagesResponse(BaseModel):
    success: bool = Field(default=True, description="是否成功")
    queued: int = Field(default=0, description="已加入后台预取队列的邮件数量")
    prefetched: int = Field(default=0, description="已预取数量")


class MarkReadRequest(BaseModel):
    message_id: str = Field(description="邮件ID")
    folder: str = Field(default="INBOX", description="文件夹路径")
    account_id: str = Field(default="", description="账号ID")


class BatchMarkReadRequest(BaseModel):
    message_ids: List[str] = Field(description="邮件ID列表")
    folder: str = Field(default="INBOX", description="文件夹路径")
    account_id: str = Field(default="", description="账号ID")


class BatchMarkReadResponse(BaseModel):
    success: bool = Field(description="是否成功")
    marked: int = Field(description="成功标记数量")


class MarkAllReadRequest(BaseModel):
    account_ids: List[str] = Field(description="账号ID列表")
    folder: str = Field(default="INBOX", description="文件夹路径")


class MarkAllReadAccountResult(BaseModel):
    account_id: str = Field(description="账号ID")
    email: str = Field(description="邮箱地址")
    marked: int = Field(description="标记数量")


class MarkAllReadResponse(BaseModel):
    success: bool = Field(description="是否成功")
    results: List[MarkAllReadAccountResult] = Field(description="各账号结果")
    total_marked: int = Field(description="总标记数量")


class BatchDeleteRequest(BaseModel):
    message_ids: List[str] = Field(description="邮件ID列表")
    account_id: str = Field(default="", description="账号ID")
    folder: str = Field(default="INBOX", description="文件夹路径")


class BatchDeleteResponse(BaseModel):
    success: bool = Field(description="是否成功")
    deleted: int = Field(description="成功删除数量")


class SendMessageRequest(BaseModel):
    to: str = Field(description="收件人邮箱地址")
    subject: str = Field(description="邮件主题")
    content: str = Field(description="邮件正文")
    html: bool = Field(default=False, description="是否为 HTML 格式")


class SendMessageResponse(BaseModel):
    success: bool = Field(default=True, description="是否成功")
    message: str = Field(description="结果消息")


class ComposeMessageRequest(BaseModel):
    account_id: str = Field(default="", description="发件账号ID")
    to: list[str] = Field(default=[], max_length=50, description="收件人列表")
    cc: list[str] = Field(default=[], max_length=50, description="抄送列表")
    bcc: list[str] = Field(default=[], max_length=50, description="密送列表")
    subject: str = Field(default="", max_length=500, description="邮件主题")
    body_html: str = Field(default="", description="HTML 格式正文")
    attachments: list[str] = Field(default=[], max_length=20, description="附件文件路径列表")
    action: str = Field(default="send", description="操作类型")
    schedule_time: str | None = Field(default=None, description="ISO8601 定时发送时间")
    in_reply_to: str | None = Field(default=None, description="回复的邮件 Message-ID")
    forward_from: str | None = Field(default=None, description="转发的邮件 Message-ID")
    draft_message_id: str | None = Field(default=None, description="正在编辑的草稿邮件ID")
    draft_folder: str | None = Field(default=None, description="正在编辑的草稿所在文件夹")


class ComposeMessageResponse(BaseModel):
    success: bool = Field(default=True, description="是否成功")
    message: str = Field(description="结果消息")
    job_id: str = Field(default="", description="定时发送任务ID")
    sent_folder: str = Field(default="", description="已发送文件夹路径")
    draft_message_id: str = Field(default="", description="保存后的草稿邮件ID")
    draft_folder: str = Field(default="", description="保存后的草稿文件夹路径")


class UploadAttachmentResponse(BaseModel):
    filename: str = Field(description="原始文件名")
    size: int = Field(description="文件大小（字节）")
    path: str = Field(description="服务端临时附件路径")
    source: str = Field(default="local", description="来源：local 或 nas")


class RegisterNasAttachmentRequest(BaseModel):
    path: str = Field(description="授权目录内文件路径")


class SaveAttachmentToNasRequest(BaseModel):
    account_id: str = Field(default="", description="账号ID")
    folder: str = Field(default="INBOX", description="邮件文件夹")
    target_dir: str = Field(description="目标授权目录")
    filename: str = Field(default="", description="保存文件名")


class SaveAttachmentToNasResponse(BaseModel):
    success: bool = Field(default=True, description="是否成功")
    path: str = Field(description="保存路径")
    filename: str = Field(description="文件名")
    size: int = Field(description="文件大小")


class SignatureSettingsRequest(BaseModel):
    signature_html: str = Field(default="", description="签名 HTML 内容")
    signature_enabled: bool = Field(default=False, description="是否启用签名")


class SignatureSettingsResponse(BaseModel):
    signature_html: str = Field(default="", description="签名 HTML 内容")
    signature_enabled: int = Field(default=0, description="是否启用签名")


class SignatureTemplateRequest(BaseModel):
    name: str = Field(default="", description="签名模板名称")
    content_html: str = Field(default="", description="签名 HTML 内容")
    is_default: bool = Field(default=False, description="是否默认签名")
    account_id: str = Field(default="", description="关联账号ID")


class SignatureTemplateUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, description="签名模板名称")
    content_html: Optional[str] = Field(default=None, description="签名 HTML 内容")
    is_default: Optional[bool] = Field(default=None, description="是否默认签名")
    account_id: Optional[str] = Field(default=None, description="关联账号ID")


class SignatureTemplateItem(BaseModel):
    id: int = Field(description="签名模板ID")
    name: str = Field(description="签名模板名称")
    content_html: str = Field(default="", description="签名 HTML 内容")
    is_default: bool = Field(default=False, description="是否默认签名")
    account_id: str = Field(default="", description="关联账号ID")


class SignatureListResponse(BaseModel):
    signatures: List[SignatureTemplateItem] = Field(description="签名模板列表")


class UnifiedSettingsRequest(BaseModel):
    account_ids: List[str] = Field(default=[], max_length=100, description="参与聚合的账号ID")


class UnifiedSettingsAccount(BaseModel):
    id: str
    email: str
    provider: str
    selected: bool


class UnifiedSettingsResponse(BaseModel):
    account_ids: List[str]
    accounts: List[UnifiedSettingsAccount]


class ScheduledMessagesResponse(BaseModel):
    jobs: List[Dict[str, Any]] = Field(description="待执行的定时发送任务列表")


class NotificationItem(BaseModel):
    id: str = Field(description="通知ID")
    account_id: str = Field(description="账号ID")
    provider: str = Field(description="邮箱平台")
    email: str = Field(description="邮箱地址")
    folder: str = Field(description="文件夹")
    is_read: bool = Field(description="是否已读")
    time: float = Field(description="通知时间")
    type: str = Field(default="new_mail", description="通知类型")
    message: str = Field(default="", description="通知描述文本")
    message_cache_id: str = Field(default="", description="缓存邮件ID")
    message_uid: int = Field(default=0, description="IMAP UID")
    rfc_message_id: str = Field(default="", description="RFC Message-ID")
    subject: str = Field(default="", description="邮件主题")
    from_addr: str = Field(default="", description="发件人")
    to_addr: str = Field(default="", description="收件人")
    cc: str = Field(default="", description="抄送")
    mail_date: str = Field(default="", description="邮件日期")
    body_preview: str = Field(default="", description="正文摘要")
    has_attachments: bool = Field(default=False, description="是否有附件")
    batch_count: int = Field(default=1, description="批量数量")


class NotificationListResponse(BaseModel):
    notifications: List[NotificationItem] = Field(description="通知列表")


class NotificationReadResponse(BaseModel):
    success: bool = Field(description="是否成功")


class NotificationReadAllResponse(BaseModel):
    success: bool = Field(description="是否成功")
    updated: int = Field(description="更新的通知数量")


class NotificationClearResponse(BaseModel):
    success: bool = Field(description="是否成功")
    deleted: int = Field(description="删除的通知数量")


class ErrorResponse(BaseModel):
    error: str = Field(description="错误信息")


class ContactEmailItem(BaseModel):
    id: int = Field(default=0)
    email: str
    is_primary: bool = Field(default=False)


class ContactItem(BaseModel):
    id: int
    name: str = Field(default="")
    emails: List[ContactEmailItem] = Field(default=[])
    phone: str = Field(default="")
    company: str = Field(default="")
    remark: str = Field(default="")
    group_name: str = Field(default="")


class ContactListResponse(BaseModel):
    contacts: List[ContactItem]


class ContactSearchResponse(BaseModel):
    results: List[ContactItem]


class ContactCreateRequest(BaseModel):
    name: str = Field(default="", max_length=255)
    emails: List[str] = Field(default=[], max_length=20)
    phone: str = Field(default="", max_length=128)
    company: str = Field(default="", max_length=255)
    remark: str = Field(default="", max_length=2000)
    group_name: str = Field(default="", max_length=255)


class ContactUpdateRequest(ContactCreateRequest):
    id: int = Field(default=0)


class QuickAddContactRequest(BaseModel):
    name: str = Field(default="", max_length=255)
    email: str = Field(min_length=3, max_length=320)


class ContactStatsResponse(BaseModel):
    count: int = Field(default=0)
    last_date: str = Field(default="")
