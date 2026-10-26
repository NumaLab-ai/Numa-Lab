import os
import time
import logging
import asyncio
from typing import Optional, Union

from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, MenuButtonWebApp, BotCommand
from aiogram.client.session.aiohttp import AiohttpSession
from dotenv import load_dotenv

import nocodb
import stats
import service
import referral
import messaging
import scraper_settings
from auth import verify_init_data, AuthError

logging.basicConfig(level=logging.INFO)
load_dotenv(override=True)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "RAG3QU1T").lstrip("@")
ADMIN_USERNAMES = {u.strip().lstrip("@").lower() for u in os.getenv("ADMIN_USERNAMES", "RAG3QU1T,numa_ceo").split(",") if u.strip()}
WEBAPP_URL = os.getenv("WEBAPP_URL", "http://100.94.236.55:8000")
TG_PROXY = os.getenv("TG_PROXY") or None

ALLOW_MANAGER_BROADCAST = os.getenv("ALLOW_MANAGER_BROADCAST", "0") == "1"

STATE = {"admin_ids": set(), "bot_username": os.getenv("BOT_USERNAME", "").lstrip("@") or None}
PENDING = {}          # кто сейчас пишет сообщение через бота: {tg_id: {"to": ..., "ts": ...}}
PENDING_TTL = 600
DETAILS_WAIT = {}     # кто сейчас присылает реквизиты: {tg_id: {"ts": ..., "after_deal": bool}}


def rub(value) -> str:
    try:
        return f"{float(value):,.0f}".replace(",", " ")
    except (TypeError, ValueError):
        return "0"
limiter = messaging.RateLimiter()

bot = Bot(token=BOT_TOKEN, session=AiohttpSession(proxy=TG_PROXY))
dp = Dispatcher()
fastapi_app = FastAPI(title="Numa Partners API")

if os.path.isdir(os.path.join(BASE_DIR, "static")):
    fastapi_app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


# ---------- Вспомогательное ----------

_cache = {}


def cached(key, ttl, fn, *args):
    """Короткий кэш, чтобы не дёргать NocoDB на каждый клик (Raspberry Pi слабая)."""
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = fn(*args)
    _cache[key] = (now, value)
    return value


def invalidate(*prefixes):
    for key in list(_cache):
        if any(key.startswith(p) for p in prefixes):
            _cache.pop(key, None)


async def run(fn, *args):
    """Выполняет синхронную функцию (NocoDB) в потоке и переводит ошибки в HTTP."""
    try:
        return await asyncio.to_thread(fn, *args)
    except service.ServiceError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)
    except nocodb.NocoError as e:
        logging.error("NocoDB: %s", e)
        raise HTTPException(status_code=502, detail="База данных временно недоступна")


def is_admin(tg_user: types.User) -> bool:
    return (tg_user.username or "").lower() in ADMIN_USERNAMES


def approve_keyboard(tg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_{tg_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{tg_id}"),
        InlineKeyboardButton(text="⛔ Бан", callback_data=f"ban_{tg_id}"),
    ]])


async def notify_user(tg_id: int, text: Optional[str]):
    if not text:
        return
    try:
        await bot.send_message(tg_id, text)
    except Exception as e:
        logging.warning("Не удалось уведомить %s: %s", tg_id, e)


async def notify_admins(text: str, reply_markup=None):
    ids = set(STATE["admin_ids"])
    try:
        ids.update(await asyncio.to_thread(nocodb.get_admin_ids))
    except nocodb.NocoError as e:
        logging.warning("Не удалось получить админов из базы: %s", e)
    if not ids:
        logging.warning("Некому отправить уведомление админу: никто из админов ещё не писал /start")
    for admin_id in ids:
        await notify_user_markup(admin_id, text, reply_markup)


async def notify_user_markup(tg_id: int, text: str, reply_markup=None):
    try:
        await bot.send_message(tg_id, text, reply_markup=reply_markup)
    except Exception as e:
        logging.warning("Не удалось уведомить %s: %s", tg_id, e)


