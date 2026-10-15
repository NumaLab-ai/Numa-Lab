import re
import threading
import time
import urllib.parse

import requests
import urllib3

from utils import phone_key

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

# Агрегаторы, соцсети и карты: это не «собственный сайт» компании
BLOCKED_DOMAINS = [
    "yandex", "2gis", "google", "vk.com", "ok.ru", "facebook", "instagram", "duckduckgo", "zoon", "avito",
    "youtube", "t.me", "telegram", "flamp", "tripadvisor", "restoclub", "afisha", "orgpage", "yell.ru",
    "spravker", "cataloxy", "profi.ru", "hh.ru", "wikipedia", "tiktok", "dzen", "rutube", "pulscen",
    "yp.ru", "blizko", "sravni", "prodoctorov", "docdoc", "napopravku", "sberhealth", "ru.wikipedia",
    "restaurantguru", "foursquare", "yelp", "pinterest", "linkedin", "twitter", "x.com", "wildberries",
    "ozon", "market.yandex", "kudago", "mapcarta", "nicelocal", "moscow.rf", "rusprofile", "list-org",
    "checko", "sbis.ru", "audit-it", "e-ecolog", "vsekidki", "birmaster", "cataloxy", "mos.ru",
]

GENERIC_WORDS = {
    "салон", "студия", "клуб", "центр", "клиника", "ресторан", "кафе", "москва", "московский", "школа",
    "сервис", "автосервис", "детский", "медицинский", "фитнес", "красоты", "барбершоп", "маникюр",
    "стоматология", "компания", "магазин", "мастерская", "автомойка", "парикмахерская", "массаж",
    "сауна", "баня", "тату", "студии", "центра", "бар", "пицца", "суши",
}

# Настоящие системы онлайн-записи (мессенджеры сюда не входят)
BOOKING_KEYWORDS = {
    "yclients": "YClients", "dikidi": "DIKIDI", "altegio": "Altegio", "easyweek": "EasyWeek",
    "sonline": "Sonline", "bookform": "Bookform", "restoplace": "Restoplace", "tilda.ws/form": "Tilda-форма",
    "запись онлайн": "Онлайн-запись", "онлайн-запись": "Онлайн-запись", "забронировать онлайн": "Онлайн-бронь",
}

_ddg_lock = threading.Lock()
_ddg_last = [0.0]


def _search_links(query: str):
    """Ссылки из выдачи DuckDuckGo. None — поиск не сработал (лимит, блокировка, сеть)."""
    with _ddg_lock:
        wait = 1.5 - (time.time() - _ddg_last[0])
        if wait > 0:
            time.sleep(wait)
        try:
            resp = requests.get("https://html.duckduckgo.com/html/", params={"q": query}, headers=HEADERS, timeout=8)
        except requests.RequestException as e:
            print(f"[Web Search] Ошибка сети: {e}")
            return None
        finally:
            _ddg_last[0] = time.time()

    if resp.status_code != 200 or "result__a" not in resp.text:
        print(f"[Web Search] Поиск не отдал выдачу (код {resp.status_code})")
        return None

    links = []
    for raw in re.findall(r'uddg=([^&"\']+)', resp.text):
        link = urllib.parse.unquote(raw)
        if link.startswith("http") and link not in links:
            links.append(link)
    return links


def _is_blocked(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return any(domain in host for domain in BLOCKED_DOMAINS)


def _fetch(url: str):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=(4, 7), verify=False)
        return resp.status_code, resp.text[:300000]
    except requests.RequestException:
        return None, ""


def _name_tokens(name: str):
    words = re.findall(r"[a-zа-я0-9]{4,}", name.lower().replace("ё", "е"))
    return [w for w in words if w not in GENERIC_WORDS]


def _belongs_to_company(html: str, name: str, pkey: str) -> bool:
    """Страница действительно принадлежит компании: в начале встречается её название или телефон."""
    head = html[:20000].lower().replace("ё", "е")
    if any(token in head for token in _name_tokens(name)):
        return True
    return bool(pkey) and pkey in re.sub(r"\D", "", html)


def _find_own_site(company_name: str, city: str, phone: str):
    """Возвращает (url, проверка_удалась). url == '' значит собственного сайта не нашли."""
    links = _search_links(f"{company_name} {city} официальный сайт")
    if links is None:
        return "", False
    pkey = phone_key(phone)
    checked = 0
    for link in links:
        if _is_blocked(link):
            continue
        checked += 1
        if checked > 3:
            break
        status, html = _fetch(link)
        if status == 200 and _belongs_to_company(html, company_name, pkey):
            return link, True
    return "", True


def _no_site(check_failed=False):
    return {
        "has_website": False, "site_url": "", "site_summary": "Сайт отсутствует.",
        "has_online_booking": False, "booking_service": "Нет", "tg_acc": "", "check_failed": check_failed,
    }


def analyze_website(company_name: str, city: str, site_url: str = "", phone: str = "") -> dict:
    site_url = (site_url or "").strip()
    if not site_url:
        site_url, ok = _find_own_site(company_name, city, phone)
        if not ok:
            return _no_site(check_failed=True)  # не знаем, есть ли сайт: лучше не гадать
        if not site_url:
            return _no_site()

    target_url = site_url if site_url.startswith("http") else f"https://{site_url}"
    status, html = _fetch(target_url)

    if status != 200:
        note = f"код ответа {status}" if status else "временно недоступен"
        return {
            "has_website": True, "site_url": target_url,
            "site_summary": f"Сайт существует ({target_url}), {note}.",
            "has_online_booking": False, "booking_service": "Не удалось проверить",
            "tg_acc": "", "check_failed": False,
        }

    tg_match = re.search(r"(?:https?:)?(?://)?t\.me/(?!share|joinchat)([a-zA-Z0-9_\+]{4,})", html, re.IGNORECASE)
    tg_acc = f"https://t.me/{tg_match.group(1)}" if tg_match else ""

    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
    desc_match = re.search(r'<meta[^>]*name=["\']description["\'][^>]*content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    meta_desc = desc_match.group(1).strip() if desc_match else ""

    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()

    html_lower = html.lower()
    booking = sorted({name for kw, name in BOOKING_KEYWORDS.items() if kw in html_lower})
    messengers = [n for kw, n in (("wa.me", "WhatsApp"), ("t.me", "Telegram")) if kw in html_lower]
    booking_service = ", ".join(booking) if booking else "Нет онлайн-записи"

    summary = (f"Title: {title} | Description: {meta_desc} | Онлайн-запись: {booking_service} | "
               f"Мессенджеры на сайте: {', '.join(messengers) or 'нет'} | Текст: {text[:300]}...")

    return {
        "has_website": True, "site_url": target_url, "site_summary": summary,
        "has_online_booking": bool(booking), "booking_service": booking_service,
        "tg_acc": tg_acc, "check_failed": False,
    }
