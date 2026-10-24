"""Однократная подготовка таблиц NocoDB для админки и комиссий.

Запуск (из папки numa_bot, в venv):  python setup_schema.py
Скрипт ничего не удаляет: только добавляет недостающие колонки и проверяет статусы.
"""
import requests
import nocodb

NEEDED = {
    "Users": (nocodb.USERS_TABLE_ID, [
        ("commission_percent", "Decimal"),
        ("referrer_id", "SingleLineText"),
        ("approved_at", "SingleLineText"),
    ]),
    "Leads": (nocodb.LEADS_TABLE_ID, [
        ("deal_amount", "Decimal"),
        ("manager_percent", "Decimal"),
        ("won_at", "SingleLineText"),
        ("last_comment", "LongText"),
        ("referrer_id", "SingleLineText"),
        ("referral_percent", "Decimal"),
    ]),
}
STATUS_VALUES = {
    "Users": ("approved", "pending", "rejected", "banned"),
    "Leads": ("New", "In_Progress", "Postponed", "Lost", "Won"),
}


def meta(table_id):
    r = requests.get(f"{nocodb.NOCODB_URL}/api/v2/meta/tables/{table_id}", headers=nocodb.HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


def main():
    for name, (table_id, wanted) in NEEDED.items():
        print(f"\n== {name} ({table_id}) ==")
        try:
            columns = meta(table_id).get("columns", [])
        except Exception as e:
            print(f"  Не удалось прочитать таблицу: {e}")
            continue
        existing = {(c.get("title") or "").lower(): c for c in columns}
        print("  Колонки:", ", ".join(sorted(existing)))

        for col, uidt in wanted:
            if col.lower() in existing:
                print(f"  ✓ {col} уже есть")
                continue
            r = requests.post(
                f"{nocodb.NOCODB_URL}/api/v2/meta/tables/{table_id}/columns",
                headers=nocodb.HEADERS,
                json={"column_name": col, "title": col, "uidt": uidt},
                timeout=15,
            )
            if r.status_code in (200, 201):
                print(f"  + {col} ({uidt}) создана")
            else:
                print(f"  ✗ {col}: не удалось создать автоматически ({r.status_code}). "
                      f"Создайте вручную в NocoDB: имя {col}, тип {uidt}.")

        status = existing.get("status")
        if status and status.get("uidt") in ("SingleSelect", "MultiSelect"):
            options = {o.get("title") for o in (status.get("colOptions") or {}).get("options", [])}
            missing = [v for v in STATUS_VALUES[name] if v not in options]
            if missing:
                print(f"  ! Колонка status — список. Добавьте варианты: {', '.join(missing)}")
            else:
                print("  ✓ Все нужные значения status есть")
        elif status:
            print(f"  ✓ status — обычное поле ({status.get('uidt')}), новые значения подойдут")


if __name__ == "__main__":
    main()