DETAILS_PROMPT = ("💳 Укажите реквизиты для выплат одним сообщением: номер карты или телефон для СБП, "
                  "банк и имя получателя.\nИзменить позже: /requisites. Отмена: /cancel")


async def ask_details(tg_id: int, after_deal: bool = False, intro: str = ""):
    DETAILS_WAIT[tg_id] = {"ts": time.time(), "after_deal": after_deal}
    await notify_user_markup(tg_id, (intro + "\n\n" if intro else "") + DETAILS_PROMPT)


def _set_status_sync(tg_id: int, status: str):
    rec = nocodb.get_user_by_tg_id(tg_id)
    service.check_status_change(rec, status, ADMIN_USERNAMES)
    fields = {"status": status}
    if status == "approved" and not rec.get("approved_at"):
        fields["approved_at"] = service.now_iso()  # с этого дня идёт месяц реферального бонуса
    nocodb.update_user(rec["Id"], fields)
    invalidate("users", f"user:{tg_id}")
    return rec, fields


async def set_user_status(tg_id: int, status: str):
    """Меняет статус менеджера и сообщает ему об этом. Используется и ботом, и API."""
    rec, fields = await run(_set_status_sync, tg_id, status)
    await notify_user(tg_id, service.USER_STATUS_MESSAGES.get(status))
    if "approved_at" in fields and rec.get("referrer_id"):
        try:
            await notify_user(int(rec["referrer_id"]), (
                f"🤝 {rec.get('full_name') or 'Ваш друг'} присоединился по вашей ссылке!\n"
                f"Ближайшие {referral.REFERRAL_DAYS} дней вы получаете {referral.REFERRAL_PERCENT:g}% с суммы его сделок."))
        except (TypeError, ValueError):
            pass


async def get_bot_username():
    if not STATE["bot_username"]:
        try:
            STATE["bot_username"] = (await bot.get_me()).username
        except Exception as e:
            logging.warning("Не удалось узнать username бота (можно задать BOT_USERNAME в .env): %s", e)
    return STATE["bot_username"]


# ---------- Telegram-бот ----------

@dp.message(CommandStart())
async def start_handler(message: types.Message, command: CommandObject = None):
    print(f"[BOT LOG] /start от {message.from_user.username} (ID: {message.from_user.id})")
    tg_id = message.from_user.id
    username = (message.from_user.username or "").lstrip("@")
    full_name = message.from_user.full_name or "Пользователь"

    try:
        user = await asyncio.to_thread(nocodb.get_user_by_tg_id, tg_id)

        if is_admin(message.from_user):
            STATE["admin_ids"].add(tg_id)
            if not user:
                await asyncio.to_thread(nocodb.create_user_request, tg_id, username, full_name, "admin", "approved")
            elif user.get("status") != "approved" or user.get("role") != "admin":
                await asyncio.to_thread(nocodb.update_user, user["Id"], {"status": "approved", "role": "admin"})
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🚀 Открыть Numa Partners Mini App", web_app=WebAppInfo(url=f"{WEBAPP_URL}/app"))
            ]])
            await message.answer(
                f"👋 Приветствую, Администратор Numa Studio (@{username})!\nДоступ администратора активирован.",
                reply_markup=kb,
            )
            return

        if user:
            status = user.get("status")
            if status == "approved":
                kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="💼 Открыть Numa Partners App", web_app=WebAppInfo(url=f"{WEBAPP_URL}/app"))
                ]])
                await message.answer(f"С возвращением, {full_name}! Ваша панель готова к работе.", reply_markup=kb)
                if not str(user.get("payout_details") or "").strip():
                    await ask_details(tg_id, intro="Чтобы мы могли выплатить заработанное, нужны ваши реквизиты.")
            elif status == "pending":
                await message.answer(f"⏳ Ваша заявка находится на рассмотрении у администратора (@{ADMIN_USERNAME}).")
            elif status == "banned":
                await message.answer("⛔ Доступ к Numa Partners закрыт администратором.")
            else:
                await message.answer("❌ Доступ к Numa Partners был отклонен.")
            return

        referrer_id, referrer_name = None, ""
        ref_id = referral.parse_ref_payload(command.args if command else None)
        if ref_id and ref_id != tg_id:
            ref_rec = await asyncio.to_thread(_user_record, ref_id)
            if ref_rec and ref_rec.get("status") == "approved":
                referrer_id, referrer_name = ref_id, ref_rec.get("full_name") or ""
        await asyncio.to_thread(nocodb.create_user_request, tg_id, username, full_name, "manager", "pending", referrer_id)
    except nocodb.NocoError as e:
        logging.error("NocoDB в /start: %s", e)
        await message.answer("⚠️ Сервис временно недоступен, попробуйте через минуту.")
        return
    except Exception:
        logging.exception("Сбой в /start")
        await message.answer("⚠️ Что-то пошло не так, попробуйте /start ещё раз через минуту.")
        return

    await message.answer(
        f"🛎 Ваша заявка отправлена на подтверждение администратору (@{ADMIN_USERNAME}). Ожидайте авторизации!"
    )
    await notify_admins(
        f"🛎 Новая заявка менеджера:\n{full_name} (@{username or 'без username'})\nID: {tg_id}"
        + (f"\nПригласил: {referrer_name} (ID {referrer_id})" if referrer_id else ""),
        approve_keyboard(tg_id),
    )
    await ask_details(tg_id, intro="Пока администратор рассматривает заявку, оставьте реквизиты для выплат по вашим сделкам.")


