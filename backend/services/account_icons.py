import io
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from data_paths import ACCOUNT_ICONS_DIR, ensure_data_dirs


MAX_ACCOUNT_ICON_BYTES = 10 * 1024 * 1024
MAX_ACCOUNT_ICON_PIXELS = 40_000_000
ACCOUNT_ICON_SIZE = 256
ACCOUNT_ICON_PRESET_IDS = frozenset({
    "mail-purple",
    "mail-blue",
    "mail-green",
    "work",
    "personal",
    "school",
    "team",
    "star",
})
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
_ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}


@dataclass
class StagedAccountIcon:
    target: Path
    temporary: Path
    backup: Path | None = None


def _safe_id(value: str, label: str) -> str:
    normalized = (value or "").strip()
    if not normalized or not _SAFE_ID.fullmatch(normalized):
        raise ValueError(f"{label}无效")
    return normalized


def account_icon_path(user_uid: str, account_id: str) -> Path:
    safe_user = _safe_id(user_uid, "用户 ID")
    safe_account = _safe_id(account_id, "账号 ID")
    return ACCOUNT_ICONS_DIR / safe_user / f"{safe_account}.webp"


def _normalize_image(data: bytes, temporary: Path) -> None:
    if not data:
        raise ValueError("图片文件为空")
    if len(data) > MAX_ACCOUNT_ICON_BYTES:
        raise ValueError("图片不能超过 10 MB")

    try:
        with Image.open(io.BytesIO(data)) as source:
            image_format = (source.format or "").upper()
            if image_format not in _ALLOWED_FORMATS:
                raise ValueError("仅支持 JPG、PNG 或 WebP 图片")
            if source.width < 1 or source.height < 1:
                raise ValueError("无法读取该图片，请更换文件")
            if source.width * source.height > MAX_ACCOUNT_ICON_PIXELS:
                raise ValueError("图片尺寸过大，请更换图片")
            source.load()
            normalized = ImageOps.exif_transpose(source)
            normalized = normalized.convert("RGBA" if "A" in normalized.getbands() else "RGB")
            normalized = ImageOps.fit(
                normalized,
                (ACCOUNT_ICON_SIZE, ACCOUNT_ICON_SIZE),
                method=Image.Resampling.LANCZOS,
            )
            normalized.save(temporary, format="WEBP", quality=90, method=6)
    except ValueError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("无法读取该图片，请更换文件") from exc


def stage_account_icon(user_uid: str, account_id: str, data: bytes) -> StagedAccountIcon:
    ensure_data_dirs()
    target = account_icon_path(user_uid, account_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.stem}.{uuid.uuid4().hex}.tmp.webp"
    try:
        _normalize_image(data, temporary)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return StagedAccountIcon(target=target, temporary=temporary)


def commit_staged_account_icon(staged: StagedAccountIcon) -> Path:
    backup = None
    if staged.target.exists():
        backup = staged.target.parent / f".{staged.target.stem}.{uuid.uuid4().hex}.bak.webp"
        os.replace(staged.target, backup)
        staged.backup = backup
    try:
        os.replace(staged.temporary, staged.target)
    except Exception:
        if backup and backup.exists():
            os.replace(backup, staged.target)
        raise
    if backup:
        backup.unlink(missing_ok=True)
    staged.backup = None
    return staged.target


def rollback_staged_account_icon(staged: StagedAccountIcon) -> None:
    staged.temporary.unlink(missing_ok=True)
    if staged.backup and staged.backup.exists():
        if staged.target.exists():
            staged.target.unlink(missing_ok=True)
        os.replace(staged.backup, staged.target)
    staged.backup = None


def save_account_icon(user_uid: str, account_id: str, data: bytes) -> Path:
    staged = stage_account_icon(user_uid, account_id, data)
    try:
        return commit_staged_account_icon(staged)
    except Exception:
        rollback_staged_account_icon(staged)
        raise


def resolve_account_icon(user_uid: str, account_id: str) -> Path | None:
    try:
        target = account_icon_path(user_uid, account_id)
        root = (ACCOUNT_ICONS_DIR / _safe_id(user_uid, "用户 ID")).resolve()
        resolved = target.resolve()
    except (OSError, ValueError):
        return None
    if resolved.parent != root or resolved.suffix.lower() != ".webp":
        return None
    return resolved


def delete_account_icon(user_uid: str, account_id: str) -> None:
    target = resolve_account_icon(user_uid, account_id)
    if not target:
        return
    try:
        target.unlink(missing_ok=True)
        parent = target.parent
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        return


def delete_user_account_icons(user_uid: str) -> None:
    try:
        user_dir = account_icon_path(user_uid, "placeholder").parent
        root = ACCOUNT_ICONS_DIR.resolve()
        resolved = user_dir.resolve()
    except (OSError, ValueError):
        return
    if resolved.parent != root:
        return
    shutil.rmtree(resolved, ignore_errors=True)
