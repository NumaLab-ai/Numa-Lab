import os
import time
from dotenv import load_dotenv

load_dotenv(override=True)

from yandex_parser import search_yandex_leads
from filter import filter_lead
from ai_qualifier import qualify_lead_with_ai
from nocodb_client import send_lead_to_nocodb, get_existing_leads_keys

CATEGORIES = [
    "салон красоты",
    "парикмахерская",
    "ресторан",
    "фитнес клуб",
    "барбершоп",
    "стоматология",
    "автосервис",
    "автомойка",
    "ветклиника",
    "косметология",
    "автошкола",
    "детский центр",
    "языковая школа",
    "медицинский центр",
    "тату салон",
    "массажный салон",
    "студия маникюра",
    "детейлинг",
    "шиномонтаж",
    "фотостудия",
    "сауна",
    "студия танцев",
    "ремонт телефонов",
    "клининг"
]

CITY = "Москва"
LIMIT_PER_CATEGORY = 15

def run_multicategory_pipeline():
    print(f"\n=== ЗАПУСК АВТОНОМНОГО ПАЙПЛАЙНА NUMA STUDIO ===")
    print(f"Город: {CITY} | Категорий: {len(CATEGORIES)} | Лимит: {LIMIT_PER_CATEGORY}/кат.\n")

    existing_names, existing_phones = get_existing_leads_keys()

    total_added = 0
    total_skipped = 0

    for cat_idx, category in enumerate(CATEGORIES, 1):
        print(f"\n==========================================")
        print(f"[{cat_idx}/{len(CATEGORIES)}] Поиск по категории: '{category}'")
        print(f"==========================================")

        raw_leads = search_yandex_leads(query=category, city=CITY, limit=LIMIT_PER_CATEGORY)

        for idx, raw_lead in enumerate(raw_leads, 1):
            company_name = raw_lead.get("company_name", "").strip()
            phone = raw_lead.get("phone", "").strip()

            print(f"\n--- Лид {idx}/{len(raw_leads)}: {company_name} ---")

            if company_name.lower() in existing_names or (phone and phone in existing_phones):
                print(f"[Дубликат] Пропущен: Компания '{company_name}' уже есть в NocoDB.")
                total_skipped += 1
                continue

            is_valid, processed_lead = filter_lead(raw_lead)
            if not is_valid:
                print(f"[Фильтр] Отклонен: {processed_lead.get('reason')}")
                total_skipped += 1
                continue

            phone_str = processed_lead['phone'] or 'Нет'
            web_url_val = processed_lead.get('web_url', '')
            web_status = f"Есть ({web_url_val})" if processed_lead.get('has_website') else "Нет"
            print(f"[Фильтр] Пройден. Тел: {phone_str}, Сайт: {web_status}")

            qualified_lead = qualify_lead_with_ai(processed_lead)
            tier = qualified_lead.get('tier')
            
            print(f"[AI] Tier: {tier}")

            if tier == 'D':
                print(f"[AI] Отклонен: Крупная сеть или не целевой клиент.")
                total_skipped += 1
                continue

            print(f"[AI] Оффер: {qualified_lead.get('ai_offer', '')[:90]}...")

            qualified_lead['status'] = "New"
            if send_lead_to_nocodb(qualified_lead):
                total_added += 1
                existing_names.add(company_name.lower())
                if phone:
                    existing_phones.add(phone)

            time.sleep(1)

    print(f"\n=== РАБОТА ЗАВЕРШЕНА ===")
    print(f"Добавлено новых целевых лидов: {total_added}")
    print(f"Пропущено дубликатов / нецелевых: {total_skipped}")

if __name__ == "__main__":
    run_multicategory_pipeline()
