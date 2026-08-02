import asyncio
import logging
import httpx
from app.runtime import get_setting
from app.services import log, process, employee_by_phone, activate_known_phone, set_state, receive_location, receive_image

logger = logging.getLogger(__name__)


def _credentials():
    return (get_setting("whatsapp_access_token"), get_setting("whatsapp_phone_number_id"), get_setting("meta_api_version", "v23.0"))


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


async def download_media(media_id: str):
    token, _, api_version = _credentials()
    if not token or not media_id:
        return None
    headers = {"Authorization": f"Bearer {token}"}
    for attempt in range(1, 4):
        try:
            async with httpx.AsyncClient(timeout=45, follow_redirects=True) as client:
                meta = await client.get(f"https://graph.facebook.com/{api_version}/{media_id}", headers=headers)
                meta.raise_for_status()
                media_url = meta.json().get("url")
                if not media_url:
                    raise RuntimeError("Meta did not return a media URL")
                data = await client.get(media_url, headers=headers)
                data.raise_for_status()
                return data.content
        except Exception:
            logger.exception("Could not download WhatsApp media %s attempt=%s", media_id, attempt)
            if attempt < 3:
                await asyncio.sleep(attempt)
    return None


async def send_text(to: str, text: str):
    return await _send(to, {"type": "text", "text": {"preview_url": False, "body": text}})


async def send_selfie_review_result(to: str, name: str, action: str, approved: bool, score: float, attendance_result: str = ""):
    """Notify an employee after HR resolves a pending attendance selfie."""
    duty_action = "Check-in" if action == "check_in" else "Check-out"
    if approved:
        message = (
            f"✅ Attendance Approved\n\n{name}, আপনার {duty_action} selfie Admin approve করেছেন।\n"
            f"{attendance_result or 'Attendance সফলভাবে final হয়েছে।'}\n"
            f"Security score: {score * 100:.1f}%"
        )
    else:
        message = (
            f"❌ Attendance Selfie Rejected\n\n{name}, আপনার {duty_action} selfie Admin reject করেছেন।\n"
            f"Security score: {score * 100:.1f}%\n\n"
            "Attendance record হয়নি। WhatsApp menu থেকে আবার চেষ্টা করে নতুন live selfie তুলুন।"
        )
    result = {"sent": False, "reason": "not attempted"}
    for attempt in range(1, 4):
        result = await send_text(to, message)
        if result.get("sent"):
            return result
        # Do not repeatedly retry permanent Meta/client errors.
        status_code = int(result.get("status_code") or 0)
        if 400 <= status_code < 500:
            break
        if attempt < 3:
            await asyncio.sleep(attempt)
    logger.error("Selfie review notification failed phone=%s action=%s approved=%s result=%s", to, action, approved, result)
    return result

async def send_template(to: str, template_name: str, values: list[str]):
    return await _send(to,{"type":"template","template":{"name":template_name,"language":{"code":"bn"},"components":[{"type":"body","parameters":[{"type":"text","text":str(v)} for v in values]}]}})

async def send_document_bytes(to: str, content: bytes, filename: str, caption: str=""):
    token, phone_id, api_version = _credentials()
    if not token or not phone_id or not to: return {"sent":False,"reason":"WhatsApp setup or employee number missing"}
    headers={"Authorization":f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            upload=await client.post(f"https://graph.facebook.com/{api_version}/{phone_id}/media",headers=headers,data={"messaging_product":"whatsapp","type":"application/pdf"},files={"file":(filename,content,"application/pdf")})
        if upload.is_error: return {"sent":False,"status_code":upload.status_code,"error":upload.text}
        return await _send(to,{"type":"document","document":{"id":upload.json()["id"],"filename":filename,"caption":caption}})
    except Exception as exc:
        logger.exception("Could not send WhatsApp document")
        return {"sent":False,"error":str(exc)}


async def send_location_request(to: str, body_text: str = "📍 Attendance-এর জন্য আপনার বর্তমান Location পাঠান।"):
    return await _send(to, {
        "type": "interactive",
        "interactive": {
            "type": "location_request_message",
            "body": {"text": body_text},
            "action": {"name": "send_location"},
        },
    })


async def send_menu(to: str, name: str | None = None):
    greeting = f"স্বাগতম {name}" if name else "BURAQ Smart Attendance"
    return await _send(to, {
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": "BURAQ Attendance"},
            "body": {"text": f"👋 {greeting}\nনিচের Menu থেকে কাজ নির্বাচন করুন।"},
            "footer": {"text": "Face AI • GPS • Secure"},
            "action": {
                "button": "Menu খুলুন",
                "sections": [{"title": "Attendance Menu", "rows": [
                    {"id": "register", "title": "Register", "description": "Staff ID দিয়ে নিবন্ধন"},
                    {"id": "check_in", "title": "Check In", "description": "GPS ও Face AI দিয়ে উপস্থিতি"},
                    {"id": "check_out", "title": "Check Out", "description": "GPS ও Face AI দিয়ে ছুটি"},
                    {"id": "my_attendance", "title": "My Attendance", "description": "সাম্প্রতিক ৭ দিনের রিপোর্ট"},
                    {"id": "my_duty", "title": "My Duty", "description": "আগামী duty schedule দেখুন"},
                ]}]
            }
        }
    })


