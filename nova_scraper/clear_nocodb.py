import os
import requests
from dotenv import load_dotenv

load_dotenv(override=True)

NOCODB_URL = os.getenv("NOCODB_URL", "http://localhost:8080").strip()
NOCODB_TABLE_ID = os.getenv("NOCODB_TABLE_ID", "motbvyatc3zwyu3").strip()
NOCODB_TOKEN = os.getenv("NOCODB_TOKEN", "").strip()

def clear_all_records():
    if not NOCODB_TOKEN:
        print("[Error] NOCODB_TOKEN не найден в .env!")
        return

    url = f"{NOCODB_URL}/api/v2/tables/{NOCODB_TABLE_ID}/records"
    headers = {"xc-token": NOCODB_TOKEN, "Content-Type": "application/json"}

    try:
        # 1. Загружаем список всех существующих ID
        resp = requests.get(url, headers=headers, params={"limit": 1000, "fields": "Id"}, timeout=10)
        if resp.status_code != 200:
            print(f"[Error] Не удалось получить данные: {resp.status_code} - {resp.text}")
            return

        records = resp.json().get("list", [])
        if not records:
            print("[NocoDB] Таблица уже пуста.")
            return

        print(f"[NocoDB] Найдено {len(records)} записей для удаления.")

        # 2. Формируем массив объектов с Id для массового удаления
        payload = [{"Id": r["Id"]} for r in records if "Id" in r]

        # Удаляем пачками по 100 записей
        for i in range(0, len(payload), 100):
            batch = payload[i:i+100]
            del_resp = requests.delete(url, headers=headers, json=batch, timeout=10)
            if del_resp.status_code in [200, 204]:
                print(f"[NocoDB] Удалено {len(batch)} записей...")
            else:
                print(f"[NocoDB Error] Ошибка удаления: {del_resp.status_code} - {del_resp.text}")

        print("\n[Успех] Таблица NocoDB полностью очищена!")

    except Exception as e:
        print(f"[Exception] Ошибка очистки: {e}")

if __name__ == "__main__":
    clear_all_records()
