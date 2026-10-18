"""Очистка и проверка ссылок, юзернеймов и каналов, которые админ вводит в боте."""
import re
from urllib.parse import urlparse

_NAME = r"[A-Za-z][A-Za-z0-9_]{3,31}"


def clean(s) -> str:
    s = re.sub(r"<[^>]+>", "", str(s or ""))          # вырезаем HTML (<a href=...>)
    s = s.replace("\u200b", "").replace("\ufeff", "").replace("\xa0", " ")
    return s.strip().strip("\"'«»<>").strip()


def norm_url(s):
    """Возвращает корректный URL для кнопки или None."""
    s = clean(s)
    if not s:
        return None
    m = re.fullmatch(r"@(" + _NAME + ")", s)
    if m:
        return f"https://t.me/{m.group(1)}"
    if re.match(r"(?i)^(https?|tg)://", s):
        url = s
    elif re.match(r"(?i)^(t\.me|telegram\.me|telegram\.dog)/", s):
        url = "https://" + s
    elif re.match(r"(?i)^[\w-]+(\.[\w-]+)+(/\S*)?$", s):
        url = "https://" + s
    else:
        return None
    if re.search(r"\s", url):
        return None
    p = urlparse(url)
    if p.scheme.lower() == "tg":
        return url if p.netloc else None
    if p.scheme.lower() in ("http", "https") and "." in p.netloc:
        if p.netloc.lower() in ("t.me", "telegram.me") and not p.path.strip("/"):
            return None  # просто t.me/ без канала — это не ссылка на что-то
        return url
    return None


def norm_handle(s):
    """@username менеджера (принимает @name, name, t.me/name, https://t.me/name) или None."""
    s = clean(s)
    s = re.sub(r"(?i)^(https?://)?(t\.me|telegram\.me)/", "", s).lstrip("@").split("?")[0].strip("/")
    return "@" + s if re.fullmatch(_NAME, s) else None


def norm_chat(s):
    """Канал для публикации: @name, -100… ID или ссылка t.me/name. Возвращает строку или None."""
    s = clean(s)
    if re.fullmatch(r"-?\d{5,}", s):
        return s
    return norm_handle(s)


def tme(handle):
    """https://t.me/name из '@name'."""
    return f"https://t.me/{handle.lstrip('@')}" if handle else None
