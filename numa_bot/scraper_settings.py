"""Настройки парсера лидов, которые админ меняет в Mini App.
Хранятся в JSON рядом со скрапером (nova_scraper/scraper_settings.json); парсер читает файл при каждом запуске."""
import json
import os
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.getenv("SCRAPER_SETTINGS_PATH") or os.path.join(HERE, "..", "nova_scraper", "scraper_settings.json")

# ключ: (по умолчанию, минимум, максимум, подпись)
SPEC = {
    "target_new": (15, 1, 100, "Новых лидов на категорию за запуск"),
    "max_check": (80, 10, 500, "Сколько кандидатов проверять (сайт + ИИ) на категорию"),
    "workers": (3, 1, 6, "Параллельных проверок"),
    "pool_target": (0, 0, 10000, "Не искать, пока свободных лидов не меньше N (0 = искать всегда)"),
}


class SettingsError(ValueError):
    pass


def defaults() -> dict:
    return {k: v[0] for k, v in SPEC.items()}


def load() -> dict:
    data = defaults()
    try:
        with open(PATH, encoding="utf-8") as f:
            raw = json.load(f)
        for k, (d, lo, hi, _) in SPEC.items():
            if k in raw:
                data[k] = min(hi, max(lo, int(raw[k])))
    except (OSError, ValueError, TypeError):
        pass
    return data


def validate(values: dict) -> dict:
    out = {}
    for k, v in values.items():
        if k not in SPEC:
            raise SettingsError(f"Неизвестная настройка: {k}")
        _, lo, hi, label = SPEC[k]
        try:
            n = int(float(str(v).replace(",", ".").strip()))
        except ValueError:
            raise SettingsError(f"«{label}»: нужно число")
        if not lo <= n <= hi:
            raise SettingsError(f"«{label}»: от {lo} до {hi}")
        out[k] = n
    return out


def save(values: dict) -> dict:
    data = load()
    data.update(validate(values))
    d = os.path.dirname(os.path.abspath(PATH))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PATH)
    return data


def describe() -> list:
    cur = load()
    return [{"key": k, "label": s[3], "value": cur[k], "min": s[1], "max": s[2], "default": s[0]} for k, s in SPEC.items()]
