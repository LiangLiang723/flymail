"""飞邮版本号统一管理

从项目根目录的 VERSION 文件读取版本号，所有涉及版本信息的地方都从此模块导入。
修改版本号时只需修改根目录的 VERSION 文件即可。
"""

import os
from pathlib import Path


def _read_version() -> str:
    configured = os.environ.get("FLYMAIL_VERSION", "").strip()
    if configured:
        return configured

    repository_root = Path(__file__).resolve().parent.parent
    candidates = (
        repository_root / "VERSION",
        Path("/app/VERSION"),
    )
    for candidate in candidates:
        try:
            value = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value
    return "0.0.0"


VERSION = _read_version()
