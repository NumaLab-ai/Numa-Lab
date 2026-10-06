import requests
import re
import urllib.parse
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def analyze_website(company_name: str, city: str, site_url: str = "") -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    # 1. Если сайта нет от OSM — ищем официальный сайт в DuckDuckGo
    if not site_url or not site_url.strip():
        try:
            query = f"{company_name} {city} официальный сайт"
            search_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
            resp = requests.get(search_url, headers=headers, timeout=5)
            if resp.status_code == 200:
                links = re.findall(r'class="result__url"[^>]*href="([^"]+)"', resp.text)
                if not links:
                    links = re.findall(r'href="(https?://[^"]+)"', resp.text)
                
                for link in links:
                    if not any(domain in link.lower() for domain in ["yandex", "2gis", "vk.com", "duckduckgo", "zoon", "avito", "youtube", "telegram"]):
                        if "uddg=" in link:
                            match = re.search(r'uddg=([^&]+)', link)
                            if match:
                                link = urllib.parse.unquote(match.group(1))
                        site_url = link
                        break
        except Exception as e:
            print(f"[Web Search] Ошибка поиска сайта: {e}")

    site_url = (site_url or "").strip()
    if not site_url:
        return {
            "has_website": False,
            "site_url": "",
            "site_summary": "Сайт отсутствует.",
            "has_online_booking": False,
            "booking_service": "Нет",
            "tg_acc": ""
        }

    target_url = site_url if site_url.startswith("http") else f"https://{site_url}"

    # 2. Загрузка и анализ страницы сайта
    try:
        resp = requests.get(target_url, headers=headers, timeout=6, verify=False)
        if resp.status_code == 200:
            html = resp.text
            
            # Поиск Telegram-аккаунта / канала на сайте
            tg_match = re.search(r'https?://t\.me/([a-zA-Z0-9_\+]+)', html, re.IGNORECASE)
            tg_acc = tg_match.group(0) if tg_match else ""

            title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
            title = title_match.group(1).strip() if title_match else ""

            desc_match = re.search(r'<meta[^>]*name=["\']description["\'][^>]*content=["\']([^"\']+)["\']', html, re.IGNORECASE)
            meta_desc = desc_match.group(1).strip() if desc_match else ""

            clean_text = re.sub(r'<[^>]+>', ' ', html)
            clean_text = re.sub(r'\s+', ' ', clean_text).strip()[:1000]

            html_lower = html.lower()
            booking_keywords = {
                "yclients": "YClients",
                "dikidi": "DIKIDI",
                "altegio": "Altegio",
                "easyweek": "EasyWeek",
                "1c": "1C-Битрикс",
                "goplaces": "GoPlaces",
                "wa.me": "WhatsApp",
                "t.me": "Telegram"
            }

            found_services = [name for kw, name in booking_keywords.items() if kw in html_lower]
            has_booking = len(found_services) > 0
            booking_service = ", ".join(found_services) if found_services else "Нет онлайн-записи"

            summary = f"Title: {title} | Description: {meta_desc} | Системы: {booking_service} | Текст: {clean_text[:300]}..."

            return {
                "has_website": True,
                "site_url": target_url,
                "site_summary": summary,
                "has_online_booking": has_booking,
                "booking_service": booking_service,
                "tg_acc": tg_acc
            }
        else:
            return {
                "has_website": True,
                "site_url": target_url,
                "site_summary": f"Сайт существует ({target_url}), код ответа {resp.status_code}.",
                "has_online_booking": False,
                "booking_service": "Неизвестно",
                "tg_acc": ""
            }
    except Exception as e:
        return {
            "has_website": True,
            "site_url": target_url,
            "site_summary": f"Сайт существует ({target_url}), но временно недоступен.",
            "has_online_booking": False,
            "booking_service": "Не удалось проверить",
            "tg_acc": ""
        }
