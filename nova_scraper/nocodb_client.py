import os
import time

import requests
from dotenv import load_dotenv

from utils import norm_name, phone_keys, site_key

load_dotenv(override=True)

NOCODB_URL = os.getenv("NOCODB_URL", "http://localhost:8080").strip().rstrip("/")
NOCODB_TABLE_ID = os.getenv("NOCODB_TABLE_ID", "motbvyatc3zwyu3").strip()

# Только эти поля уходят в таблицу Leads (служебные поля парсера туда не попадают)
LEAD_FIELDS = (
    "company_name", "category", "city", "phone", "yandex_url", "web_url", "tg_acc", "has_website",
    "site_summary", "has_online_booking", "booking_service", "tier", "pain_points", "ai_offer", "status",
)


def _headers():
    return {"xc-token": os.getenv("NOCODB_TOKEN", "").strip(), "Content-Type": "application/json"}


def get_existing_leads_keys():
    """Нормализованные названия (и ключи сайтов «site:...») и все ключи телефонов лидов в базе — защита от дублей.
    Возвращает (names, phone_keys, ok). ok=False, если базу прочитать не удалось."""
    if not os.getenv("NOCODB_TOKEN", "").strip():
        return set(), set(), False

    url = f"{NOCODB_URL}/api/v2/tables/{NOCODB_TABLE_ID}/records"
    names, phones, offset = set(), set(), 0
    try:
        while True:
            params = {"limit": 1000, "offset": offset, "fields": "company_name,phone,web_url"}
            resp = requests.get(url, headers=_headers(), params=params, timeout=20)
            if resp.status_code != 200:
                print(f"[NocoDB Cache] Код {resp.status_code}")
                return names, phones, False
            data = resp.json()
            rows = data.get("list", [])
            for r in rows:
                name = norm_name(r.get("company_name"))
                if name:
                    names.add(name)
                site = site_key(r.get("web_url"))
                if site:
                    names.add(site)
                phones.update(phone_keys(r.get("phone")))
            if (data.get("pageInfo") or {}).get("isLastPage", True) or not rows:
                break
            offset += len(rows)
    except requests.RequestException as e:
        print(f"[NocoDB Cache Error] {e}")
        return names, phones, False

    print(f"[NocoDB Cache] В базе уже {len(names)} лидов (для проверки дублей).")
    return names, phones, True


def send_lead_to_nocodb(lead_data: dict) -> bool:
    if not os.getenv("NOCODB_TOKEN", "").strip():
        print("[Error] NOCODB_TOKEN не найден!")
        return False

    url = f"{NOCODB_URL}/api/v2/tables/{NOCODB_TABLE_ID}/records"
    payload = {k: lead_data.get(k) for k in LEAD_FIELDS if k in lead_data}

    for attempt in range(3):
        try:
            response = requests.post(url, headers=_headers(), json=payload, timeout=15)
            if response.status_code in (200, 201):
                print(f"[Успех] Лид '{lead_data.get('company_name')}' добавлен в NocoDB (Tier {lead_data.get('tier')})")
                return True
            print(f"[NocoDB Error] Код: {response.status_code}, Ответ: {response.text[:200]}")
            if response.status_code < 500:
                return False
        except requests.RequestException as e:
            print(f"[NocoDB Exception] {e}")
        time.sleep(2 * (attempt + 1))
    return False


def count_new_leads():
    """Сколько лидов со статусом New (свободный пул). None, если база не ответила."""
    url = f"{NOCODB_URL}/api/v2/tables/{NOCODB_TABLE_ID}/records"
    try:
        resp = requests.get(url, headers=_headers(), params={"where": "(status,eq,New)", "limit": 1, "fields": "Id"}, timeout=15)
        if resp.status_code == 200:
            total = (resp.json().get("pageInfo") or {}).get("totalRows")
            return int(total) if total is not None else None
    except (requests.RequestException, ValueError):
        pass
    return None
