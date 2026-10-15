"""Уведомления админам в Telegram: отчёты о пополнении базы и тревоги о сбоях.

Токен бота и прокси берутся из ../numa_bot/.env (копировать их в .env парсера не нужно).
Отправка идёт через curl: у Pi нет прямого доступа к Telegram, поэтому используется тот же
локальный прокси, что и у бота. Любая ошибка здесь не должна ломать пайплайн.
"""
import os
import subprocess

import requests
from dotenv import dotenv_values

HERE = os.path.dirname(os.path.abspath(__file__))
BOT_ENV = os.path.join(HERE, "..", "numa_bot", ".env")


def _bot_config():
    cfg = dotenv_values(BOT_ENV)
    return cfg.get("BOT_TOKEN"), cfg.get("TG_PROXY"), cfg.get("USERS_TABLE_ID")


def admin_ids():
    """Telegram-id одобренных админов из таблицы Users."""
    token = os.getenv("NOCODB_TOKEN", "").strip()
    url = os.getenv("NOCODB_URL", "http://localhost:8080").strip().rstrip("/")
    table = _bot_config()[2] or os.getenv("USERS_TABLE_ID", "mls8kbn05zsx3mu")
    if not token:
        return []
    try:
        r = requests.get(
            f"{url}/api/v2/tables/{table}/records",
            headers={"xc-token": token},
            params={"where": "(role,eq,admin)~and(status,eq,approved)", "fields": "telegram_id", "limit": 20},
            timeout=10,
        )
        rows = r.json().get("list", []) if r.status_code == 200 else []
    except (requests.RequestException, ValueError):
        return []
    ids = []
    for row in rows:
        try:
            ids.append(int(row["telegram_id"]))
        except (KeyError, TypeError, ValueError):
            pass
    return ids


def send_admin(text: str) -> bool:
    token, proxy, _ = _bot_config()
    if not token:
        print("[Notify] Нет BOT_TOKEN в numa_bot/.env, уведомление не отправлено")
        return False
    ids = admin_ids()
    if not ids:
        print("[Notify] Не найдено админов в базе, уведомление не отправлено")
        return False

    sent = False
    for chat_id in ids:
        cmd = ["curl", "-s", "-m", "25", "-X", "POST", f"https://api.telegram.org/bot{token}/sendMessage",
               "--data-urlencode", f"chat_id={chat_id}", "--data-urlencode", f"text={text}"]
        if proxy:
            cmd[1:1] = ["-x", proxy.replace("socks5://", "socks5h://")]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=40)
            sent = sent or '"ok":true' in out.stdout.replace(" ", "")
        except (subprocess.SubprocessError, OSError) as e:
            print(f"[Notify] Ошибка отправки: {e}")
    return sent