ACTIONS = {"approve": "approved", "reject": "rejected", "ban": "banned"}
ACTION_TEXT = {"approved": "✅ Пользователь {id} одобрен", "rejected": "❌ Пользователь {id} отклонён", "banned": "⛔ Пользователь {id} заблокирован"}


@dp.callback_query(F.data.startswith("approve_"))
@dp.callback_query(F.data.startswith("reject_"))
@dp.callback_query(F.data.startswith("ban_"))
async def user_action_callback(callback: types.CallbackQuery):
    if not is_admin(callback.from_user):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    try:
        action, raw_id = callback.data.split("_", 1)
        tg_id = int(raw_id)
        status = ACTIONS[action]
    except (ValueError, KeyError):
        await callback.answer("Некорректные данные", show_alert=True)
        return
    try:
        await set_user_status(tg_id, status)
        text = ACTION_TEXT[status].format(id=tg_id)
    except HTTPException as e:
        text = f"⚠️ {e.detail}"
    await callback.message.edit_text(text)
    await callback.answer()


# ---------- Авторизация API ----------

def _user_record(tg_id: int):
    return cached(f"user:{tg_id}", 20, nocodb.get_user_by_tg_id, tg_id)


async def current_user(x_init_data: str = Header(default="")) -> dict:
    try:
        tg = verify_init_data(x_init_data, BOT_TOKEN)
    except AuthError as e:
        raise HTTPException(status_code=401, detail=str(e))

    try:
        tg_id = int(tg["id"])
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Неверные данные пользователя")
    username = tg.get("username") or ""
    admin = username.lower() in ADMIN_USERNAMES
    rec = await run(_user_record, tg_id)

    if not admin:
        if not rec:
            raise HTTPException(status_code=403, detail="not_registered")
        if rec.get("status") != "approved":
            raise HTTPException(status_code=403, detail=rec.get("status") or "rejected")

    name = f"{tg.get('first_name', '')} {tg.get('last_name', '')}".strip() or username or str(tg_id)
    return {
        "tg_id": tg_id,
        "username": username,
        "name": name,
        "is_admin": admin,
        "record": rec,
        "percent": stats.user_percent(rec),
    }


async def admin_user(me: dict = Depends(current_user)) -> dict:
    if not me["is_admin"]:
        raise HTTPException(status_code=403, detail="Только для администратора")
    return me


# ---------- API для Mini App ----------

@fastapi_app.get("/app", response_class=HTMLResponse)
async def get_mini_app():
    with open(os.path.join(BASE_DIR, "static", "index.html"), "r", encoding="utf-8") as f:
        return f.read()


