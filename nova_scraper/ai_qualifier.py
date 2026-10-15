import json
import os
import re
import time

import requests
from dotenv import load_dotenv

load_dotenv(override=True)

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
TIERS = ("A", "B", "C", "D")
RETRY_DELAYS = (2, 6, 15)


def build_prompt(lead: dict) -> str:
    has_website = lead.get("has_website", False)
    web_url = lead.get("web_url", "")
    return f"""
Ты — AI-квалификатор лидов для агентства автоматизации Numa Studio.

Компания: {lead.get('company_name', '')} ({lead.get('category', '')}, {lead.get('city', '')})
Сайт: {'Да (' + web_url + ')' if has_website else 'НЕТ САЙТА'}
Данные сайта: {lead.get('site_summary', 'Информация о сайте отсутствует.')}

Правила оценки Tier:
- Tier 'A' (ВЫСШИЙ ПРИОРИТЕТ): частный малый/средний бизнес БЕЗ сайта. AI-бот в Telegram/WhatsApp от Numa Studio — идеальная замена сайту для записи 24/7 и сбора клиентов.
- Tier 'B': частный бизнес, сайт ЕСТЬ, но нет умных AI-ботов или автозаписи в мессенджерах.
- Tier 'C': малый бизнес, у которого автоматизация уже хорошо настроена (онлайн-запись, боты).
- Tier 'D': НЕ ЦЕЛЕВОЙ КЛИЕНТ — федеральные/городские сети и франшизы, государственные, муниципальные и бюджетные учреждения.

Задачи:
1. tier: строго 'A', 'B', 'C' или 'D'.
2. pain_points: 2-3 ключевые боли бизнеса (для Tier A — отсутствие сайта и потеря заявок; для Tier B — ручная работа администраторов). Не больше 300 символов.
3. ai_offer: короткий персонализированный оффер от Numa Studio, 2-3 предложения, без выдуманных фактов о компании. Не больше 400 символов.

Ответь ТОЛЬКО валидным JSON:
{{"tier": "A", "pain_points": "...", "ai_offer": "..."}}
"""


def _parse_json(content: str):
    content = content.strip()
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.IGNORECASE)
    try:
        return json.loads(content)
    except ValueError:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except ValueError:
                pass
    return None


def apply_result(lead: dict, data: dict):
    """Проверяет ответ ИИ и приводит тир в соответствие с фактом наличия сайта."""
    tier = str(data.get("tier", "")).strip().upper()
    if tier not in TIERS:
        return None
    # A и B различаются только наличием сайта — это известный факт, а не мнение модели
    if tier in ("A", "B"):
        tier = "B" if lead.get("has_website") else "A"
    lead["tier"] = tier
    lead["pain_points"] = str(data.get("pain_points", "")).strip()[:500]
    lead["ai_offer"] = str(data.get("ai_offer", "")).strip()[:700]
    return lead


def qualify_lead_with_ai(lead: dict):
    """Возвращает лид с тиром или None, если ИИ не ответил (такой лид не сохраняем, попробуем в следующий раз)."""
    if not DEEPSEEK_API_KEY:
        print("[AI] DEEPSEEK_API_KEY не задан")
        return None

    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "Ты эксперт B2B-квалификации в Numa Studio. Отвечай строго в JSON."},
            {"role": "user", "content": build_prompt(lead)},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }

    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            resp = requests.post(DEEPSEEK_URL, headers=headers, json=payload, timeout=30)
            if resp.status_code == 200:
                data = _parse_json(resp.json()["choices"][0]["message"]["content"])
                result = apply_result(lead, data) if isinstance(data, dict) else None
                if result:
                    return result
                print("[AI] Некорректный ответ модели")
            elif resp.status_code in (401, 402, 403):
                print(f"[AI] Ошибка доступа/баланса DeepSeek ({resp.status_code}): {resp.text[:120]}")
                return None  # повторы бессмысленны
            else:
                print(f"[AI] Код {resp.status_code}")
        except (requests.RequestException, KeyError, ValueError) as e:
            print(f"[AI] Ошибка запроса: {e}")
        if attempt < len(RETRY_DELAYS):
            time.sleep(RETRY_DELAYS[attempt])
    return None
