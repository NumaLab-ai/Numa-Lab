import os
import logging
import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from dotenv import load_dotenv

import nocodb

logging.basicConfig(level=logging.INFO)
load_dotenv(override=True)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "RAG3QU1T").lstrip("@")
WEBAPP_URL = os.getenv("WEBAPP_URL", "http://100.94.236.55:8000")

STATE = {"admin_id": int(os.getenv("ADMIN_TG_ID", "0") or 0)}

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
fastapi_app = FastAPI(title="Numa Partners API")

try:
    fastapi_app.mount("/static", StaticFiles(directory="static"), name="static")
except Exception:
    pass


def is_admin(tg_user: types.User) -> bool:
    return (tg_user.username or "").lower() == ADMIN_USERNAME.lower()


def approve_keyboard(tg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_{tg_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{tg_id}"),
    ]])


@dp.message(CommandStart())
async def start_handler(message: types.Message):
    print(f"[BOT LOG] /start от {message.from_user.username} (ID: {message.from_user.id})")
    tg_id = message.from_user.id
    username = (message.from_user.username or "").lstrip("@")
    full_name = message.from_user.full_name or "Пользователь"

    try:
        user = nocodb.get_user_by_tg_id(tg_id)
    except Exception as e:
        print(f"[NocoDB Error] {e}")
        user = None

    if is_admin(message.from_user):
        STATE["admin_id"] = tg_id
        if not user:
            nocodb.create_user_request(tg_id, username, full_name, role="admin", status="approved")
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🚀 Открыть Numa Partners Mini App", web_app=WebAppInfo(url=f"{WEBAPP_URL}/app"))
        ]])
        await message.answer(
            f"👋 Приветствую, Владелец Numa Studio (@{ADMIN_USERNAME})!\nДоступ администратора активирован.",
            reply_markup=kb,
        )
        return

    if user:
        if user.get("status") == "approved":
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="💼 Открыть Numa Partners App", web_app=WebAppInfo(url=f"{WEBAPP_URL}/app"))
            ]])
            await message.answer(f"С возвращением, {full_name}! Ваша панель готова к работе.", reply_markup=kb)
        elif user.get("status") == "pending":
            await message.answer(f"⏳ Ваша заявка находится на рассмотрении у администратора (@{ADMIN_USERNAME}).")
        else:
            await message.answer("❌ Доступ к Numa Partners был отклонен.")
        return

    nocodb.create_user_request(tg_id, username, full_name, role="manager", status="pending")
    await message.answer(
        f"🛎 Ваша заявка отправлена на подтверждение администратору (@{ADMIN_USERNAME}). Ожидайте авторизации!"
    )

    admin_id = STATE["admin_id"]
    if admin_id:
        try:
            await bot.send_message(
                admin_id,
                f"🛎 Новая заявка менеджера:\n{full_name} (@{username or 'без username'})\nID: {tg_id}",
                reply_markup=approve_keyboard(tg_id),
            )
        except Exception as e:
            print(f"[Admin Notify Error] {e}")
    else:
        print("[WARN] ADMIN_TG_ID не задан: админ не получит уведомление. Отправьте /start с аккаунта админа.")


@dp.callback_query(F.data.startswith("approve_"))
async def approve_user_callback(callback: types.CallbackQuery):
    if not is_admin(callback.from_user):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    target_tg_id = int(callback.data.split("_")[1])
    user = nocodb.get_user_by_tg_id(target_tg_id)
    if user and nocodb.update_user_status(user["Id"], "approved"):
        await callback.message.edit_text(f"✅ Пользователь {target_tg_id} успешно одобрен!")
        try:
            await bot.send_message(target_tg_id, "🎉 Ваша заявка одобрена! Нажмите /start для входа в Mini App.")
        except Exception as e:
            print(f"[Notify Error] {e}")
    else:
        await callback.message.edit_text(f"⚠️ Не удалось одобрить пользователя {target_tg_id}.")
    await callback.answer()


@dp.callback_query(F.data.startswith("reject_"))
async def reject_user_callback(callback: types.CallbackQuery):
    if not is_admin(callback.from_user):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    target_tg_id = int(callback.data.split("_")[1])
    user = nocodb.get_user_by_tg_id(target_tg_id)
    if user and nocodb.update_user_status(user["Id"], "rejected"):
        await callback.message.edit_text(f"❌ Пользователь {target_tg_id} отклонен.")
        try:
            await bot.send_message(target_tg_id, "❌ К сожалению, ваша заявка отклонена.")
        except Exception as e:
            print(f"[Notify Error] {e}")
    else:
        await callback.message.edit_text(f"⚠️ Не удалось отклонить пользователя {target_tg_id}.")
    await callback.answer()


@fastapi_app.get("/app", response_class=HTMLResponse)
async def get_mini_app():
    with open("static/index.html", "r", encoding="utf-8") as f:
        return f.read()


@fastapi_app.get("/api/user/check/{tg_id}")
async def check_user_access(tg_id: int):
    user = nocodb.get_user_by_tg_id(tg_id)
    if not user:
        raise HTTPException(status_code=403, detail="User not found")
    return user


@fastapi_app.get("/api/leads")
async def fetch_leads(status: str = "New", tier: str = None):
    return nocodb.get_leads_feed(status_filter=status, tier_filter=tier)


class LeadStatusUpdate(BaseModel):
    lead_id: int
    status: str
    manager_tg_id: int
    comment: str = None


@fastapi_app.post("/api/leads/update")
async def update_lead(data: LeadStatusUpdate):
    res = nocodb.update_lead_status(data.lead_id, data.status, data.manager_tg_id, data.comment)
    if not res:
        raise HTTPException(status_code=500, detail="Failed to update lead")
    return {"status": "ok"}


async def start_bot():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


@fastapi_app.on_event("startup")
async def on_startup():
    asyncio.create_task(start_bot())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(fastapi_app, host="0.0.0.0", port=8000)