@fastapi_app.get("/api/me")
async def api_me(period: str = "all", me: dict = Depends(current_user)):
    period = period if period in service.PERIODS else "all"
    leads = await run(cached, f"mine:{me['tg_id']}", 15, nocodb.get_leads_of_manager, me["tg_id"])
    record = me["record"] or {
        "telegram_id": me["tg_id"], "full_name": me["name"], "username": me["username"],
        "status": "approved", "role": "admin" if me["is_admin"] else "manager",
    }
    return {
        "tg_id": me["tg_id"],
        "name": me["name"],
        "username": me["username"],
        "is_admin": me["is_admin"],
        "percent": me["percent"],
        "stats": service.personal_row(leads, record, period),
        "payout_details": str(record.get("payout_details") or ""),
    }


@fastapi_app.get("/api/leads")
async def api_leads(scope: str = "pool", tier: str = "A", status: Optional[str] = None, me: dict = Depends(current_user)):
    if scope == "pool":
        if tier not in service.POOL_TIERS:
            raise HTTPException(status_code=400, detail="Недопустимый tier")
        return await run(nocodb.get_leads_feed, "New", tier, 50)
    if scope == "mine":
        if status and status not in service.LEAD_STATUSES:
            raise HTTPException(status_code=400, detail="Недопустимый статус")
        leads = await run(cached, f"mine:{me['tg_id']}", 15, nocodb.get_leads_of_manager, me["tg_id"])
        return [l for l in leads if (l.get("status") == status if status else l.get("status") in service.LEAD_STATUSES)]
    raise HTTPException(status_code=400, detail="Недопустимый scope")


