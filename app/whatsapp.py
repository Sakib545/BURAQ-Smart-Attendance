import logging
import httpx
from app.runtime import get_setting
from app.services import log, process

logger = logging.getLogger(__name__)


def _credentials():
    return (
        get_setting("whatsapp_access_token"),
        get_setting("whatsapp_phone_number_id"),
        get_setting("meta_api_version", "v23.0"),
    )


async def _send(to: str, payload: dict):
    token, phone_id, api_version = _credentials()
    if not token or not phone_id:
        logger.error("WhatsApp credentials are missing")
        return {"sent": False, "reason": "WhatsApp setup incomplete"}
    url = f"https://graph.facebook.com/{api_version}/{phone_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to, **payload}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, headers=headers, json=body)
        if response.is_error:
            logger.error("WhatsApp API error %s: %s", response.status_code, response.text)
            return {"sent": False, "status_code": response.status_code, "error": response.text}
        data = response.json()
        log("outgoing", to, payload.get("type", "unknown"), str(payload), data.get("messages", [{}])[0].get("id"))
        return {"sent": True, "data": data}
    except Exception as exc:
        logger.exception("Could not send WhatsApp message")
        return {"sent": False, "error": str(exc)}


async def send_text(to: str, text: str):
    return await _send(to, {"type": "text", "text": {"preview_url": False, "body": text}})


async def send_menu(to: str, name: str | None = None):
    greeting = f"স্বাগতম {name}" if name else "BURAQ Smart Attendance"
    return await _send(to, {
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": "BURAQ Attendance"},
            "body": {"text": f"👋 {greeting}\nনিচের Menu থেকে কাজ নির্বাচন করুন।"},
            "footer": {"text": "সহজ • দ্রুত • নিরাপদ"},
            "action": {
                "button": "Menu খুলুন",
                "sections": [{"title": "Attendance Menu", "rows": [
                    {"id": "register", "title": "Register", "description": "Staff ID দিয়ে নিবন্ধন"},
                    {"id": "check_in", "title": "Check In", "description": "আজকের উপস্থিতি শুরু"},
                    {"id": "check_out", "title": "Check Out", "description": "আজকের উপস্থিতি শেষ"},
                    {"id": "my_attendance", "title": "My Attendance", "description": "সাম্প্রতিক ৭ দিনের রিপোর্ট"},
                ]}]
            }
        }
    })


async def handle(payload: dict):
    processed = 0
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for message in value.get("messages", []):
                phone = message.get("from", "")
                typ = message.get("type", "unknown")
                message_id = message.get("id")
                if message_id and not log("incoming", phone, typ, str(message), message_id):
                    logger.info("Ignored duplicate webhook message %s", message_id)
                    continue
                text = ""
                if typ == "text":
                    text = message.get("text", {}).get("body", "")
                elif typ == "interactive":
                    interactive = message.get("interactive", {})
                    text = (interactive.get("list_reply") or interactive.get("button_reply") or {}).get("id", "")
                elif typ == "location":
                    text = "location"
                elif typ == "image":
                    text = "image"

                normalized = " ".join(text.strip().lower().split())
                if normalized in {"hi", "hello", "menu", "start"}:
                    from app.services import employee_by_phone
                    employee = employee_by_phone(phone)
                    await send_menu(phone, employee["name"] if employee else None)
                elif typ in {"text", "interactive"}:
                    await send_text(phone, process(phone, text))
                elif typ == "location":
                    await send_text(phone, "📍 Location পেয়েছি।")
                elif typ == "image":
                    await send_text(phone, "📸 ছবি পেয়েছি।")
                else:
                    await send_text(phone, "এই message type এখনো supported নয়। Hi লিখে Menu খুলুন।")
                processed += 1
    return processed
