"""Пайплайн поиска и квалификации лидов Numa Studio.

Запуск:
  python main.py                      обычный запуск: следующий по очереди округ Москвы
  python main.py --dry                только посмотреть, сколько компаний найдено (без сайтов, ИИ и записи в базу)
  python main.py --district "Южный административный округ"
  python main.py --categories "ресторан,сауна"
"""
import argparse
import json
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv

load_dotenv(override=True)

from yandex_parser import fetch_candidates, CATEGORIES, DISTRICTS
from filter import filter_lead, quick_reject
import ai_qualifier
import notify
from ai_qualifier import qualify_lead_with_ai, DEEPSEEK_API_KEY
from nocodb_client import send_lead_to_nocodb, get_existing_leads_keys, count_new_leads
from utils import phone_key, phone_keys, norm_name, site_key

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "state.json")
REJECTED_FILE = os.path.join(HERE, "rejected.json")

TARGET_NEW_PER_CATEGORY = 15   # сколько НОВЫХ лидов добавить на категорию за запуск
MAX_CHECK_PER_CATEGORY = 80    # сколько кандидатов проверять (сайт + ИИ) на категорию
WORKERS = 3                    # параллельные проверки (Raspberry Pi 3 тянет)
MAX_AI_FAILS_IN_ROW = 5        # столько сбоев ИИ подряд — останавливаем запуск
PAUSE_BETWEEN_QUERIES = 3      # пауза между запросами к Overpass, секунд
POOL_TARGET = int(os.getenv("POOL_TARGET", "0"))   # 0 = искать всегда; число N = не искать, пока свободных лидов N и больше
# Лимиты можно менять в админке Mini App: они лежат в scraper_settings.json и подхватываются при каждом запуске
try:
    with open(os.path.join(HERE, "scraper_settings.json"), encoding="utf-8") as _f:
        _cfg = json.load(_f)
    TARGET_NEW_PER_CATEGORY = max(1, int(_cfg.get("target_new", TARGET_NEW_PER_CATEGORY)))
    MAX_CHECK_PER_CATEGORY = max(1, int(_cfg.get("max_check", MAX_CHECK_PER_CATEGORY)))
    WORKERS = max(1, int(_cfg.get("workers", WORKERS)))
    if "POOL_TARGET" not in os.environ:
        POOL_TARGET = max(0, int(_cfg.get("pool_target", POOL_TARGET)))
except (OSError, ValueError, TypeError):
    pass
ALERT_COOLDOWN = 6 * 3600      # одну и ту же тревогу шлём не чаще раза в 6 часов


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def alert(state: dict, kind: str, text: str):
    """Тревога админам в Telegram, не чаще раза в ALERT_COOLDOWN на каждый вид."""
    alerts = state.setdefault("alerts", {})
    if time.time() - alerts.get(kind, 0) < ALERT_COOLDOWN:
        return
    alerts[kind] = time.time()
    save_json(STATE_FILE, state)
    notify.send_admin(text)


def process(raw: dict):
    """Проверка сайта и квалификация одного кандидата (выполняется в потоке)."""
    try:
        return _process(raw)
    except Exception as e:  # один сбойный кандидат не должен ронять весь запуск
        return {"kind": "skip", "reason": f"внутренняя ошибка: {e}", "permanent": False}


def _process(raw: dict):
    ok, lead = filter_lead(raw)
    if not ok:
        return {"kind": "skip", "reason": lead["reason"], "permanent": lead["permanent"]}
    qualified = qualify_lead_with_ai(lead)
    if qualified is None:
        return {"kind": "ai_fail"}
    if qualified["tier"] == "D":
        return {"kind": "skip", "reason": "ИИ: не целевой клиент (Tier D)", "permanent": True}
    qualified["status"] = "New"
    return {"kind": "ok", "lead": qualified}