def num_or_zero(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


class LeadUpdate(BaseModel):
    lead_id: int
    status: str
    comment: Optional[str] = None
    deal_amount: Optional[Union[float, str]] = None


def _update_lead_sync(data: LeadUpdate, me: dict):
    lead = nocodb.get_lead(data.lead_id)
    if not lead:
        raise service.ServiceError(404, "Лид не найден")

    def percent_of(tg_id: int) -> float:
        return stats.user_percent(_user_record(tg_id))

    def referral_of(owner_id: int) -> dict:
        owner = _user_record(owner_id)
        try:
            referrer = _user_record(int((owner or {}).get("referrer_id")))
        except (TypeError, ValueError):
            return {}
        return referral.deal_snapshot(owner, referrer)

    fields = service.plan_lead_update(lead, me, data.status, data.comment, data.deal_amount, percent_of, referral_of)
    nocodb.update_lead(data.lead_id, fields)
    invalidate("mine:", "all_leads", "refl:")
    return lead, fields


@fastapi_app.post("/api/leads/update")
async def api_update_lead(data: LeadUpdate, me: dict = Depends(current_user)):
    lead, fields = await run(_update_lead_sync, data, me)
    if fields.get("status") == "Won" and "deal_amount" in fields:
        amount_text = f"{fields['deal_amount']:,.0f}".replace(",", " ")
        asyncio.create_task(notify_admins(
            f"🎉 Сделка! {me['name']}: «{lead.get('company_name', '')}» на {amount_text} ₽\n"
            f"Ожидает оплаты клиентом. Отметить результат: Mini App → Сделки."
        ))
        if fields.get("referrer_id"):
            bonus = fields["deal_amount"] * num_or_zero(fields.get("referral_percent")) / 100
            asyncio.create_task(notify_user(int(fields["referrer_id"]), (
                f"💸 Ваш друг {me['name']} закрыл сделку на {amount_text} ₽. "
                f"Ваш бонус: {f'{bonus:,.0f}'.replace(',', ' ')} ₽ ({fields.get('referral_percent'):g}%)")))
    return {"status": "ok"}


def _save_details_sync(tg_id: int, raw) -> str:
    text = service.parse_details(raw)
    rec = nocodb.get_user_by_tg_id(tg_id)
    if not rec:
        raise service.ServiceError(404, "Сначала пройдите регистрацию: /start")
    nocodb.set_payout_details(rec["Id"], text)
    invalidate("users", f"user:{tg_id}")
    return text


class DetailsIn(BaseModel):
    details: str


@fastapi_app.post("/api/me/details")
async def api_my_details(data: DetailsIn, me: dict = Depends(current_user)):
    text = await run(_save_details_sync, me["tg_id"], data.details)
    return {"status": "ok", "details": text}


@dp.callback_query(F.data.startswith("pdok_"))
@dp.callback_query(F.data.startswith("pdchg_"))
async def details_check_callback(callback: types.CallbackQuery):
    action, _, lead_id = callback.data.partition("_")
    if not lead_id.isdigit():
        await callback.answer("Некорректные данные", show_alert=True)
        return
    lead = await run(nocodb.get_lead, int(lead_id))
    if not lead or str(lead.get("assigned_manager") or "") != str(callback.from_user.id):
        await callback.answer("Это не ваша сделка", show_alert=True)
        return
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    if action == "pdok":
        await callback.message.answer("👍 Спасибо, реквизиты подтверждены. Выплата будет отправлена на них.")
        await notify_admins(f"✅ {callback.from_user.full_name} подтвердил реквизиты по сделке «{lead.get('company_name', '')}». Можно переводить.")
    else:
        await ask_details(callback.from_user.id, after_deal=True, intro="Пришлите верные реквизиты, мы передадим их администратору.")
    await callback.answer()


# ---------- API админа ----------

DEAL_ORDER = {"awaiting": 0, "paid": 1, "payout_done": 2, "client_lost": 3}


def _deals_sync():
    users = {str(u.get("telegram_id")): u for u in cached("users", 15, nocodb.get_users)}
    out = []
    for l in nocodb.get_won_leads():
        if l.get("deal_amount") in (None, ""):
            continue
        owner = str(l.get("assigned_manager") or "")
        u = users.get(owner) or {}
        percent = stats.lead_percent(l, u)
        amount = stats.num(l.get("deal_amount"))
        details = str(u.get("payout_details") or "").strip()
        out.append({
            "id": l["Id"], "company": l.get("company_name") or "", "amount": amount, "percent": percent,
            "payout": round(amount * percent / 100.0, 2),
            "payment_status": l.get("payment_status") or "paid",
            "manager": u.get("full_name") or f"ID {owner}", "manager_id": owner,
            "username": u.get("username") or "", "details": details, "has_details": bool(details),
            "won_at": l.get("won_at") or "",
        })
    out.sort(key=lambda d: (DEAL_ORDER.get(d["payment_status"], 1), -(stats.parse_dt(d["won_at"]) or stats.parse_dt("2000-01-01")).timestamp()))
    return out[:300]


@fastapi_app.get("/api/admin/deals")
async def api_admin_deals(me: dict = Depends(admin_user)):
    return {"deals": await run(_deals_sync)}


class DealStatus(BaseModel):
    lead_id: int
    payment_status: str


def _deal_status_sync(lead_id: int, new_status: str):
    lead = nocodb.get_lead(lead_id)
    service.check_payment_status(lead, new_status)
    nocodb.set_payment_status(lead_id, new_status)
    invalidate("mine:", "all_leads", "refl:")
    owner = str(lead.get("assigned_manager") or "")
    rec = nocodb.get_user_by_tg_id(int(owner)) if owner.isdigit() else None
    return lead, owner, rec


@fastapi_app.post("/api/admin/deals/status")
async def api_admin_deal_status(data: DealStatus, me: dict = Depends(admin_user)):
    lead, owner, rec = await run(_deal_status_sync, data.lead_id, data.payment_status)
    if owner.isdigit():
        company = lead.get("company_name") or ""
        percent = stats.lead_percent(lead, rec or {})
        payout = rub(stats.num(lead.get("deal_amount")) * percent / 100.0)
        st = data.payment_status
        if st == "paid":
            details = str((rec or {}).get("payout_details") or "").strip()
            if details:
                kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="✅ Всё верно", callback_data=f"pdok_{lead['Id']}"),
                    InlineKeyboardButton(text="✏️ Изменить", callback_data=f"pdchg_{lead['Id']}"),
                ]])
                await notify_user_markup(int(owner), (
                    f"✅ Клиент оплатил заказ «{company}».\nВаша выплата: {payout} ₽ ({percent:g}%).\n\n"
                    f"Реквизиты, на которые мы переведём:\n{details}\n\nПроверьте, пожалуйста, чтобы мы не отправили деньги не туда."), kb)
            else:
                await ask_details(int(owner), after_deal=True, intro=(
                    f"✅ Клиент оплатил заказ «{company}». Ваша выплата: {payout} ₽ ({percent:g}%).\nУ нас нет ваших реквизитов."))
        elif st == "client_lost":
            await notify_user(int(owner), f"❌ Клиент по «{company}» не оплатил заказ. Сделка не засчитана.")
        elif st == "payout_done":
            await notify_user(int(owner), f"💸 Выплата {payout} ₽ по «{company}» отправлена. Проверьте поступление.")
    return {"status": "ok"}

