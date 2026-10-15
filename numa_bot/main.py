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
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.client.session.aiohttp import AiohttpSession
from dotenv import load_dotenv

import nocodb
import stats
import service
from auth import verify_init_data, AuthError

logging.basicConfig(level=logging.INFO)
load_dotenv(override=True)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "RAG3QU1T").lstrip("@")
ADMIN_USERNAMES = {u.strip().lstrip("@").lower() for u in os.getenv("ADMIN_USERNAMES", "RAG3QU1T,numa_ceo").split(",") if u.strip()}
WEBAPP_URL = os.getenv("WEBAPP_URL", "http://100.94.236.55:8000")
TG_PROXY = os.getenv("TG_PROXY") or None

STATE = {"admin_ids": set()}

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


def _set_status_sync(tg_id: int, status: str):
    rec = nocodb.get_user_by_tg_id(tg_id)
    service.check_status_change(rec, status, ADMIN_USERNAMES)
    nocodb.update_user(rec["Id"], {"status": status})
    invalidate("users", f"user:{tg_id}")


async def set_user_status(tg_id: int, status: str):
    """Меняет статус менеджера и сообщает ему об этом. Используется и ботом, и API."""
    await run(_set_status_sync, tg_id, status)
    await notify_user(tg_id, service.USER_STATUS_MESSAGES.get(status))


# ---------- Telegram-бот ----------

@dp.message(CommandStart())
async def start_handler(message: types.Message):
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
            elif status == "pending":
                await message.answer(f"⏳ Ваша заявка находится на рассмотрении у администратора (@{ADMIN_USERNAME}).")
            elif status == "banned":
                await message.answer("⛔ Доступ к Numa Partners закрыт администратором.")
            else:
                await message.answer("❌ Доступ к Numa Partners был отклонен.")
            return

        await asyncio.to_thread(nocodb.create_user_request, tg_id, username, full_name, "manager", "pending")
    except nocodb.NocoError as e:
        logging.error("NocoDB в /start: %s", e)
        await message.answer("⚠️ Сервис временно недоступен, попробуйте через минуту.")
        return

    await message.answer(
        f"🛎 Ваша заявка отправлена на подтверждение администратору (@{ADMIN_USERNAME}). Ожидайте авторизации!"
    )
    await notify_admins(
        f"🛎 Новая заявка менеджера:\n{full_name} (@{username or 'без username'})\nID: {tg_id}",
        approve_keyboard(tg_id),
    )


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

    fields = service.plan_lead_update(lead, me, data.status, data.comment, data.deal_amount, percent_of)
    nocodb.update_lead(data.lead_id, fields)
    invalidate("mine:", "all_leads")
    return lead, fields


@fastapi_app.post("/api/leads/update")
async def api_update_lead(data: LeadUpdate, me: dict = Depends(current_user)):
    lead, fields = await run(_update_lead_sync, data, me)
    if fields.get("status") == "Won" and "deal_amount" in fields:
        amount_text = f"{fields['deal_amount']:,.0f}".replace(",", " ")
        asyncio.create_task(notify_admins(
            f"🎉 Сделка! {me['name']}: «{lead.get('company_name', '')}» на {amount_text} ₽"
        ))
    return {"status": "ok"}


# ---------- API админа ----------

@fastapi_app.get("/api/admin/stats")
async def api_admin_stats(period: str = "all", me: dict = Depends(admin_user)):
    period = period if period in service.PERIODS else "all"
    users = await run(cached, "users", 15, nocodb.get_users)
    leads = await run(cached, "all_leads", 30, nocodb.get_leads_light)
    result = stats.compute_stats(leads, users, period)
    result["admin_usernames"] = sorted(ADMIN_USERNAMES)
    return result


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


# ---------- Запуск ----------

async def start_bot():
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logging.info("Bot polling started")
        await dp.start_polling(bot, handle_signals=False)
    except Exception:
        logging.exception("BOT POLLING FAILED")


@fastapi_app.on_event("startup")
async def on_startup():
    asyncio.create_task(start_bot())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(fastapi_app, host="0.0.0.0", port=8000)
