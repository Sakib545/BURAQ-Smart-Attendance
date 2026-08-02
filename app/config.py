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
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    admin_api_key: str = os.getenv("ADMIN_API_KEY", "")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    allow_temp_db_fallback: bool = os.getenv("ALLOW_TEMP_DB_FALLBACK", "false").strip().lower() in {"1","true","yes","on"}
    require_secure_secrets: bool = os.getenv("REQUIRE_SECURE_SECRETS", "true").strip().lower() in {"1","true","yes","on"}
    duplicate_accept_below: float = float(os.getenv("DUPLICATE_ACCEPT_BELOW", "0.76"))
    duplicate_reject_at: float = float(os.getenv("DUPLICATE_REJECT_AT", "0.91"))
    duplicate_hash_weight: float = float(os.getenv("DUPLICATE_HASH_WEIGHT", "0.55"))
    duplicate_face_weight: float = float(os.getenv("DUPLICATE_FACE_WEIGHT", "0.10"))
    duplicate_pose_weight: float = float(os.getenv("DUPLICATE_POSE_WEIGHT", "0.15"))
    duplicate_landmark_weight: float = float(os.getenv("DUPLICATE_LANDMARK_WEIGHT", "0.20"))
    duplicate_corroboration_gate: float = float(os.getenv("DUPLICATE_CORROBORATION_GATE", "0.72"))
    face_match_threshold: float = float(os.getenv("FACE_MATCH_THRESHOLD", "0.48"))
    face_quality_min: float = float(os.getenv("FACE_QUALITY_MIN", "45"))
    # A sample below this never enters the gallery. A weak enrolment sample is
    # permanent and makes every later verification harder.
    face_enroll_quality_min: float = float(os.getenv("FACE_ENROLL_QUALITY_MIN", "55"))
    # The claimed employee must beat every other employee by this much.
    face_margin_min: float = float(os.getenv("FACE_MARGIN_MIN", "0.06"))
    face_enroll_samples: int = int(os.getenv("FACE_ENROLL_SAMPLES", "3"))
    # Adaptive gallery: a confidently verified selfie can join the gallery so
    # the profile follows beards, glasses and ageing instead of drifting away.
    face_adapt_enabled: bool = os.getenv("FACE_ADAPT_ENABLED", "true").strip().lower() in {"1","true","yes","on"}
    face_adapt_min_score: float = float(os.getenv("FACE_ADAPT_MIN_SCORE", "0.62"))
    face_adapt_min_quality: float = float(os.getenv("FACE_ADAPT_MIN_QUALITY", "70"))
    face_adapt_min_margin: float = float(os.getenv("FACE_ADAPT_MIN_MARGIN", "0.12"))
    face_gallery_max: int = int(os.getenv("FACE_GALLERY_MAX", "8"))
    face_blur_min: float = float(os.getenv("FACE_BLUR_MIN", "42"))
    # Location is the primary attendance boundary for BURAQ. In simple mode
    # Face AI confirms identity without strict pose/passive-liveness gates.
    simple_face_mode: bool = os.getenv("SIMPLE_FACE_MODE", "true").strip().lower() in {"1","true","yes","on"}

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
        return issues

    def production_warnings(self) -> list[str]:
        """Non-critical integrations must never prevent attendance startup."""
        warnings: list[str] = []
        backup_key = os.getenv("BACKUP_ENCRYPTION_KEY", "").strip()
        if backup_key and len(backup_key) < 32:
            warnings.append("BACKUP_ENCRYPTION_KEY is short; CONFIG_ENCRYPTION_KEY fallback will be used")
        if os.getenv("BACKUP_S3_BUCKET", "").strip():
            missing = [key for key in ("BACKUP_S3_ACCESS_KEY_ID", "BACKUP_S3_SECRET_ACCESS_KEY") if not os.getenv(key, "").strip()]
            if missing:
                warnings.append("Off-site backup disabled; missing " + ", ".join(missing))
        return warnings

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
