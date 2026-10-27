"""Общие мелочи для парсера: нормализация телефонов."""
import re


def phone_key(raw) -> str:
    """Ключ для дедупликации: 10 цифр российского номера без кода страны.
    Из строки с несколькими номерами берётся первый подходящий. Пустая строка — номера нет."""
    for part in re.split(r"[;,/]| или ", str(raw or "")):
        digits = re.sub(r"\D", "", part)
        if len(digits) == 11 and digits[0] in "78":
            return digits[1:]
        if len(digits) == 10:
            return digits
    return ""


def format_phone(raw) -> str:
    key = phone_key(raw)
    if not key:
        return ""
    return f"+7 {key[:3]} {key[3:6]}-{key[6:8]}-{key[8:]}"


_LEGAL = {"ооо", "ип", "зао", "оао", "пао", "ао", "нко", "тоо"}
# общие площадки: один адрес у многих компаний, по нему дубли не ищем
SHARED_HOSTS = {"vk.com", "vk.ru", "ok.ru", "instagram.com", "facebook.com", "t.me", "telegram.me", "wa.me", "youtube.com",
                "tiktok.com", "yandex.ru", "yandex.com", "2gis.ru", "google.com", "taplink.cc", "linktr.ee", "tilda.ws",
                "wixsite.com", "github.io", "business.site", "clients.site", "flamp.ru", "zoon.ru", "avito.ru"}


def norm_name(raw) -> str:
    """Название для сравнения: без регистра, кавычек, «ё», форм собственности (ООО, ИП) и лишних пробелов."""
    text = str(raw or "").lower().replace("ё", "е")
    text = re.sub(r"[^\w\s]", " ", text)
    words = [w for w in text.split() if w not in _LEGAL]
    return " ".join(words)


def phone_keys(raw) -> list:
    """Все российские номера из строки (а не только первый), без повторов."""
    keys = []
    for part in re.split(r"[;,/]| или ", str(raw or "")):
        digits = re.sub(r"\D", "", part)
        key = digits[1:] if len(digits) == 11 and digits[0] in "78" else digits if len(digits) == 10 else ""
        if key and key not in keys:
            keys.append(key)
    return keys


def site_key(url) -> str:
    """Ключ сайта вида «site:example.ru» (без www и пути). Пусто для соцсетей и общих площадок."""
    text = str(url or "").strip().lower()
    if not text:
        return ""
    host = re.sub(r"^[a-z]+://", "", text).split("/")[0].split("?")[0].split(":")[0]
    host = host[4:] if host.startswith("www.") else host
    if "." not in host or any(host == h or host.endswith("." + h) for h in SHARED_HOSTS):
        return ""
    return "site:" + host
