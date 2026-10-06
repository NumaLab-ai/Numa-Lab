import os
import requests
from dotenv import load_dotenv

load_dotenv(override=True)

NOCODB_URL = os.getenv("NOCODB_URL", "http://localhost:8080").strip()
NOCODB_TABLE_ID = os.getenv("NOCODB_TABLE_ID", "motbvyatc3zwyu3").strip()

def get_existing_leads_keys() -> tuple[set, set]:
    """Выгружает существующие названия компаний и телефоны из NocoDB для защиты от дублей."""
    token = os.getenv("NOCODB_TOKEN", "").strip()
    if not token:
        return set(), set()

    url = f"{NOCODB_URL}/api/v2/tables/{NOCODB_TABLE_ID}/records"
    headers = {"xc-token": token}
    params = {"limit": 1000, "fields": "company_name,phone"}

    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            records = data.get("list", [])
            
            existing_names = {str(r.get("company_name", "")).strip().lower() for r in records if r.get("company_name")}
            existing_phones = {str(r.get("phone", "")).strip() for r in records if r.get("phone")}
            
            print(f"[NocoDB Cache] Загружено из базы {len(records)} лидов для проверки дублей.")
            return existing_names, existing_phones
    except Exception as e:
        print(f"[NocoDB Cache Error] Ошибка проверки дубликатов: {e}")

    return set(), set()

def send_lead_to_nocodb(lead_data: dict) -> bool:
    token = os.getenv("NOCODB_TOKEN", "").strip()
    if not token:
        print("[Error] NOCODB_TOKEN не найден!")
        return False

    url = f"{NOCODB_URL}/api/v2/tables/{NOCODB_TABLE_ID}/records"
    headers = {
        "xc-token": token,
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(url, headers=headers, json=lead_data, timeout=10)
        if response.status_code in [200, 201]:
            print(f"[Успех] Лид '{lead_data.get('company_name')}' успешно добавлен в NocoDB!")
            return True
        else:
            print(f"[NocoDB Error] Код: {response.status_code}, Ответ: {response.text}")
            return False
    except Exception as e:
        print(f"[NocoDB Exception] Ошибка подключения: {e}")
        return False
