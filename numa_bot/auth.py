"""Проверка подписи Telegram Mini App (initData).

Фронтенд шлёт строку tg.initData в заголовке X-Init-Data. Подпись выдаёт Telegram,
подделать её без токена бота нельзя, поэтому по ней можно доверять id и username.
"""
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


class AuthError(Exception):
    pass


def verify_init_data(init_data: str, bot_token: str, max_age: int = 86400) -> dict:
    """Возвращает словарь user из initData или бросает AuthError."""
    if not init_data:
        raise AuthError("Откройте приложение через бота в Telegram")

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise AuthError("Нет подписи")

    check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise AuthError("Неверная подпись")

    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError:
        raise AuthError("Неверная дата")
    if time.time() - auth_date > max_age:
        raise AuthError("Сессия устарела, откройте приложение заново")

    try:
        user = json.loads(pairs.get("user", ""))
    except ValueError:
        raise AuthError("Нет данных пользователя")
    if not isinstance(user, dict) or "id" not in user:
        raise AuthError("Нет данных пользователя")
    return user
