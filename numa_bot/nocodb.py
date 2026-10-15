"""Работа с NocoDB. Все функции при сбое сети или базы бросают NocoError,
чтобы «база недоступна» не путалась с «ничего не найдено»."""
import os
import requests
from dotenv import load_dotenv

load_dotenv(override=True)

NOCODB_URL = os.getenv("NOCODB_URL", "http://100.94.236.55:8080").rstrip("/")
NOCODB_TOKEN = os.getenv("NOCODB_TOKEN", "").strip()
LEADS_TABLE_ID = os.getenv("LEADS_TABLE_ID", "").strip()
USERS_TABLE_ID = os.getenv("USERS_TABLE_ID", "").strip()

HEADERS = {"xc-token": NOCODB_TOKEN, "Content-Type": "application/json"}
TIMEOUT = 10

# Поля, нужные для статистики (без тяжёлых текстовых колонок)
STATS_FIELDS = "Id,status,tier,assigned_manager,deal_amount,won_at,manager_percent"


class NocoError(Exception):
    pass


def _clean(value) -> str:
    """Значение для where-фильтра: убираем символы, которыми NocoDB разбирает условия."""
    for ch in ",()~":
        value = str(value).replace(ch, " ")
    return str(value).strip()


def _records_url(table_id: str) -> str:
    return f"{NOCODB_URL}/api/v2/tables/{table_id}/records"


def _request(method: str, url: str, **kwargs):
    try:
        r = requests.request(method, url, headers=HEADERS, timeout=TIMEOUT, **kwargs)
    except requests.RequestException as e:
        raise NocoError(f"NocoDB недоступна: {e}") from e
    return r


def _check(r, ok=(200, 201, 204)):
    if r.status_code not in ok:
        raise NocoError(f"NocoDB ответила {r.status_code}: {r.text[:200]}")
    return r


def _list(table_id, where=None, fields=None, sort=None, limit=100, offset=0):
    params = {"limit": limit, "offset": offset}
    if where:
        params["where"] = where
    if fields:
        params["fields"] = fields
    if sort:
        params["sort"] = sort
    data = _check(_request("GET", _records_url(table_id), params=params)).json()
    return data.get("list", []), bool((data.get("pageInfo") or {}).get("isLastPage", False))


def _list_all(table_id, where=None, fields=None, sort=None, page=1000, max_rows=50000):
    rows, offset = [], 0
    while len(rows) < max_rows:
        chunk, last = _list(table_id, where, fields, sort, limit=page, offset=offset)
        rows.extend(chunk)
        if last or not chunk:
            break
        offset += len(chunk)
    return rows


# ---------- Пользователи ----------

def get_user_by_tg_id(telegram_id: int):
    rows, _ = _list(USERS_TABLE_ID, where=f"(telegram_id,eq,{_clean(telegram_id)})", limit=1)
    return rows[0] if rows else None


def get_users():
    return _list_all(USERS_TABLE_ID)


def get_admin_ids():
    ids = []
    for u in get_users():
        if u.get("role") == "admin" and u.get("status") == "approved":
            try:
                ids.append(int(u.get("telegram_id")))
            except (TypeError, ValueError):
                pass
    return ids


def create_user_request(telegram_id: int, username: str, full_name: str, role: str = "manager", status: str = "pending"):
    payload = [{
        "telegram_id": telegram_id,
        "username": username or "",
        "full_name": full_name or "Manager",
        "role": role,
        "status": status,
    }]
    _check(_request("POST", _records_url(USERS_TABLE_ID), json=payload))
    return True


def update_user(record_id: int, fields: dict):
    payload = [{"Id": record_id, **fields}]
    _check(_request("PATCH", _records_url(USERS_TABLE_ID), json=payload))
    return True


def update_user_status(record_id: int, status: str):
    return update_user(record_id, {"status": status})


# ---------- Лиды ----------

def get_lead(lead_id: int):
    r = _request("GET", f"{_records_url(LEADS_TABLE_ID)}/{int(lead_id)}")
    if r.status_code == 404:
        return None
    return _check(r).json()


def get_leads_feed(status_filter: str = "New", tier_filter: str = None, limit: int = 30, offset: int = 0):
    where = f"(status,eq,{_clean(status_filter)})"
    if tier_filter:
        where += f"~and(tier,eq,{_clean(tier_filter)})"
    rows, _ = _list(LEADS_TABLE_ID, where=where, sort="-Id", limit=limit, offset=offset)
    return rows


def get_leads_of_manager(tg_id: int, limit: int = 500):
    where = f"(assigned_manager,eq,{_clean(tg_id)})"
    rows, _ = _list(LEADS_TABLE_ID, where=where, sort="-Id", limit=limit)
    return rows


def get_leads_light():
    """Все лиды, только поля для статистики."""
    return _list_all(LEADS_TABLE_ID, fields=STATS_FIELDS)


def update_lead(lead_id: int, fields: dict):
    payload = [{"Id": int(lead_id), **fields}]
    _check(_request("PATCH", _records_url(LEADS_TABLE_ID), json=payload))
    return True