@fastapi_app.get("/api/admin/stats")
async def api_admin_stats(period: str = "all", me: dict = Depends(admin_user)):
    period = period if period in service.PERIODS else "all"
    users = await run(cached, "users", 15, nocodb.get_users)
    leads = await run(cached, "all_leads", 30, nocodb.get_leads_light)
    result = stats.compute_stats(leads, users, period)
    result["admin_usernames"] = sorted(ADMIN_USERNAMES)
    return result


@fastapi_app.get("/api/admin/scraper")
async def api_scraper_get(me: dict = Depends(admin_user)):
    leads = await run(cached, "all_leads", 30, nocodb.get_leads_light)
    free = sum(1 for l in leads if not l.get("assigned_manager") and l.get("status") in (None, "", "New"))
    return {"settings": scraper_settings.describe(), "total_leads": len(leads), "free_leads": free}


class ScraperSettings(BaseModel):
    values: dict


@fastapi_app.post("/api/admin/scraper")
async def api_scraper_set(data: ScraperSettings, me: dict = Depends(admin_user)):
    try:
        await asyncio.to_thread(scraper_settings.save, data.values)
    except scraper_settings.SettingsError as e:
        raise HTTPException(400, str(e))
    return {"status": "ok", "settings": scraper_settings.describe()}


class UserStatus(BaseModel):
    tg_id: int
    status: str


@fastapi_app.post("/api/admin/users/status")
async def api_user_status(data: UserStatus, me: dict = Depends(admin_user)):
    await set_user_status(data.tg_id, data.status)
    return {"status": "ok"}


class UserPercent(BaseModel):
    tg_id: int
    percent: Union[float, str]


def _set_percent_sync(tg_id: int, percent):
    value = service.parse_percent(percent)
    rec = nocodb.get_user_by_tg_id(tg_id)
    if not rec:
        raise service.ServiceError(404, "Пользователь не найден")
    nocodb.update_user(rec["Id"], {"commission_percent": value})
    invalidate("users", f"user:{tg_id}")
    return value


@fastapi_app.post("/api/admin/users/percent")
async def api_user_percent(data: UserPercent, me: dict = Depends(admin_user)):
    value = await run(_set_percent_sync, data.tg_id, data.percent)
    await notify_user(data.tg_id, f"💼 Ваш процент с продаж: {value:g}%")
    return {"status": "ok", "percent": value}


# ---------- Реферальная программа ----------

def _referral_sync(me: dict):
    users = cached("users", 15, nocodb.get_users)
    ref_leads = cached(f"refl:{me['tg_id']}", 30, nocodb.get_leads_by_referrer, me["tg_id"])
    return referral.summarize(me["tg_id"], users, ref_leads)


@fastapi_app.get("/api/referral")
async def api_referral(me: dict = Depends(current_user)):
    data = await run(_referral_sync, me)
    username = await get_bot_username()
    data["link"] = f"https://t.me/{username}?start=ref_{me['tg_id']}" if username else None
    return data


