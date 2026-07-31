from pydantic import BaseModel


class User(BaseModel):
    id: str
    username: str
    nickname: str | None = ""
    avatar_path: str | None = ""
    password_hash: str
    role: str = "user"
    status: str = "active"
    created_at: float = 0.0
    updated_at: float = 0.0


class Account(BaseModel):
    model_config = {"validate_assignment": True}

    id: str
    user_uid: str
    email: str
    provider: str
    credentials_json: str = ""
    status: str = "disconnected"
    remark: str = ""
    group_name: str = ""
    hide_email: bool = False
    sort_order: int = 0
    poll_interval_seconds: int = 10
    icon_type: str = "default"
    icon_value: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0


class CachedMessage(BaseModel):
    id: str
    account_id: str
    user_uid: str
    uid: int
    folder: str
    subject: str
    from_addr: str
    to_addr: str
    cc: str = ""
    date: str
    is_read: bool = False
    is_starred: bool = False
    has_attachments: bool = False
    body_text: str = ""
    body_html: str = ""
    message_id: str = ""
    body_checked: bool = False
    storage_path: str = ""
    cached_at: float = 0.0


class CachedAttachment(BaseModel):
    account_id: str
    user_uid: str
    uid: int
    folder: str
    part_number: int
    filename: str = ""
    content_type: str = ""
    size: int = 0
    content_id: str = ""
    is_inline: bool = False
    local_path: str = ""
    content_sha256: str = ""
    last_accessed_at: float = 0.0
    cached_at: float = 0.0


class Notification(BaseModel):
    id: str
    user_uid: str
    account_id: str
    provider: str
    email: str
    folder: str
    is_read: bool = False
    created_at: float = 0.0
    type: str = "new_mail"
    message: str = ""
    message_cache_id: str = ""
    message_uid: int = 0
    rfc_message_id: str = ""
    subject: str = ""
    from_addr: str = ""
    to_addr: str = ""
    cc: str = ""
    mail_date: str = ""
    body_preview: str = ""
    has_attachments: bool = False
    batch_count: int = 1
    extra_json: str = ""


class ContactEmail(BaseModel):
    id: int = 0
    contact_id: int = 0
    email: str = ""
    is_primary: bool = False


class Contact(BaseModel):
    id: int = 0
    user_uid: str = ""
    name: str = ""
    emails: list[ContactEmail] = []
    phone: str = ""
    company: str = ""
    remark: str = ""
    group_name: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0


class Signature(BaseModel):
    id: int = 0
    name: str = ""
    content_html: str = ""
    is_default: int = 0
    account_id: str = ""
    user_uid: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
