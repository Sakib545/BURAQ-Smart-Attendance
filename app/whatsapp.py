import logging
import httpx
from app.config import settings
from app.services import log, process

logger = logging.getLogger(__name__)


async def send_text(to: str, text: str):
    if not settings.whatsapp_access_token or not settings.whatsapp_phone_number_id:
        logger.error("WhatsApp credentials are missing")
        return {"sent": False, "reason": "credentials missing"}

    url = f"https://graph.facebook.com/{settings.meta_api_version}/{settings.whatsapp_phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {settings.whatsapp_access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, headers=headers, json=payload)
            if response.is_error:
                logger.error("WhatsApp API error %s: %s", response.status_code, response.text)
                return {"sent": False, "status_code": response.status_code, "error": response.text}
            data = response.json()
            log("outgoing", to, "text", text, data.get("messages", [{}])[0].get("id"))
            return {"sent": True, "data": data}
    except Exception as exc:
        logger.exception("Could not send WhatsApp message")
        return {"sent": False, "error": str(exc)}


async def handle(payload: dict):
    processed = 0
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for message in value.get("messages", []):
                phone = message.get("from", "")
                typ = message.get("type", "unknown")
                message_id = message.get("id")

                # Meta can retry the same webhook. A duplicate must not create
                # a second attendance entry or a second reply.
                if message_id and not log("incoming", phone, typ, str(message), message_id):
                    logger.info("Ignored duplicate webhook message %s", message_id)
                    continue

                if typ == "text":
                    text = message.get("text", {}).get("body", "")
                    await send_text(phone, process(phone, text))
                elif typ == "location":
                    await send_text(phone, "📍 Location পেয়েছি। GPS verification পরবর্তী version-এ সক্রিয় হবে।")
                elif typ == "image":
                    await send_text(phone, "📸 ছবি পেয়েছি। Face verification পরবর্তী version-এ সক্রিয় হবে।")
                else:
                    await send_text(phone, "এই message type এখনো supported নয়।")
                processed += 1
    return processed
