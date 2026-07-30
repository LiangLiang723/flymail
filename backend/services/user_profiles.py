import io
import re
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from data_paths import AVATARS_DIR, ensure_data_dirs


MAX_AVATAR_BYTES = 5 * 1024 * 1024
AVATAR_SIZE = 256
_SAFE_USER_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


def _avatar_path(user_id: str) -> Path:
    value = (user_id or "").strip()
    if not value or not _SAFE_USER_ID.fullmatch(value):
        raise ValueError("用户 ID 无效")
    return AVATARS_DIR / f"{value}.webp"


def save_user_avatar(user_id: str, data: bytes) -> str:
    if not data:
        raise ValueError("头像文件为空")
    if len(data) > MAX_AVATAR_BYTES:
        raise ValueError("头像不能超过 5 MB")

    ensure_data_dirs()
    AVATARS_DIR.mkdir(parents=True, exist_ok=True)
    target = _avatar_path(user_id)
    temporary = target.with_suffix(".tmp.webp")

    try:
        with Image.open(io.BytesIO(data)) as source:
            source.load()
            if source.width < 1 or source.height < 1:
                raise ValueError("头像尺寸无效")
            normalized = ImageOps.exif_transpose(source).convert("RGB")
            normalized = ImageOps.fit(
                normalized,
                (AVATAR_SIZE, AVATAR_SIZE),
                method=Image.Resampling.LANCZOS,
            )
            normalized.save(temporary, format="WEBP", quality=88, method=6)
        temporary.replace(target)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        temporary.unlink(missing_ok=True)
        if isinstance(exc, ValueError) and str(exc).startswith("头像"):
            raise
        raise ValueError("无法识别头像图片") from exc

    return str(target)


def resolve_user_avatar(avatar_path: str | None) -> Path | None:
    value = (avatar_path or "").strip()
    if not value:
        return None
    try:
        root = AVATARS_DIR.resolve()
        resolved = Path(value).resolve()
    except OSError:
        return None
    if resolved.parent != root or resolved.suffix.lower() != ".webp":
        return None
    return resolved


def delete_user_avatar(avatar_path: str | None) -> None:
    resolved = resolve_user_avatar(avatar_path)
    if not resolved:
        return
    try:
        resolved.unlink(missing_ok=True)
    except OSError:
        return
