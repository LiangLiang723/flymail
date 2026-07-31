from models import Account
from services.account_icons import ACCOUNT_ICON_PRESET_IDS, resolve_account_icon


def account_icon_fields(account: Account) -> dict[str, str]:
    icon_type = account.icon_type if account.icon_type in {"default", "preset", "upload"} else "default"
    icon_value = ""
    icon_url = ""

    if icon_type == "preset":
        if account.icon_value in ACCOUNT_ICON_PRESET_IDS:
            icon_value = account.icon_value
        else:
            icon_type = "default"

    if icon_type == "upload":
        icon_path = resolve_account_icon(account.user_uid, account.id)
        if icon_path and icon_path.is_file():
            icon_url = f"/api/accounts/{account.id}/icon?v={int(account.updated_at or 0)}"
        else:
            icon_type = "default"

    return {
        "icon_type": icon_type,
        "icon_value": icon_value,
        "icon_url": icon_url,
    }
