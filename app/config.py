from dataclasses import dataclass
from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()


def _database_url() -> str:
    # Railway injects DATABASE_URL when PostgreSQL is attached.
    value = os.getenv("DATABASE_URL", "").strip()
    if value.startswith("postgres://"):
        value = "postgresql+psycopg://" + value[len("postgres://"):]
    elif value.startswith("postgresql://"):
        value = "postgresql+psycopg://" + value[len("postgresql://"):]
    if value:
        return value
    path = os.getenv("DATABASE_PATH", "data/buraq_attendance.db")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path}"


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "BURAQ Smart Attendance")
    environment: str = os.getenv("ENVIRONMENT", "production")
    timezone: str = os.getenv("TIMEZONE", "Asia/Dhaka")
    database_url: str = _database_url()
    whatsapp_verify_token: str = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
    whatsapp_access_token: str = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
    whatsapp_phone_number_id: str = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    meta_api_version: str = os.getenv("META_API_VERSION", "v23.0")
    admin_api_key: str = os.getenv("ADMIN_API_KEY", "")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    allow_temp_db_fallback: bool = os.getenv("ALLOW_TEMP_DB_FALLBACK", "false").strip().lower() in {"1","true","yes","on"}
    require_secure_secrets: bool = os.getenv("REQUIRE_SECURE_SECRETS", "true").strip().lower() in {"1","true","yes","on"}
    duplicate_accept_below: float = float(os.getenv("DUPLICATE_ACCEPT_BELOW", "0.70"))
    duplicate_reject_at: float = float(os.getenv("DUPLICATE_REJECT_AT", "0.90"))
    duplicate_hash_weight: float = float(os.getenv("DUPLICATE_HASH_WEIGHT", "0.45"))
    duplicate_face_weight: float = float(os.getenv("DUPLICATE_FACE_WEIGHT", "0.25"))
    duplicate_pose_weight: float = float(os.getenv("DUPLICATE_POSE_WEIGHT", "0.15"))
    duplicate_landmark_weight: float = float(os.getenv("DUPLICATE_LANDMARK_WEIGHT", "0.15"))

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql+")

    def production_issues(self) -> list[str]:
        issues: list[str] = []
        if self.environment == "production" and self.require_secure_secrets:
            for key in ("SESSION_SECRET", "CONFIG_ENCRYPTION_KEY"):
                value = os.getenv(key, "").strip()
                if len(value) < 32:
                    issues.append(f"{key} must be at least 32 characters")
        if self.environment == "production" and not os.getenv("DATABASE_URL", "").strip():
            issues.append("DATABASE_URL is required in production")
        backup_key = os.getenv("BACKUP_ENCRYPTION_KEY", "").strip()
        if backup_key and len(backup_key) < 32:
            issues.append("BACKUP_ENCRYPTION_KEY must be at least 32 characters")
        if os.getenv("BACKUP_S3_BUCKET", "").strip():
            for key in ("BACKUP_S3_ACCESS_KEY_ID", "BACKUP_S3_SECRET_ACCESS_KEY"):
                if not os.getenv(key, "").strip():
                    issues.append(f"{key} is required when BACKUP_S3_BUCKET is set")
        return issues

    def validate(self) -> list[str]:
        missing = []
        for key, value in {
            "WHATSAPP_VERIFY_TOKEN": self.whatsapp_verify_token,
            "WHATSAPP_ACCESS_TOKEN": self.whatsapp_access_token,
            "WHATSAPP_PHONE_NUMBER_ID": self.whatsapp_phone_number_id,
            "ADMIN_API_KEY": self.admin_api_key,
        }.items():
            if not value:
                missing.append(key)
        return missing


settings = Settings()
Path("exports").mkdir(exist_ok=True)

# Guided attendance location settings. Set these in Railway Variables for strict office-radius checks.
OFFICE_LATITUDE = os.getenv("OFFICE_LATITUDE", "").strip()
OFFICE_LONGITUDE = os.getenv("OFFICE_LONGITUDE", "").strip()
OFFICE_RADIUS_METERS = float(os.getenv("OFFICE_RADIUS_METERS", "150"))
