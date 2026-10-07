import re
import unicodedata

_SPACE = re.compile(r"\s+")


def norm(text: str | None) -> str:
    if not text:
        return ""
    return _SPACE.sub(" ", unicodedata.normalize("NFC", str(text))).strip().casefold()


def norm_int(value) -> int | None:
    """Track/disc tags arrive as 3, "3", "03" or "3/12"."""
    if value is None:
        return None
    head = str(value).split("/")[0].strip()
    return int(head) if head.isdigit() else None
