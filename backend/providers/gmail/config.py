import os
from typing import Any, Dict

# Gmail OAuth2 配置
# 需要在 Google Cloud Console 创建 OAuth2 凭据
# https://console.cloud.google.com/apis/credentials
GMAIL_CLIENT_ID = os.environ.get("GMAIL_CLIENT_ID", "")
GMAIL_CLIENT_SECRET = os.environ.get("GMAIL_CLIENT_SECRET", "")
# 默认为空，由前端动态传入或从 settings.json 加载
# 不再硬编码 localhost，避免在飞牛网关环境中使用错误的回调地址
GMAIL_REDIRECT_URI = os.environ.get("GMAIL_REDIRECT_URI", "")

GMAIL_IMAP_HOST = "imap.gmail.com"
GMAIL_IMAP_PORT = 993
GMAIL_SMTP_HOST = "smtp.gmail.com"
GMAIL_SMTP_PORT = 587



def proxy_url_from_extra(extra: Dict[str, Any] | None) -> str:
    """Return the per-account Gmail HTTP proxy when explicitly enabled."""
    if not extra or not bool(extra.get("gmail_proxy_enabled")):
        return ""
    return str(extra.get("gmail_proxy_url") or "").strip()


GMAIL_SCOPES = [
    "https://mail.google.com/",
    "https://www.googleapis.com/auth/userinfo.email",
]