# ---------- Сообщения через бота ----------

class MessageIn(BaseModel):
    to: str
    text: str


def _users_cached():
    return cached("users", 15, nocodb.get_users)


def _plan_message_sync(me: dict, to: str, text: str):
    return messaging.plan_message(me, to, text, _users_cached(), ADMIN_USERNAMES, limiter, ALLOW_MANAGER_BROADCAST)


async def deliver(me: dict, targets: list, text: str) -> dict:
    """Отправляет сообщение получателям (с кнопкой «Ответить») и возвращает итог."""
    body = messaging.format_message(me, text)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="↩️ Ответить", callback_data=f"wreply_{me['tg_id']}")
    ]])
    sent, failed = 0, []
    for t in targets:
        try:
            await bot.send_message(int(t["tg_id"]), body, reply_markup=kb)
            sent += 1
        except Exception as e:
            logging.warning("Сообщение для %s не доставлено: %s", t["tg_id"], e)
            failed.append(t["name"])
        await asyncio.sleep(0.05)  # лимит Telegram около 30 сообщений в секунду
    logging.info("Сообщение от %s: доставлено %s, не доставлено %s", me["tg_id"], sent, len(failed))
    return {"sent": sent, "failed": failed}


@fastapi_app.get("/api/messages/recipients")
async def api_message_recipients(me: dict = Depends(current_user)):
    users = await run(_users_cached)
    return messaging.recipients_view(me, users, ADMIN_USERNAMES, ALLOW_MANAGER_BROADCAST)


@fastapi_app.post("/api/messages/send")
async def api_message_send(data: MessageIn, me: dict = Depends(current_user)):
    targets, text = await run(_plan_message_sync, me, data.to, data.text)
    return await deliver(me, targets, text)


async def bot_actor(tg_user: types.User):
    """Тот же допуск, что у API: одобренный участник или админ. Иначе None."""
    admin = is_admin(tg_user)
    rec = await run(_user_record, tg_user.id)
    if not admin and (not rec or rec.get("status") != "approved"):
        return None
    name = tg_user.full_name or tg_user.username or str(tg_user.id)
    return {"tg_id": tg_user.id, "username": tg_user.username or "", "name": name, "is_admin": admin, "record": rec}


