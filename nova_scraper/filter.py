from website_analyzer import analyze_website

# Стоп-слова для отсечения госсектора и бюджетных организаций
STATE_STOP_WORDS = [
    "гбуз", "гауз", "гбу", "мбуз", "фгбу", "фгуп", "государственн", 
    "городская поликлиника", "детская поликлиника", "поликлиника №", 
    "поликлиника n", "больница №", "больница n", "стоматологическая поликлиника №", 
    "стоматологическая поликлиника n", "црб", "департамент", "министерство", 
    "федеральн", "администрация", "центр гигиены"
]

def filter_lead(raw_lead: dict) -> tuple[bool, dict]:
    company_name = raw_lead.get("company_name", "").strip()
    phone = raw_lead.get("phone", "").strip()
    city = raw_lead.get("city", "Москва")
    initial_site_url = raw_lead.get("site_url", "").strip()
    osm_tg_acc = raw_lead.get("tg_acc", "").strip()

    if not company_name:
        return False, {"reason": "Отсутствует название компании"}
    if not phone:
        return False, {"reason": "Отсутствует номер телефона"}

    # Проверка на госкомпании и бюджетные учреждения
    name_lower = company_name.lower()
    for stop_word in STATE_STOP_WORDS:
        if stop_word in name_lower:
            return False, {"reason": f"Госучреждение / Бюджетная организация ('{stop_word}')"}

    # Глубокий анализ сайта
    web_info = analyze_website(company_name=company_name, city=city, site_url=initial_site_url)

    final_tg = web_info.get("tg_acc") or osm_tg_acc

    processed_lead = {
        "company_name": company_name,
        "category": raw_lead.get("category", ""),
        "city": city,
        "phone": phone,
        "yandex_url": raw_lead.get("yandex_url", ""),
        "web_url": web_info["site_url"],
        "tg_acc": final_tg,
        "has_website": web_info["has_website"],
        "site_summary": web_info["site_summary"],
        "has_online_booking": web_info["has_online_booking"],
        "booking_service": web_info["booking_service"]
    }

    return True, processed_lead
