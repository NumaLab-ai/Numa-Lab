import os
import json
import re
import requests
from dotenv import load_dotenv

load_dotenv(override=True)

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"

def qualify_lead_with_ai(lead: dict) -> dict:
    if not DEEPSEEK_API_KEY:
        lead["tier"] = "B"
        lead["pain_points"] = "Ошибка ключа API"
        lead["ai_offer"] = "Внедрение AI-бота для онлайн-записи от Numa Studio."
        return lead

    company_name = lead.get("company_name", "")
    category = lead.get("category", "")
    city = lead.get("city", "")
    has_website = lead.get("has_website", False)
    web_url = lead.get("web_url", "")
    site_summary = lead.get("site_summary", "Информация о сайте отсутствует.")

    prompt = f"""
Ты — AI-квалификатор лидов для агентства автоматизации Numa Studio.

Компания: {company_name} ({category}, {city})
Сайт: {'Да (' + web_url + ')' if has_website else 'НЕТ САЙТА'}
Данные сайта: {site_summary}

Правила оценки Tier:
- Tier 'A' (ВЫСШИЙ ПРИОРИТЕТ): Частный малый/средний бизнес БЕЗ сайта. Для них AI-бот в Telegram/WhatsApp от Numa Studio — идеальная замена полноценному веб-сайту для записи 24/7 и сбора клиентов.
- Tier 'B' (ВТОРОЙ ПРИОРИТЕТ): Частный бизнес ЕСТЬ сайт, но нет умных AI-ботов или авто-записи в мессенджерах.
- Tier 'C': Малый бизнес, у которого уже идеально настроена автоматизация.
- Tier 'D': НЕ ЦЕЛЕВОЙ КЛИЕНТ. Сюда входят:
  1. Крупные федеральные/городские сети и франшизы (Тануки, Додо, МЕДСИ и т.д.).
  2. Государственные, муниципальные и бюджетные учреждения.

Задачи:
1. tier: Укажи строго 'A', 'B', 'C' или 'D'.
2. pain_points: 2-3 ключевые боли бизнеса (для Tier A — акцент на отсутствие сайта и потерю заявок; для Tier B — на ручную работу администраторов).
3. ai_offer: Персонализированный оффер от Numa Studio.

Ответь ТОЛЬКО валидным JSON без markdown:
{{
  "tier": "A",
  "pain_points": "...",
  "ai_offer": "..."
}}
"""

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "Ты эксперт B2B-квалификации в Numa Studio. Отвечай строго в JSON."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2
    }

    try:
        resp = requests.post(DEEPSEEK_URL, headers=headers, json=payload, timeout=20)
        if resp.status_code == 200:
            content = resp.json()['choices'][0]['message']['content'].strip()
            if content.startswith("```"):
                content = re.sub(r'^```json\s*|^```\s*|```$', '', content, flags=re.MULTILINE).strip()
            data = json.loads(content)
            lead["tier"] = data.get("tier", "B")
            lead["pain_points"] = data.get("pain_points", "")
            lead["ai_offer"] = data.get("ai_offer", "")
        else:
            lead["tier"] = "B"
            lead["pain_points"] = "Ошибка запроса к ИИ"
            lead["ai_offer"] = "Предлагаем AI-бота для онлайн-записи."
    except Exception as e:
        print(f"[AI Exception] {e}")
        lead["tier"] = "B"
        lead["pain_points"] = "Ошибка квалификации"
        lead["ai_offer"] = "Предлагаем AI-бота для коммуникаций с клиентами."

    return lead
