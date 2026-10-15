"""Общие мелочи для парсера: нормализация телефонов."""
import re


def phone_key(raw) -> str:
    """Ключ для дедупликации: 10 цифр российского номера без кода страны.
    Из строки с несколькими номерами берётся первый подходящий. Пустая строка — номера нет."""
    for part in re.split(r"[;,/]| или ", str(raw or "")):
        digits = re.sub(r"\D", "", part)
        if len(digits) == 11 and digits[0] in "78":
            return digits[1:]
        if len(digits) == 10:
            return digits
    return ""


def format_phone(raw) -> str:
    key = phone_key(raw)
    if not key:
        return ""
    return f"+7 {key[:3]} {key[3:6]}-{key[6:8]}-{key[8:]}"
