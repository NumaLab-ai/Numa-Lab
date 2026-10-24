"""Бизнес-правила: кто что может менять и как считаются сделки.
Модуль не ходит в сеть сам, поэтому легко тестируется."""
from datetime import datetime, timezone

import stats

POOL_TIERS = ("A", "B", "C")
LEAD_STATUSES = ("In_Progress", "Postponed", "Lost", "Won")
USER_STATUSES = ("approved", "rejected", "banned", "pending")
PERIODS = ("all", "week", "month")
MAX_AMOUNT = 1_000_000_000
MAX_COMMENT = 500

USER_STATUS_MESSAGES = {
    "approved": "🎉 Доступ к Numa Partners открыт! Нажмите /start, чтобы войти.",
    "rejected": "❌ К сожалению, ваша заявка отклонена.",
    "banned": "⛔ Доступ к Numa Partners закрыт администратором.",
    "pending": None,
}


class ServiceError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def parse_amount(value):
    """Сумма сделки в рублях: число > 0 и не больше лимита. None, если не указана."""
    if value is None or str(value).strip() == "":
        return None
    try:
        amount = float(str(value).replace(" ", "").replace(",", "."))
    except ValueError:
        raise ServiceError(400, "Сумма должна быть числом")
    if not (0 < amount <= MAX_AMOUNT):
        raise ServiceError(400, "Сумма вне допустимого диапазона")
    return round(amount, 2)


def parse_percent(value):
    try:
        percent = float(str(value).replace(",", "."))
    except ValueError:
        raise ServiceError(400, "Процент должен быть числом")
    if not (0 <= percent <= 100):
        raise ServiceError(400, "Процент должен быть от 0 до 100")
    return round(percent, 2)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def plan_lead_update(lead: dict, me: dict, status: str, comment, amount, percent_of, referral_of=None) -> dict:
    """Возвращает поля для записи в лид или бросает ServiceError.

    me: {"tg_id": int, "is_admin": bool}
    percent_of(tg_id) -> процент менеджера (для фиксации в сделке).
    referral_of(tg_id) -> {referrer_id, referral_percent} или {} (реферальный бонус в сделке).
    """
    if status not in LEAD_STATUSES:
        raise ServiceError(400, "Недопустимый статус")

    current = lead.get("status") or "New"
    owner = str(lead.get("assigned_manager") or "")
    me_id = str(me["tg_id"])

    if current == "New" or not owner:
        new_owner = me_id  # лид из общего пула: тот, кто двигает, забирает его себе
    elif owner == me_id or me.get("is_admin"):
        new_owner = owner
    else:
        raise ServiceError(409, "Этот лид уже в работе у другого менеджера")

    fields = {"status": status, "assigned_manager": new_owner}

    comment = (comment or "").strip()
    if comment:
        fields["last_comment"] = comment[:MAX_COMMENT]

    if status == "Won":
        parsed = parse_amount(amount)
        if parsed is not None:
            fields["deal_amount"] = parsed
        elif lead.get("deal_amount") in (None, ""):
            raise ServiceError(400, "Укажите сумму сделки")
        if current != "Won" or not lead.get("won_at"):
            fields["won_at"] = now_iso()
            snap = referral_of(int(new_owner)) if referral_of else {}
            if snap:
                fields.update(snap)
            elif str(lead.get("referrer_id") or "").strip():
                fields.update({"referrer_id": "", "referral_percent": 0})  # старые условия сделки больше не действуют
        if current != "Won" or lead.get("manager_percent") in (None, ""):
            fields["manager_percent"] = percent_of(int(new_owner))

    return fields


def check_status_change(target: dict, new_status: str, admin_usernames: set):
    if new_status not in USER_STATUSES:
        raise ServiceError(400, "Недопустимый статус")
    if not target:
        raise ServiceError(404, "Пользователь не найден")
    username = (target.get("username") or "").lower()
    if target.get("role") == "admin" or username in admin_usernames:
        raise ServiceError(400, "Статус администратора менять нельзя")


def personal_row(leads: list, user_record: dict, period: str = "all") -> dict:
    """Статистика одного менеджера по его лидам."""
    result = stats.compute_stats(leads, [user_record], period)
    tg_id = str(user_record.get("telegram_id"))
    for row in result["managers"]:
        if row["tg_id"] == tg_id:
            return row
    return result["managers"][0] if result["managers"] else {}
