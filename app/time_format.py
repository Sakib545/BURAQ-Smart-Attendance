from datetime import datetime


def format_time_12h(value: str | None) -> str:
    """Display stored 24-hour duty time as a friendly 12-hour time."""
    text = str(value or "").strip()
    for pattern in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(text, pattern).strftime("%I:%M %p").lstrip("0")
        except ValueError:
            continue
    return text

