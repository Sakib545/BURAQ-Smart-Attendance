from dataclasses import dataclass
from pathlib import Path
import os
from dotenv import load_dotenv
load_dotenv()
@dataclass(frozen=True)
class Settings:
    app_name: str=os.getenv("APP_NAME","BURAQ Smart Attendance")
    timezone: str=os.getenv("TIMEZONE","Asia/Dhaka")
    database_path: str=os.getenv("DATABASE_PATH","data/buraq_attendance.db")
    whatsapp_verify_token: str=os.getenv("WHATSAPP_VERIFY_TOKEN","")
    whatsapp_access_token: str=os.getenv("WHATSAPP_ACCESS_TOKEN","")
    whatsapp_phone_number_id: str=os.getenv("WHATSAPP_PHONE_NUMBER_ID","")
    meta_api_version: str=os.getenv("META_API_VERSION","v23.0")
    admin_api_key: str=os.getenv("ADMIN_API_KEY","")
settings=Settings()
Path(settings.database_path).parent.mkdir(parents=True,exist_ok=True)
Path("exports").mkdir(exist_ok=True)
