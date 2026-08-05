import hashlib
import io
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from data_paths import SIGNATURE_IMAGES_DIR, ensure_data_dirs


MAX_SIGNATURE_IMAGE_BYTES = 5 * 1024 * 1024
MAX_SIGNATURE_IMAGE_PIXELS = 40_000_000
MAX_SIGNATURE_IMAGE_DIMENSION = 1200
_ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
_IMAGE_ID_PATTERN = re.compile(r"^(?P<bucket>[0-9a-f]{24})\.(?P<name>[0-9a-f]{32})$")


@dataclass(frozen=True)
class StoredSignatureImage:
    image_id: str
    path: Path


def _user_bucket(user_uid: str) -> str:
    normalized = str(user_uid or "").strip()
    if not normalized:
        raise ValueError("用户 ID 无效")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def _normalize_image(data: bytes) -> bytes:
    if not data:
        raise ValueError("图片文件为空")
    if len(data) > MAX_SIGNATURE_IMAGE_BYTES:
        raise ValueError("图片不能超过 5 MB")

    try:
        with Image.open(io.BytesIO(data)) as source:
            image_format = (source.format or "").upper()
            if image_format not in _ALLOWED_FORMATS:
                raise ValueError("仅支持 JPG、PNG 或 WebP 图片")
            if source.width < 1 or source.height < 1:
                raise ValueError("无法读取该图片，请更换文件")
            if source.width * source.height > MAX_SIGNATURE_IMAGE_PIXELS:
                raise ValueError("图片尺寸过大，请更换图片")
            source.load()
            normalized = ImageOps.exif_transpose(source)
            has_alpha = "A" in normalized.getbands() or "transparency" in source.info
            normalized = normalized.convert("RGBA" if has_alpha else "RGB")
            normalized.thumbnail(
                (MAX_SIGNATURE_IMAGE_DIMENSION, MAX_SIGNATURE_IMAGE_DIMENSION),
                Image.Resampling.LANCZOS,
            )
            output = io.BytesIO()
            normalized.save(output, format="WEBP", quality=88, method=6)
            return output.getvalue()
    except ValueError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("无法读取该图片，请更换文件") from exc


def save_signature_image(user_uid: str, data: bytes) -> StoredSignatureImage:
    normalized = _normalize_image(data)
    ensure_data_dirs()
    bucket = _user_bucket(user_uid)
    name = uuid.uuid4().hex
    image_id = f"{bucket}.{name}"
    target_dir = SIGNATURE_IMAGES_DIR / bucket
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{name}.webp"
    temporary = target_dir / f".{name}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_bytes(normalized)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return StoredSignatureImage(image_id=image_id, path=target)


def resolve_signature_image(image_id: str) -> Path | None:
    match = _IMAGE_ID_PATTERN.fullmatch(str(image_id or "").strip().lower())
    if not match:
        return None
    root = SIGNATURE_IMAGES_DIR.resolve()
    target = SIGNATURE_IMAGES_DIR / match.group("bucket") / f"{match.group('name')}.webp"
    try:
        resolved = target.resolve()
    except OSError:
        return None
    if root not in resolved.parents or resolved.suffix.lower() != ".webp":
        return None
    return resolved