def run_pipeline(district=None, categories=None, dry=False, force=False):
    state = load_json(STATE_FILE, {"district_idx": 0})
    auto_district = district is None
    district = district or DISTRICTS[state.get("district_idx", 0) % len(DISTRICTS)]
    categories = categories or CATEGORIES

    print(f"\n=== ПАЙПЛАЙН NUMA STUDIO {'(DRY-RUN) ' if dry else ''}===")
    print(f"Округ: {district} | Категорий: {len(categories)} | Цель: {TARGET_NEW_PER_CATEGORY} новых лидов на категорию\n")

    if not dry and not DEEPSEEK_API_KEY:
        print("[Стоп] DEEPSEEK_API_KEY не задан в .env — квалификация невозможна.")
        alert(state, "deepseek", "⚠️ Парсер лидов остановлен: в .env нет DEEPSEEK_API_KEY.")
        return
    ai_qualifier.LAST_FATAL = False

    names, phones, db_ok = get_existing_leads_keys()
    if not db_ok and not dry:
        print("[Стоп] Не удалось прочитать базу NocoDB: без неё не отличить новые лиды от дублей.")
        alert(state, "nocodb", "⚠️ Парсер лидов остановлен: NocoDB не отвечает.")
        return
    if not dry and not force and POOL_TARGET > 0:
        pool = count_new_leads()
        if pool is not None and pool >= POOL_TARGET:
            print(f"[Пропуск] Свободных лидов {pool} (цель {POOL_TARGET}): база и так полная, новый поиск не нужен.")
            return
        print(f"Свободных лидов сейчас: {pool if pool is not None else 'неизвестно'} (цель {POOL_TARGET})")
    rejected = set(load_json(REJECTED_FILE, []))

    started = time.time()
    totals = {"added": 0, "dup": 0, "rejected": 0, "checked": 0, "tier": {}, "fetched": 0, "failed_fetch": 0}
    ai_fails = 0
    stop = False

    for cat_idx, category in enumerate(categories, 1):
        if stop:
            break
        print(f"\n[{cat_idx}/{len(categories)}] {category}")
        candidates = fetch_candidates(category, district)
        time.sleep(PAUSE_BETWEEN_QUERIES)
        if candidates is None:
            print("  Overpass недоступен, категория пропущена")
            totals["failed_fetch"] += 1
            continue
        totals["fetched"] += 1

        fresh, n_dup, n_rej = [], 0, 0
        seen_now = set()
        for c in candidates:
            key = phone_key(c["phone"])
            ids = set(phone_keys(c["phone"]))
            nm, site = norm_name(c["company_name"]), site_key(c.get("site_url"))
            if nm:
                ids.add("name:" + nm)
            if site:
                ids.add(site)
            if (nm and nm in names) or (site and site in names) or any(k in phones for k in phone_keys(c["phone"])) or ids & seen_now:
                n_dup += 1
            elif (key and key in rejected) or quick_reject(c):
                n_rej += 1
            else:
                fresh.append(c)
                seen_now |= ids
        totals["dup"] += n_dup
        totals["rejected"] += n_rej

        no_site = sum(1 for c in fresh if not c["site_url"])
        print(f"  Найдено с телефоном: {len(candidates)} | уже в базе: {n_dup} | отсев по названию/сетям: {n_rej} | "
              f"кандидатов: {len(fresh)} (без сайта в OSM: {no_site})")
        if dry:
            continue

        # сначала те, у кого сайта в OSM нет: среди них больше Tier A; внутри групп порядок случайный
        random.shuffle(fresh)
        fresh.sort(key=lambda c: bool(c["site_url"]))
        fresh = fresh[:MAX_CHECK_PER_CATEGORY]

        added = 0
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for start in range(0, len(fresh), WORKERS * 2):
                if added >= TARGET_NEW_PER_CATEGORY or stop:
                    break
                batch = fresh[start:start + WORKERS * 2]
                for raw, res in zip(batch, pool.map(process, batch)):
                    totals["checked"] += 1
                    name = raw["company_name"]
                    if res["kind"] == "ai_fail":
                        ai_fails += 1
                        print(f"  [ИИ недоступен] {name}")
                        if ai_fails >= MAX_AI_FAILS_IN_ROW:
                            print("[Стоп] DeepSeek не отвечает несколько раз подряд — останавливаю запуск.")
                            stop = True
                        continue
                    ai_fails = 0
                    if res["kind"] == "skip":
                        print(f"  [Пропуск] {name}: {res['reason']}")
                        if res["permanent"]:
                            rejected.add(phone_key(raw["phone"]))
                            totals["rejected"] += 1
                        continue
                    lead = res["lead"]
                    if added >= TARGET_NEW_PER_CATEGORY:
                        continue  # уже набрали; этот лид останется для следующего запуска
                    if send_lead_to_nocodb(lead):
                        added += 1
                        totals["added"] += 1
                        totals["tier"][lead["tier"]] = totals["tier"].get(lead["tier"], 0) + 1
                        names.add(norm_name(name))
                        if site_key(lead.get("web_url")):
                            names.add(site_key(lead.get("web_url")))
                        phones.update(phone_keys(lead["phone"]))
        print(f"  Добавлено: {added}")
        save_json(REJECTED_FILE, sorted(rejected))

    if not dry:
        save_json(REJECTED_FILE, sorted(rejected))
        if stop and ai_qualifier.LAST_FATAL:
            alert(state, "deepseek", "⚠️ DeepSeek отклоняет запросы парсера (ключ или баланс). Проверьте аккаунт DeepSeek: пополнение базы остановлено.")
        elif stop:
            alert(state, "deepseek", "⚠️ DeepSeek не отвечает несколько раз подряд, запуск парсера прерван. Попробую снова по расписанию.")
        if totals["fetched"] == 0 and totals["failed_fetch"]:
            alert(state, "overpass", "⚠️ Серверы карт (Overpass) не отвечают, парсер не смог найти компании. Попробую снова по расписанию.")
        # следующий запуск пойдёт в следующий округ; если запуск оборвался или Overpass почти не отвечал, повторим этот же
        if auto_district and not stop and totals["fetched"] >= max(1, len(categories) // 2):
            state["district_idx"] = (state.get("district_idx", 0) + 1) % len(DISTRICTS)
            save_json(STATE_FILE, state)

    minutes = (time.time() - started) / 60
    print("\n=== ГОТОВО ===")
    print(f"Округ: {district} | время: {minutes:.1f} мин")
    print(f"Категорий обработано: {totals['fetched']}, не загрузилось: {totals['failed_fetch']}")
    print(f"Дубликатов: {totals['dup']} | отсеяно: {totals['rejected']} | проверено: {totals['checked']}")
    if not dry:
        tiers = ", ".join(f"{k}: {v}" for k, v in sorted(totals["tier"].items())) or "—"
        print(f"ДОБАВЛЕНО НОВЫХ ЛИДОВ: {totals['added']} ({tiers})")
        if totals["added"]:
            pool_after = count_new_leads()
            notify.send_admin(
                f"🤖 База пополнена: +{totals['added']} лидов ({tiers}).\n"
                f"Округ: {district}\nСвободных лидов в пуле: {pool_after if pool_after is not None else '?'}"
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true", help="только посчитать кандидатов")
    parser.add_argument("--district", help="конкретный округ вместо очереди")
    parser.add_argument("--categories", help="список категорий через запятую")
    parser.add_argument("--force", action="store_true", help="искать, даже если свободных лидов уже достаточно")
    args = parser.parse_args()
    cats = [c.strip() for c in args.categories.split(",")] if args.categories else None
    run_pipeline(district=args.district, categories=cats, dry=args.dry, force=args.force)
