import base64
import hashlib
import html
import re
import uuid
from dataclasses import dataclass

from services.mime_parts import InlineImagePart
from services.signature_images import (
    MAX_SIGNATURE_IMAGE_BYTES,
    parse_signature_image_id,
    resolve_signature_image,
    signature_image_belongs_to_user,
)


_IMAGE_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE | re.DOTALL)
_SRC_ATTR_RE = re.compile(
    r'\bsrc\s*=\s*(?P<quote>["\'])(?P<src>.*?)(?P=quote)',
    re.IGNORECASE | re.DOTALL,
)
_MANAGED_ATTR_RE = re.compile(
    r'\sdata-flymail-signature-image\s*=\s*(?P<quote>["\'])(?P<image_id>.*?)(?P=quote)',
    re.IGNORECASE | re.DOTALL,
)
_DATA_IMAGE_RE = re.compile(
    r"^data:(?P<content_type>image/(?:png|jpeg|gif|webp));base64,(?P<payload>.+)$",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class PreparedInlineBody:
    body_html: str
    inline_images: list[InlineImagePart]


def _replace_src(tag: str, source: str) -> str:
    escaped = html.escape(source, quote=True)
    if _SRC_ATTR_RE.search(tag):
        return _SRC_ATTR_RE.sub(f'src="{escaped}"', tag, count=1)
    close_at = tag.rfind("/>")
    if close_at >= 0:
        return f'{tag[:close_at].rstrip()} src="{escaped}" />'
    close_at = tag.rfind(">")
    if close_at < 0:
        return tag
    return f'{tag[:close_at].rstrip()} src="{escaped}">'


def _managed_id_from_tag(tag: str, src: str) -> tuple[str | None, bool]:
    managed_match = _MANAGED_ATTR_RE.search(tag)
    if managed_match:
        raw_id = html.unescape(managed_match.group("image_id")).strip()
        image_id = parse_signature_image_id(f"flymail-signature-image:{raw_id}")
        return image_id, True

    decoded_src = html.unescape(src)
    image_id = parse_signature_image_id(decoded_src)
    managed_marker = (
        decoded_src.lower().startswith("flymail-signature-image:")
        or "/api/signature-images/" in decoded_src.lower()
    )
    return image_id, managed_marker


def _content_id() -> str:
    return f"flymail-{uuid.uuid4().hex}@inline"


async def prepare_inline_images(user_uid: str, body_html: str) -> PreparedInlineBody:
    parts: list[InlineImagePart] = []
    cached_parts: dict[str, InlineImagePart] = {}

    def replace_tag(match: re.Match[str]) -> str:
        tag = match.group(0)
        src_match = _SRC_ATTR_RE.search(tag)
        src = src_match.group("src") if src_match else ""
        image_id, managed_marker = _managed_id_from_tag(tag, src)

        if managed_marker:
            if not image_id:
                raise ValueError("签名图片标识无效，请重新插入图片")
            if not signature_image_belongs_to_user(user_uid, image_id):
                raise ValueError("签名图片不属于当前用户")
            image_path = resolve_signature_image(image_id)
            if not image_path or not image_path.is_file():
                raise ValueError("签名图片不存在，请重新插入图片")
            cache_key = f"asset:{image_id}"
            part = cached_parts.get(cache_key)
            if part is None:
                part = InlineImagePart(
                    content_id=_content_id(),
                    data=image_path.read_bytes(),
                    content_type="image/webp",
                    filename=f"signature-{image_id.rsplit('.', 1)[-1]}.webp",
                )
                cached_parts[cache_key] = part
                parts.append(part)
            rewritten = _MANAGED_ATTR_RE.sub("", tag)
            return _replace_src(rewritten, f"cid:{part.content_id}")

        decoded_src = html.unescape(src)
        data_match = _DATA_IMAGE_RE.fullmatch(decoded_src)
        if not data_match:
            return tag

        payload = re.sub(r"\s+", "", data_match.group("payload"))
        try:
            data = base64.b64decode(payload, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("内嵌图片数据无效，请重新插入图片") from exc
        if not data:
            raise ValueError("内嵌图片数据为空，请重新插入图片")
        if len(data) > MAX_SIGNATURE_IMAGE_BYTES:
            raise ValueError("内嵌图片不能超过 5 MB")

        content_type = data_match.group("content_type").lower()
        digest = hashlib.sha256(content_type.encode("ascii") + b"\0" + data).hexdigest()
        cache_key = f"data:{digest}"
        part = cached_parts.get(cache_key)
        if part is None:
            extension = "jpg" if content_type == "image/jpeg" else content_type.split("/", 1)[1]
            part = InlineImagePart(
                content_id=_content_id(),
                data=data,
                content_type=content_type,
                filename=f"inline-{digest[:12]}.{extension}",
            )
            cached_parts[cache_key] = part
            parts.append(part)
        return _replace_src(tag, f"cid:{part.content_id}")

    prepared_html = _IMAGE_TAG_RE.sub(replace_tag, str(body_html or ""))
    return PreparedInlineBody(body_html=prepared_html, inline_images=parts)
