"""Реферальная программа: «приведи друга и получи 10% от его сделок за первый месяц».

Правила (меняются константами ниже):
  - друг приходит по ссылке t.me/<бот>?start=ref_<id приглашающего> и проходит обычное одобрение;
  - 30 дней с момента одобрения друга приглашающий получает 10% от СУММЫ каждой сделки друга;
  - условия фиксируются в сделке в момент её закрытия (referrer_id, referral_percent),
    поэтому позднейшие изменения правил или процентов не переписывают прошлое;
  - бонус платит компания: он вычитается из чистого дохода и не уменьшает процент самого друга.
Модуль не ходит в сеть и легко тестируется.
"""
import math
import re
from datetime import datetime, timedelta, timezone

import stats

REFERRAL_PERCENT = 10.0
REFERRAL_DAYS = 30

_PAYLOAD = re.compile(r"^ref_(\d{1,15})$")


def parse_ref_payload(args):
    """'ref_123456' из /start-ссылки -> 123456; всё остальное -> None."""
    m = _PAYLOAD.match((args or "").strip())
    return int(m.group(1)) if m else None


def window(friend, days=REFERRAL_DAYS):
    """(начало, конец) окна бонуса друга или None, если его ещё не одобрили."""
    start = stats.parse_dt((friend or {}).get("approved_at"))
    return (start, start + timedelta(days=days)) if start else None


def days_left(friend, now=None, days=REFERRAL_DAYS):
    now = now or datetime.now(timezone.utc)
    w = window(friend, days)
    if not w:
        return None
    return max(0, math.ceil((w[1] - now).total_seconds() / 86400))  # неполный день считается за день


def deal_snapshot(owner, referrer, now=None, percent=REFERRAL_PERCENT, days=REFERRAL_DAYS):
    """Поля для записи в закрываемую сделку или {}, если бонус не положен."""
    now = now or datetime.now(timezone.utc)
    if not owner or not referrer:
        return {}
    ref_id = str(owner.get("referrer_id") or "").strip()
    if not ref_id or ref_id == str(owner.get("telegram_id")):
        return {}
    if str(referrer.get("telegram_id")) != ref_id or referrer.get("status") != "approved":
        return {}
    w = window(owner, days)
    if not w or not (w[0] <= now <= w[1]):
        return {}
    return {"referrer_id": ref_id, "referral_percent": float(percent)}


def bonus_of(lead):
    """Бонус приглашающего с одной сделки (0, если сумма не указана или условий нет)."""
    if not str(lead.get("referrer_id") or "").strip() or lead.get("deal_amount") in (None, ""):
        return 0.0
    return stats.num(lead.get("deal_amount")) * stats.num(lead.get("referral_percent")) / 100.0


def summarize(me_id, users, ref_leads, now=None):
    """Данные экрана «Друзья» для приглашающего me_id.

    users: все записи Users; ref_leads: сделки, где referrer_id == me_id.
    """
    now = now or datetime.now(timezone.utc)
    me_id = str(me_id)
    won = [l for l in ref_leads if l.get("status") == "Won" and str(l.get("referrer_id")) == me_id]
    friends = []
    for u in users:
        if str(u.get("referrer_id") or "") != me_id:
            continue
        fid = str(u.get("telegram_id"))
        deals = [l for l in won if str(l.get("assigned_manager")) == fid and bonus_of(l) > 0]
        friends.append({
            "tg_id": fid,
            "name": u.get("full_name") or f"ID {fid}",
            "username": u.get("username") or "",
            "status": u.get("status") or "pending",
            "joined": u.get("approved_at") or "",
            "days_left": days_left(u, now) if u.get("status") == "approved" else None,
            "deals": len(deals),
            "earned": round(sum(bonus_of(l) for l in deals), 2),
        })
    friends.sort(key=lambda f: (-f["earned"], f["name"]))
    return {
        "percent": REFERRAL_PERCENT,
        "days": REFERRAL_DAYS,
        "friends": friends,
        "invited": len(friends),
        "active": sum(1 for f in friends if f["status"] == "approved"),
        "deals": sum(f["deals"] for f in friends),
        "earned": round(sum(f["earned"] for f in friends), 2),
    }
