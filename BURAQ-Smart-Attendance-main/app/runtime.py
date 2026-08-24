import base64
import hashlib
import os
from cryptography.fernet import Fernet, InvalidToken

from app.database import get_db
from app.config import settings

DEFAULTS = {
    "whatsapp_verify_token": settings.whatsapp_verify_token,
    "whatsapp_access_token": settings.whatsapp_access_token,
    "whatsapp_phone_number_id": settings.whatsapp_phone_number_id,
    "meta_api_version": settings.meta_api_version,
}

SENSITIVE_KEYS = {"whatsapp_verify_token", "whatsapp_access_token", "whatsapp_phone_number_id"}


def _fernet() -> Fernet:
    seed = (
        os.getenv("CONFIG_ENCRYPTION_KEY")
        or os.getenv("SESSION_SECRET")
        or os.getenv("ADMIN_API_KEY")
        or "buraq-attendance-development-fallback-key"
    )
    key = base64.urlsafe_b64encode(hashlib.sha256(seed.encode("utf-8")).digest())
    return Fernet(key)


def _encrypt(value: str) -> str:
    if not value:
        return ""
    return "enc:v1:" + _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def _decrypt(value: str) -> str:
    if not value.startswith("enc:v1:"):
        return value
    try:
        return _fernet().decrypt(value[7:].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""


def get_setting(key: str, default: str = "") -> str:
    try:
        with get_db() as c:
            row = c.execute("SELECT value FROM system_settings WHERE key=?", (key,)).fetchone()
            if row and row["value"] is not None:
                raw = str(row["value"])
                return _decrypt(raw) if key in SENSITIVE_KEYS else raw
    except Exception:
        pass
    return str(DEFAULTS.get(key, default) or default)


def set_setting(key: str, value: str) -> None:
    stored = _encrypt(value) if key in SENSITIVE_KEYS else value
    with get_db() as c:
        c.execute(
            "INSERT INTO system_settings(key,value,updated_at) VALUES(?,?,CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=CURRENT_TIMESTAMP",
            (key, stored),
        )


def configured() -> bool:
    return all(get_setting(k) for k in ("whatsapp_verify_token", "whatsapp_access_token", "whatsapp_phone_number_id"))


def import_environment_defaults() -> None:
    """Import Railway variables once, without overwriting dashboard-managed values."""
    for key, value in DEFAULTS.items():
        if value and not get_setting(key):
            set_setting(key, str(value))


def get_stored_setting(key: str) -> str:
    """Return the database representation for encrypted configuration backup."""
    try:
        with get_db() as c:
            row = c.execute("SELECT value FROM system_settings WHERE key=?", (key,)).fetchone()
            return str(row["value"]) if row and row["value"] is not None else ""
    except Exception:
        return ""


def restore_stored_setting(key: str, stored_value: str) -> None:
    """Restore an already encrypted database representation."""
    with get_db() as c:
        c.execute(
            "INSERT INTO system_settings(key,value,updated_at) VALUES(?,?,CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=CURRENT_TIMESTAMP",
            (key, stored_value),
        )
