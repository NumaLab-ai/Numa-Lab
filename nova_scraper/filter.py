from website_analyzer import analyze_website

# Госсектор и бюджетные организации
STATE_STOP_WORDS = [
    "гбуз", "гауз", "гбу", "мбуз", "фгбу", "фгуп", "государственн",
    "городская поликлиника", "детская поликлиника", "поликлиника №",
    "поликлиника n", "больница №", "больница n", "стоматологическая поликлиника №",
    "стоматологическая поликлиника n", "црб", "департамент", "министерство",
    "федеральн", "администрация", "центр гигиены", "муниципальн", "гимназия", "школа №",
    "детский сад №", "дворец культуры", "дши ",
]

# Крупные сети и франшизы: им бот Numa Studio не нужен, а ИИ на них тратить деньги незачем
CHAIN_WORDS = [
    # фитнес
    "world class", "worldclass", "спортлайф", "sportlife", "sport life", "x-fit", "xfit", "alex fitness",
    "fitness house", "фитнес хаус", "pride club", "crocus fitness",
    # еда
    "тануки", "додо", "макдоналдс", "mcdonald", "burger king", "бургер кинг", "kfc", "subway", "starbucks",
    "шоколадница", "кофемания", "иль патио", "il patio", "якитория", "му-му", "теремок", "вкусно — и точка",
    "вкусно и точка", "papa john", "папа джонс", "суши вок", "sushi wok", "кофе хаус", "coffeeshop company",
    "крошка картошка", "stars coffee", "до-до", "ростикс", "rostic", "чайхона", "дринкит",
    # медицина и стоматология
    "медси", "инвитро", "invitro", "хеликс", "helix", "ниармедик", "k+31", "к+31", "ситилаб", "гемотест",
    "cmd ", "ситидент", "citident", "мать и дитя", "бест клиник", "клиника семейная", "smart clinic",
    # авто
    "рольф", "rolf", "автомир", "ключавто", "авилон", "fit service", "фит сервис", "bosch car", "колёса даром",
    "колеса даром", "шинсервис", "kolesa",
    # красота и прочее
    "l'etoile", "летуаль", "sunlight", "ольфактория", "dessange", "persona lab", "персона лаб",
    "skyeng", "wall street", "инглекс", "english first", "gofit",
]


def quick_reject(raw: dict):
    """Дешёвая проверка по названию и данным OSM. Возвращает причину отказа или None."""
    name = (raw.get("company_name") or "").strip()
    if not name:
        return "Отсутствует название компании"
    if not (raw.get("phone") or "").strip():
        return "Отсутствует номер телефона"
    low = name.lower()
    for word in STATE_STOP_WORDS:
        if word in low:
            return f"Госучреждение / бюджетная организация ('{word.strip()}')"
    if raw.get("is_chain"):
        return "Сеть (в OSM указан brand)"
    for word in CHAIN_WORDS:
        if word in low:
            return f"Крупная сеть ('{word.strip()}')"
    return None


def filter_lead(raw_lead: dict) -> tuple[bool, dict]:
    """Возвращает (прошёл, лид) или (False, {"reason", "permanent"}).
    permanent=False означает временный сбой: лид не запоминаем как отклонённый и попробуем позже."""
    reason = quick_reject(raw_lead)
    if reason:
        return False, {"reason": reason, "permanent": True}

    company_name = raw_lead["company_name"].strip()
    phone = raw_lead["phone"].strip()
    city = raw_lead.get("city", "Москва")
    osm_tg_acc = (raw_lead.get("tg_acc") or "").strip()

    web_info = analyze_website(
        company_name=company_name,
        city=city,
        site_url=(raw_lead.get("site_url") or "").strip(),
        phone=phone,
    )
    if web_info.get("check_failed"):
        return False, {"reason": "Не удалось проверить наличие сайта (поиск недоступен)", "permanent": False}

    processed_lead = {
        "company_name": company_name,
        "category": raw_lead.get("category", ""),
        "city": city,
        "phone": phone,
        "yandex_url": raw_lead.get("yandex_url", ""),
        "web_url": web_info["site_url"],
        "tg_acc": web_info.get("tg_acc") or osm_tg_acc,
        "has_website": web_info["has_website"],
        "site_summary": web_info["site_summary"],
        "has_online_booking": web_info["has_online_booking"],
        "booking_service": web_info["booking_service"],
    }
    return True, processed_lead