async def send_guided_response(phone: str, response: str):
    if response == "__REQUEST_LOCATION__":
        result = await send_location_request(phone)
        if result.get("sent"):
            return result
        logger.error("Native location request failed phone=%s result=%s", phone, result)
        return await send_text(phone, "📍 নিচের attachment (+/📎) বাটন থেকে Location খুলে Current Location পাঠান।")
    result = await send_text(phone, response)
    if response.startswith("✅ Face Registration সম্পন্ন") or response.startswith("✅ Check In সফল") or response.startswith("✅ Check Out সফল"):
        employee = employee_by_phone(phone)
        await send_menu(phone, employee["name"] if employee else None)
    return result


async def send_guided_response_with_retry(phone: str, response: str):
    result = {"sent": False, "reason": "not attempted"}
    for attempt in range(1, 4):
        result = await send_guided_response(phone, response)
        if result.get("sent"):
            return result
        status_code = int(result.get("status_code") or 0)
        if 400 <= status_code < 500:
            break
        if attempt < 3:
            await asyncio.sleep(attempt)
    logger.error("WhatsApp response failed phone=%s result=%s", phone, result)
    return result


async def send_approval_flow(phone: str, name: str, staff_id: str):
    await send_text(phone, f"✅ আপনার BURAQ Attendance registration Admin approve করেছেন।\n\nনাম: {name}\nStaff ID: {staff_id}\n\n📸 পরবর্তী ধাপ: সামনে তাকিয়ে ৩টি পরিষ্কার selfie পাঠান। প্রথম selfie এখন পাঠান।")


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

                normalized = " ".join(text.strip().lower().split())
                if normalized in {"hi", "hello", "menu", "start"}:
                    employee = employee_by_phone(phone)
                    if not employee: employee=activate_known_phone(phone)
                    if employee:
                        await send_menu(phone, employee["name"])
                    else:
                        set_state(phone,"awaiting_staff_id")
                        await send_text(phone,"👋 BURAQ Smart Attendance-এ স্বাগতম।\n\nআপনার WhatsApp নম্বর employee profile-এর সঙ্গে মেলেনি। শুধু Staff ID পাঠান।")
                elif typ in {"text", "interactive"}:
                    await send_guided_response(phone, process(phone, text))
                elif typ == "location":
                    loc = message.get("location", {})
                    response = receive_location(phone, loc.get("latitude"), loc.get("longitude"))
                    await send_guided_response(phone, response)
                elif typ == "image":
                    media_id = message.get("image", {}).get("id", "")
                    try:
                        image_bytes = await download_media(media_id)
                        # OpenCV/Face AI is CPU-heavy. Running it in a worker
                        # thread keeps webhook and Railway healthcheck responsive.
                        response = await asyncio.to_thread(receive_image, phone, media_id, image_bytes)
                    except Exception:
                        logger.exception("Selfie processing failed phone=%s media_id=%s", phone, media_id)
                        response = "⚠️ Selfie processing সাময়িকভাবে ব্যর্থ হয়েছে। একটি নতুন live selfie আবার পাঠান।"
                    await send_guided_response_with_retry(phone, response)
                else:
                    await send_text(phone, "এই message type এখনো supported নয়। Menu খুলতে লিখুন: Menu")
                processed += 1
    return processed