@dp.message(Command("write"))
async def write_command(message: types.Message):
    me = await bot_actor(message.from_user)
    if not me:
        await message.answer("Писать коллегам могут только одобренные участники Numa Partners.")
        return
    view = messaging.recipients_view(me, await run(_users_cached), ADMIN_USERNAMES, ALLOW_MANAGER_BROADCAST)
    rows = []
    if view["can_broadcast"]:
        rows.append([InlineKeyboardButton(text="📢 Всем сразу", callback_data="wto_all")])
    if view["has_admins"]:
        rows.append([InlineKeyboardButton(text="👑 Админам", callback_data="wto_admins")])
    people = [InlineKeyboardButton(text=p["name"][:28], callback_data=f"wto_{p['tg_id']}") for p in view["people"][:40]]
    rows.extend([people[i:i + 2] for i in range(0, len(people), 2)])
    if not rows:
        await message.answer("Пока некому писать: других участников нет.")
        return
    await message.answer("Кому написать?", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@dp.callback_query(F.data.startswith("wto_"))
@dp.callback_query(F.data.startswith("wreply_"))
async def write_target_callback(callback: types.CallbackQuery):
    me = await bot_actor(callback.from_user)
    if not me:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    to = callback.data.split("_", 1)[1]
    if to != "all" and to != "admins" and not to.isdigit():
        await callback.answer("Некорректные данные", show_alert=True)
        return
    PENDING[callback.from_user.id] = {"to": to, "ts": time.time()}
    await callback.message.answer("Напишите сообщение одним текстом. Отмена: /cancel")
    await callback.answer()


@dp.message(Command("requisites"))
async def requisites_command(message: types.Message):
    rec = await run(_user_record, message.from_user.id)
    if not rec:
        await message.answer("Сначала пройдите регистрацию: /start")
        return
    current = str(rec.get("payout_details") or "").strip()
    await ask_details(message.from_user.id, intro=f"Текущие реквизиты:\n{current}" if current else "Реквизиты пока не указаны.")


@dp.message(Command("cancel"))
async def cancel_command(message: types.Message):
    PENDING.pop(message.from_user.id, None)
    DETAILS_WAIT.pop(message.from_user.id, None)
    await message.answer("Отменено.")


@dp.message(F.text & ~F.text.startswith("/"))
async def text_handler(message: types.Message):
    waiting = DETAILS_WAIT.get(message.from_user.id)
    if waiting and time.time() - waiting["ts"] <= PENDING_TTL:
        try:
            await run(_save_details_sync, message.from_user.id, message.text)
        except service.ServiceError as e:
            await message.answer(f"⚠️ {e.detail}\nПопробуйте ещё раз или /cancel")
            return
        except nocodb.NocoError as e:
            DETAILS_WAIT.pop(message.from_user.id, None)
            logging.error("Не удалось сохранить реквизиты: %s", e)
            await message.answer("⚠️ Не удалось сохранить реквизиты, попробуйте позже.")
            return
        DETAILS_WAIT.pop(message.from_user.id, None)
        await message.answer("✅ Реквизиты сохранены. Изменить: /requisites")
        if waiting.get("after_deal"):
            await notify_admins(f"💳 {message.from_user.full_name} (ID {message.from_user.id}) обновил реквизиты после сделки:\n{service.parse_details(message.text)}")
        return
    DETAILS_WAIT.pop(message.from_user.id, None)
    pend = PENDING.pop(message.from_user.id, None)
    if not pend or time.time() - pend["ts"] > PENDING_TTL:
        await message.answer("Открыть приложение: /start\nНаписать коллеге: /write")
        return
    me = await bot_actor(message.from_user)
    if not me:
        return
    try:
        targets, text = await run(_plan_message_sync, me, pend["to"], message.text)
    except HTTPException as e:
        await message.answer(f"⚠️ {e.detail}")
        return
    result = await deliver(me, targets, text)
    note = f"✅ Доставлено: {result['sent']}"
    if result["failed"]:
        note += f"\nНе доставлено ({len(result['failed'])}): " + ", ".join(result["failed"][:10]) + \
                "\nВозможно, они ещё не запускали бота."
    await message.answer(note)


# ---------- Запуск ----------

async def setup_bot_ui():
    """Кнопка Mini App рядом со строкой сообщений (для всех чатов с ботом) и меню команд."""
    try:
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(
            text="Numa", web_app=WebAppInfo(url=f"{WEBAPP_URL}/app")))
        await bot.set_my_commands([
            BotCommand(command="start", description="Открыть Numa Partners"),
            BotCommand(command="write", description="Написать коллеге или всем"),
            BotCommand(command="requisites", description="Реквизиты для выплат"),
            BotCommand(command="cancel", description="Отменить ввод"),
        ])
        logging.info("Кнопка Mini App и команды установлены")
    except Exception as e:
        logging.warning("Не удалось установить кнопку меню (нужен https-адрес WEBAPP_URL): %s", e)
    await get_bot_username()


async def start_bot():
    """Опрос Telegram не должен падать из-за временных сбоев сети/прокси: пробуем снова."""
    delay = 5
    while True:
        try:
            try:
                await bot.delete_webhook(drop_pending_updates=True)
            except Exception as e:
                logging.warning("delete_webhook не удался (продолжаем): %s", e)
            asyncio.create_task(asyncio.wait_for(setup_bot_ui(), 60))
            logging.info("Bot polling started")
            await dp.start_polling(bot, handle_signals=False)
            return
        except Exception:
            logging.exception("BOT POLLING FAILED, повтор через %s с", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 120)


@fastapi_app.on_event("startup")
async def on_startup():
    asyncio.create_task(start_bot())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(fastapi_app, host="0.0.0.0", port=8000)
