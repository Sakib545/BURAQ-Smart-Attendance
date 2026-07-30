from app.database import get_db
from app.config import settings

DEFAULTS = {
    "whatsapp_verify_token": settings.whatsapp_verify_token,
    "whatsapp_access_token": settings.whatsapp_access_token,
    "whatsapp_phone_number_id": settings.whatsapp_phone_number_id,
    "meta_api_version": settings.meta_api_version,
}

def get_setting(key: str, default: str = "") -> str:
    try:
        with get_db() as c:
            row = c.execute("SELECT value FROM system_settings WHERE key=?", (key,)).fetchone()
            if row and row["value"] is not None:
                return str(row["value"])
    except Exception:
        pass
    return str(DEFAULTS.get(key, default) or default)

def set_setting(key: str, value: str) -> None:
    with get_db() as c:
        c.execute(
            "INSERT INTO system_settings(key,value,updated_at) VALUES(?,?,CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=CURRENT_TIMESTAMP",
            (key, value),
        )

def configured() -> bool:
    return all(get_setting(k) for k in ("whatsapp_verify_token", "whatsapp_access_token", "whatsapp_phone_number_id"))
