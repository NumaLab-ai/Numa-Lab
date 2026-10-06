import os
import requests
from dotenv import load_dotenv

load_dotenv(override=True)

NOCODB_URL = os.getenv("NOCODB_URL", "http://100.94.236.55:8080").rstrip("/")
NOCODB_TOKEN = os.getenv("NOCODB_TOKEN", "").strip()
LEADS_TABLE_ID = os.getenv("LEADS_TABLE_ID", "").strip()
USERS_TABLE_ID = os.getenv("USERS_TABLE_ID", "").strip()

HEADERS = {
    "xc-token": NOCODB_TOKEN,
    "Content-Type": "application/json"
}

def get_user_by_tg_id(telegram_id: int):
    url = f"{NOCODB_URL}/api/v2/tables/{USERS_TABLE_ID}/records"
    params = {"where": f"(telegram_id,eq,{telegram_id})", "limit": 1}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=5)
        if r.status_code == 200:
            lst = r.json().get("list", [])
            return lst[0] if lst else None
    except Exception as e:
        print(f"[NocoDB User Error] {e}")
    return None

def create_user_request(telegram_id: int, username: str, full_name: str, role: str = "manager", status: str = "pending"):
    url = f"{NOCODB_URL}/api/v2/tables/{USERS_TABLE_ID}/records"
    payload = [{
        "telegram_id": telegram_id,
        "username": username or "",
        "full_name": full_name or "Manager",
        "role": role,
        "status": status
    }]
    try:
        r = requests.post(url, headers=HEADERS, json=payload, timeout=5)
        return r.status_code in [200, 201]
    except Exception as e:
        print(f"[NocoDB Create User Error] {e}")
        return False

def update_user_status(record_id: int, status: str):
    url = f"{NOCODB_URL}/api/v2/tables/{USERS_TABLE_ID}/records"
    payload = [{"Id": record_id, "status": status}]
    try:
        r = requests.patch(url, headers=HEADERS, json=payload, timeout=5)
        return r.status_code in [200, 204]
    except Exception as e:
        print(f"[NocoDB Update User Error] {e}")
        return False

def get_leads_feed(status_filter: str = "New", tier_filter: str = None, limit: int = 20):
    url = f"{NOCODB_URL}/api/v2/tables/{LEADS_TABLE_ID}/records"
    where_clause = f"(status,eq,{status_filter})"
    if tier_filter:
        where_clause += f"~and(tier,eq,{tier_filter})"
        
    params = {"where": where_clause, "limit": limit, "sort": "-Id"}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=5)
        if r.status_code == 200:
            return r.json().get("list", [])
    except Exception as e:
        print(f"[NocoDB Leads Error] {e}")
    return []

def update_lead_status(lead_id: int, new_status: str, manager_tg_id: int, comment: str = None):
    url = f"{NOCODB_URL}/api/v2/tables/{LEADS_TABLE_ID}/records"
    payload = {
        "Id": lead_id,
        "status": new_status,
        "assigned_manager": str(manager_tg_id)
    }
    if comment:
        payload["last_comment"] = comment
        
    try:
        r = requests.patch(url, headers=HEADERS, json=[payload], timeout=5)
        return r.status_code in [200, 204]
    except Exception as e:
        print(f"[NocoDB Patch Lead Error] {e}")
        return False
