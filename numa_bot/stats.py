"""Подсчёт статистики по лидам и менеджерам (чистые функции, без обращений к сети)."""
from datetime import datetime, timedelta, timezone

DEFAULT_COMMISSION = 20.0
TAKEN_STATUSES = ("In_Progress", "Won", "Lost", "Postponed")
PERIOD_DAYS = {"week": 7, "month": 30}


def num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_dt(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def user_percent(user, default=DEFAULT_COMMISSION):
    value = (user or {}).get("commission_percent")
    return num(value, default) if value not in (None, "") else default


def lead_percent(lead, user, default=DEFAULT_COMMISSION):
    """Процент, зафиксированный в сделке; если нет, то текущий процент менеджера."""
    snap = lead.get("manager_percent")
    if snap not in (None, ""):
        return num(snap, default)
    return user_percent(user, default)


def _empty_row(tg_id, user):
    return {
        "tg_id": str(tg_id),
        "name": (user or {}).get("full_name") or f"ID {tg_id}",
        "username": (user or {}).get("username") or "",
        "status": (user or {}).get("status") or "unknown",
        "role": (user or {}).get("role") or "manager",
        "percent": user_percent(user),
        "taken": 0, "in_progress": 0, "postponed": 0, "lost": 0, "won": 0,
        "pending_amount": 0, "paid_deals": 0,
        "revenue": 0.0, "payout": 0.0, "net": 0.0, "conversion": 0.0,
    }


def compute_stats(leads, users, period="all", now=None, default_percent=DEFAULT_COMMISSION):
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=PERIOD_DAYS[period]) if period in PERIOD_DAYS else None

    users_by_id = {str(u.get("telegram_id")): u for u in users if u.get("telegram_id") not in (None, "")}
    rows = {tg_id: _empty_row(tg_id, u) for tg_id, u in users_by_id.items()}

    totals = {
        "leads_total": len(leads),
        "by_status": {},
        "pool_by_tier": {},
        "revenue": 0.0, "payout": 0.0, "net": 0.0,
        "paid_deals": 0, "pending_amount": 0, "won": 0, "taken": 0, "conversion": 0.0,
    }

    for lead in leads:
        status = lead.get("status") or "New"
        totals["by_status"][status] = totals["by_status"].get(status, 0) + 1
        if status == "New":
            tier = lead.get("tier") or "?"
            totals["pool_by_tier"][tier] = totals["pool_by_tier"].get(tier, 0) + 1
            continue
        if status not in TAKEN_STATUSES:
            continue

        owner = str(lead.get("assigned_manager") or "")
        row = rows.get(owner)
        if row is None and owner:
            row = rows[owner] = _empty_row(owner, None)
        totals["taken"] += 1
        if row is not None:
            row["taken"] += 1
            key = {"In_Progress": "in_progress", "Postponed": "postponed", "Lost": "lost", "Won": "won"}[status]
            row[key] += 1
        if status == "Won":
            totals["won"] += 1

        if status != "Won":
            continue

        amount = lead.get("deal_amount")
        if amount in (None, ""):
            totals["pending_amount"] += 1
            if row is not None:
                row["pending_amount"] += 1
            continue

        if since is not None:
            won_at = parse_dt(lead.get("won_at"))
            if won_at is None or won_at < since:
                continue

        amount = num(amount)
        percent = lead_percent(lead, users_by_id.get(owner), default_percent)
        payout = amount * percent / 100.0
        totals["revenue"] += amount
        totals["payout"] += payout
        totals["paid_deals"] += 1
        if row is not None:
            row["revenue"] += amount
            row["payout"] += payout
            row["paid_deals"] += 1

    totals["net"] = totals["revenue"] - totals["payout"]
    totals["conversion"] = round(100.0 * totals["won"] / totals["taken"], 1) if totals["taken"] else 0.0

    for row in rows.values():
        row["net"] = row["revenue"] - row["payout"]
        row["conversion"] = round(100.0 * row["won"] / row["taken"], 1) if row["taken"] else 0.0
        for key in ("revenue", "payout", "net"):
            row[key] = round(row[key], 2)

    for key in ("revenue", "payout", "net"):
        totals[key] = round(totals[key], 2)

    managers = sorted(rows.values(), key=lambda r: (-r["revenue"], -r["won"], -r["taken"], r["name"]))
    return {"period": period, "totals": totals, "managers": managers}
