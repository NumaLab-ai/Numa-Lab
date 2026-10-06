import requests

def search_yandex_leads(query: str, city: str = "Москва", limit: int = 15) -> list:
    """
    Парсер заведений на базе OpenStreetMap (Overpass API) для Numa Studio.
    Отбирает реальные организации с телефонами, сайтами и Telegram.
    """
    tag_map = {
        # Базовые категории
        "салон красоты": '["shop"="beauty"]',
        "парикмахерская": '["shop"="hairdresser"]',
        "ресторан": '["amenity"="restaurant"]',
        "фитнес клуб": '["leisure"="fitness_centre"]',
        "барбершоп": '["shop"="hairdresser"]',
        "стоматология": '["amenity"="dentist"]',
        "автосервис": '["shop"="car_repair"]',
        "автомойка": '["amenity"="car_wash"]',
        "ветклиника": '["amenity"="veterinary"]',
        "косметология": '["shop"="beauty"]',
        "автошкола": '["amenity"="driving_school"]',
        "детский центр": '["education"="educational_centre"]',
        "языковая школа": '["amenity"="language_school"]',
        "медицинский центр": '["amenity"="clinic"]',

        # Расширенные категории
        "тату салон": '["shop"="tattoo"]',
        "массажный салон": '["amenity"="massage"]',
        "студия маникюра": '["shop"="beauty"]',
        "детейлинг": '["shop"="car_repair"]',
        "шиномонтаж": '["shop"="tyres"]',
        "фотостудия": '["amenity"="photo_studio"]',
        "сауна": '["amenity"="sauna"]',
        "студия танцев": '["leisure"="dance_studio"]',
        "ремонт телефонов": '["shop"="electronics_repair"]',
        "клининг": '["shop"="cleaning"]'
    }
    
    osm_tag = tag_map.get(query.lower(), '["amenity"]')
    
    overpass_query = f"""
    [out:json][timeout:25];
    area["name"="{city}"]["admin_level"="4"]->.searchArea;
    (
      node{osm_tag}(area.searchArea);
      way{osm_tag}(area.searchArea);
    );
    out body 40;
    """
    
    headers = {
        "User-Agent": "NumaStudioBot/1.0 (contact@numastudio.local)",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
    }
    
    servers = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter"
    ]
    
    for url in servers:
        try:
            response = requests.post(url, data={"data": overpass_query}, headers=headers, timeout=30)
            if response.status_code == 200:
                data = response.json()
                raw_leads = []
                
                for element in data.get("elements", []):
                    tags = element.get("tags", {})
                    name = tags.get("name")
                    phone = tags.get("phone") or tags.get("contact:phone") or tags.get("contact:mobile") or ""
                    site_url = tags.get("website") or tags.get("contact:website") or ""
                    tg_acc = tags.get("contact:telegram") or tags.get("telegram") or ""
                    
                    if not name or not phone:
                        continue
                        
                    lead = {
                        "company_name": name,
                        "category": query,
                        "city": city,
                        "phone": phone,
                        "whatsapp_url": "",
                        "vk_url": tags.get("contact:vk", ""),
                        "tg_acc": tg_acc,
                        "booking_urls": "",
                        "yandex_url": f"https://yandex.ru/maps/?text={name}",
                        "site_url": site_url
                    }
                    raw_leads.append(lead)
                    if len(raw_leads) >= limit:
                        break
                        
                print(f"[Parser] Найдено заведений с контактами ({query}): {len(raw_leads)}")
                return raw_leads
            else:
                print(f"[OSM Error] Сервер {url} вернул код {response.status_code}")
        except Exception as e:
            print(f"[OSM Exception] Ошибка подключения к {url}: {e}")
            
    return []
