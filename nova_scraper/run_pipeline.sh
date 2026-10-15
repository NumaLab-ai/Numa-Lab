#!/bin/bash

# Переходим в директорию проекта
cd /mnt/ssd_data/nova_scraper

# Активируем виртуальное окружение Python
source venv/bin/activate

# Простая ротация лога: больше 5 МБ -> старый лог переезжает в pipeline.log.1
if [ -f pipeline.log ] && [ "$(stat -c%s pipeline.log)" -gt 5242880 ]; then
    mv -f pipeline.log pipeline.log.1
fi

# Запускаем пайплайн и логируем вывод с меткой времени
echo "=== [$(date)] Автоматический запуск пайплайна ===" >> pipeline.log
python main.py >> pipeline.log 2>&1
echo "=== [$(date)] Запуск завершен ===" >> pipeline.log
