#!/bin/bash
# Автономный запуск парсера лидов. Ставится в cron каждые несколько часов:
# парсер сам решает, нужен ли поиск (смотрит на число свободных лидов в базе).

cd /mnt/ssd_data/nova_scraper || exit 1
source venv/bin/activate

# Простая ротация лога: больше 5 МБ -> старый лог переезжает в pipeline.log.1
if [ -f pipeline.log ] && [ "$(stat -c%s pipeline.log)" -gt 5242880 ]; then
    mv -f pipeline.log pipeline.log.1
fi

# flock: второй запуск не стартует, пока идёт первый.
# timeout: зависший запуск принудительно снимается через 3 часа.
# nice: парсер не отнимает процессор у бота и NocoDB.
{
    echo "=== [$(date)] Запуск пайплайна ==="
    flock -n 9 || { echo "Предыдущий запуск ещё идёт, пропускаю."; exit 0; }
    nice -n 10 timeout 3h python main.py
    echo "=== [$(date)] Запуск завершён (код $?) ==="
} 9>/tmp/numa_pipeline.lock >> pipeline.log 2>&1
