from datetime import datetime


def format_time_12h(value: str | None) -> str:
    """Display a duty time or ISO attendance timestamp as 12-hour time."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).strftime("%I:%M %p").lstrip("0")
    except ValueError:
        pass
    for pattern in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(text, pattern).strftime("%I:%M %p").lstrip("0")
        except ValueError:
            continue
    return text
