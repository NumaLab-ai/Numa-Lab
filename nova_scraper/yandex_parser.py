"""Поиск заведений в OpenStreetMap (Overpass API).

Файл называется yandex_parser.py по традиции, внутри только OSM.
Главные отличия от первой версии:
  * ищем по одному округу Москвы за запуск (main.py крутит округа по кругу),
    поэтому каждый запуск даёт новые компании, а не те же первые 40;
  * телефон и название требуем прямо в запросе: сервер не шлёт нам лишнего;
  * по категории можно задать несколько OSM-тегов, чтобы не терять заведения.
"""
import time
import urllib.parse

import requests

from utils import format_phone

DISTRICTS = [
    "Центральный административный округ",
    "Северный административный округ",
    "Северо-Восточный административный округ",
    "Восточный административный округ",
    "Юго-Восточный административный округ",
    "Южный административный округ",
    "Юго-Западный административный округ",
    "Западный административный округ",
    "Северо-Западный административный округ",
    "Зеленоградский административный округ",
    "Новомосковский административный округ",
    "Троицкий административный округ",
]

# категория -> список OSM-фильтров (объединяются через ИЛИ)
CATEGORY_TAGS = {
    "салон красоты": ['["shop"="beauty"]'],
    "парикмахерская": ['["shop"="hairdresser"]'],
    "ресторан": ['["amenity"="restaurant"]'],
    "фитнес клуб": ['["leisure"="fitness_centre"]', '["leisure"="sports_centre"]["sport"="fitness"]'],
    "барбершоп": ['["shop"="hairdresser"]["name"~"барбер|barber",i]'],
    "стоматология": ['["amenity"="dentist"]', '["healthcare"="dentist"]'],
    "автосервис": ['["shop"="car_repair"]'],
    "автомойка": ['["amenity"="car_wash"]'],
    "ветклиника": ['["amenity"="veterinary"]'],
    "косметология": ['["shop"="beauty"]["name"~"косметолог|cosmetolog",i]'],
    "автошкола": ['["amenity"="driving_school"]'],
    "детский центр": ['["education"="educational_centre"]', '["amenity"="childcare"]', '["amenity"="kindergarten"]'],
    "языковая школа": ['["amenity"="language_school"]'],
    "медицинский центр": ['["amenity"="clinic"]', '["healthcare"="clinic"]'],
    "тату салон": ['["shop"="tattoo"]'],
    "массажный салон": ['["shop"="massage"]', '["amenity"="massage"]'],
    "студия маникюра": ['["shop"="beauty"]["name"~"маникюр|ногт|nail",i]'],
    "детейлинг": ['["shop"="car_repair"]["name"~"детейлинг|detailing|полиров|керамик",i]'],
    "шиномонтаж": ['["shop"="tyres"]'],
    "фотостудия": ['["amenity"="photo_studio"]', '["shop"="photo"]', '["craft"="photographer"]'],
    "сауна": ['["leisure"="sauna"]', '["amenity"="sauna"]'],
    "студия танцев": ['["leisure"="dance"]', '["amenity"="dancing_school"]', '["leisure"="dance_studio"]'],
    "ремонт телефонов": ['["shop"="electronics_repair"]', '["craft"="electronics_repair"]'],
    "клининг": ['["shop"="cleaning"]', '["craft"="cleaning"]'],
}
CATEGORIES = list(CATEGORY_TAGS)

SERVERS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
HEADERS = {
    "User-Agent": "NumaStudioBot/1.1 (lead research; contact via numa-studio)",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}
PHONE_KEYS = '[~"^(phone|contact:phone|contact:mobile|mobile)$"~"."]'


def build_query(tags: list, district: str, max_items: int) -> str:
    unions = "\n".join(f'  nwr{tag}["name"]{PHONE_KEYS}(area.a);' for tag in tags)
    return (
        "[out:json][timeout:60];\n"
        f'area["name"="{district}"]["boundary"="administrative"]->.a;\n'
        f"(\n{unions}\n);\n"
        f"out tags center {max_items};"
    )


def parse_elements(data: dict, category: str, city: str = "Москва") -> list:
    leads = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        name = (tags.get("name") or "").strip()
        raw_phone = tags.get("phone") or tags.get("contact:phone") or tags.get("contact:mobile") or tags.get("mobile") or ""
        phone = format_phone(raw_phone)
        if not name or not phone:
            continue

        point = el if "lat" in el else (el.get("center") or {})
        if "lat" in point and "lon" in point:
            yandex_url = (f"https://yandex.ru/maps/?ll={point['lon']}%2C{point['lat']}&z=18"
                          f"&text={urllib.parse.quote(name)}")
        else:
            yandex_url = f"https://yandex.ru/maps/?text={urllib.parse.quote(name + ' ' + city)}"

        leads.append({
            "company_name": name,
            "category": category,
            "city": city,
            "phone": phone,
            "tg_acc": tags.get("contact:telegram") or tags.get("telegram") or "",
            "vk_url": tags.get("contact:vk") or "",
            "site_url": tags.get("website") or tags.get("contact:website") or tags.get("url") or "",
            "yandex_url": yandex_url,
            "is_chain": bool(tags.get("brand") or tags.get("brand:wikidata") or tags.get("network")),
            "osm_id": f"{el.get('type', '')}/{el.get('id', '')}",
        })
    return leads


def fetch_candidates(category: str, district: str, max_items: int = 1000):
    """Возвращает список заведений или None, если все серверы Overpass недоступны."""
    tags = CATEGORY_TAGS.get(category.lower())
    if not tags:
        print(f"[Parser] Неизвестная категория: {category}")
        return []
    query = build_query(tags, district, max_items)

    for url in SERVERS:
        try:
            resp = requests.post(url, data={"data": query}, headers=HEADERS, timeout=90)
        except requests.RequestException as e:
            print(f"[OSM] {url}: {e}")
            continue
        if resp.status_code == 200:
            try:
                return parse_elements(resp.json(), category)
            except ValueError:
                print(f"[OSM] {url}: ответ не JSON (скорее всего сервер перегружен)")
                continue
        print(f"[OSM] {url} вернул {resp.status_code}")
        if resp.status_code in (429, 504):
            time.sleep(10)
    return None
