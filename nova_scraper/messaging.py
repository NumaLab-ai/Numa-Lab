"""Сообщения между участниками через бота: конкретному человеку, админам или всем сразу.

Правила:
  - администратор может написать любому одобренному участнику и всем сразу;
  - менеджер может написать конкретному коллеге и админам; рассылка «всем» для менеджеров
    выключена (включается переменной ALLOW_MANAGER_BROADCAST=1), чтобы не было спама;
  - не больше 10 сообщений в минуту на человека, текст до 1000 символов.
Модуль не ходит в сеть: получателей он только отбирает, отправляет main.py.
"""
import time

from service import ServiceError

MAX_TEXT = 1000
RATE_LIMIT = 10
RATE_WINDOW = 60


class RateLimiter:
    def __init__(self, limit=RATE_LIMIT, window=RATE_WINDOW):
        self.limit, self.window, self.hits = limit, window, {}

    def allow(self, key, now=None):
        now = time.time() if now is None else now
        recent = [t for t in self.hits.get(key, []) if now - t < self.window]
        if len(recent) >= self.limit:
            self.hits[key] = recent
            return False
        recent.append(now)
        self.hits[key] = recent
        return True


def is_admin_user(user, admin_usernames):
    return user.get("role") == "admin" or (user.get("username") or "").lower() in admin_usernames


def _person(u):
    return {"tg_id": str(u.get("telegram_id")), "name": u.get("full_name") or f"ID {u.get('telegram_id')}",
            "username": u.get("username") or "", "role": u.get("role") or "manager"}


def audience(me, users):
    """Одобренные участники, кроме самого отправителя."""
    me_id = str(me["tg_id"])
    return [u for u in users if u.get("status") == "approved" and u.get("telegram_id") not in (None, "")
            and str(u.get("telegram_id")) != me_id]


def can_broadcast(me, allow_manager_broadcast=False):
    return bool(me.get("is_admin") or allow_manager_broadcast)


def recipients_view(me, users, admin_usernames, allow_manager_broadcast=False):
    people = [_person(u) for u in audience(me, users)]
    people.sort(key=lambda p: (p["role"] != "admin", p["name"].lower()))
    return {
        "can_broadcast": can_broadcast(me, allow_manager_broadcast),
        "has_admins": any(is_admin_user(u, admin_usernames) for u in audience(me, users)),
        "people": people,
    }


def plan_message(me, to, text, users, admin_usernames, limiter=None, allow_manager_broadcast=False):
    """Возвращает (получатели, нормализованный текст) или бросает ServiceError."""
    text = (text or "").strip()
    if not text:
        raise ServiceError(400, "Напишите текст сообщения")
    if len(text) > MAX_TEXT:
        raise ServiceError(400, f"Сообщение слишком длинное (максимум {MAX_TEXT} символов)")
    if limiter is not None and not limiter.allow(str(me["tg_id"])):
        raise ServiceError(429, "Слишком много сообщений, подождите минуту")

    pool = audience(me, users)
    to = str(to).strip()
    if to == "all":
        if not can_broadcast(me, allow_manager_broadcast):
            raise ServiceError(403, "Рассылка всем доступна только администратору")
        targets = pool
    elif to == "admins":
        targets = [u for u in pool if is_admin_user(u, admin_usernames)]
    else:
        targets = [u for u in pool if str(u.get("telegram_id")) == to]
    if not targets:
        raise ServiceError(404, "Получатель не найден или ещё не одобрен")
    return [_person(u) for u in targets], text


def format_message(me, text):
    who = me["name"] + (f" (@{me['username']})" if me.get("username") else "")
    return f"✉️ {who}:\n\n{text}"
