"""Short-lived signed tokens for the browser location fallback."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time


_EPHEMERAL_SECRET = secrets.token_urlsafe(48)


def _secret() -> bytes:
    value = (
        os.getenv("LOCATION_LINK_SECRET")
        or os.getenv("SESSION_SECRET")
        or os.getenv("CONFIG_ENCRYPTION_KEY")
        or _EPHEMERAL_SECRET
    )
    return value.encode("utf-8")


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def create_location_token(employee_id: int, action: str, ttl_seconds: int = 600) -> str:
    if action not in {"checkin", "checkout"}:
        raise ValueError("Invalid attendance action")
    payload = {
        "employee_id": int(employee_id),
        "action": action,
        "expires_at": int(time.time()) + max(60, int(ttl_seconds)),
        "nonce": secrets.token_urlsafe(10),
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = hmac.new(_secret(), raw, hashlib.sha256).digest()
    return f"{_encode(raw)}.{_encode(signature)}"


def verify_location_token(token: str) -> dict:
    try:
        encoded_payload, encoded_signature = token.split(".", 1)
        raw = _decode(encoded_payload)
        signature = _decode(encoded_signature)
        expected = hmac.new(_secret(), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("Invalid token signature")
        payload = json.loads(raw.decode("utf-8"))
        if payload.get("action") not in {"checkin", "checkout"}:
            raise ValueError("Invalid token action")
        if int(payload.get("expires_at") or 0) < int(time.time()):
            raise ValueError("Location link expired")
        payload["employee_id"] = int(payload["employee_id"])
        return payload
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid or expired location link") from exc
