"""草稿箱管理服务

通过 IMAP APPEND 命令将草稿保存到邮件服务器的草稿箱文件夹，
支持跨平台（QQ/Gmail/Outlook/iCloud/网易）。
"""
import logging
from email.utils import formataddr, formatdate

from services.message_body import html_to_text, prepare_outgoing_body_html
from services.mime_parts import InlineImagePart, build_alternative_body

logger = logging.getLogger("flymail")


def _build_draft_message(
    from_email: str,
    from_name: str,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    subject: str,
    body_html: str,
    inline_images: list[InlineImagePart] | None = None,
) -> bytes:
    """构建草稿 MIME 邮件（用于 IMAP APPEND）"""
    body_html = prepare_outgoing_body_html(body_html or "")
    body_text = html_to_text(body_html)
    msg = build_alternative_body(body_html, body_text, inline_images)
    msg["From"] = formataddr((from_name or "", from_email))
    if to:
        msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        msg["Bcc"] = ", ".join(bcc)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    # 草稿标记
    msg["X-Draft"] = "True"

    return msg.as_bytes()


async def save_draft_to_imap(receiver, from_email: str, from_name: str,
                              to: list[str], cc: list[str], bcc: list[str],
                              subject: str, body_html: str,
                              folder: str = "Drafts",
                              inline_images: list[InlineImagePart] | None = None) -> tuple[bool, int | None]:
    """通过 IMAP APPEND 命令保存草稿到服务器

    返回 True 表示保存成功。
    """
    try:
        message_bytes = _build_draft_message(
            from_email, from_name, to, cc, bcc, subject, body_html, inline_images
        )
        uid = await receiver.save_draft(message_bytes, folder)
        logger.info("草稿保存成功: %s", subject)
        return True, uid
    except Exception as e:
        logger.error("草稿保存失败: %s", e)
        return False, None


async def delete_draft_from_imap(receiver, uid: int, folder: str = "Drafts") -> bool:
    """删除 IMAP 服务器上的草稿（UID STORE +FLAGS \\Deleted + EXPUNGE）"""
    try:
        await receiver.delete_message_batch([str(uid)], folder)
        logger.info("草稿删除成功: UID %s", uid)
        return True
    except Exception as e:
        logger.error("草稿删除失败: %s", e)
        return False
