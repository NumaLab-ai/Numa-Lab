#!/bin/bash

# Переходим в директорию проекта
cd /mnt/ssd_data/nova_scraper

# Активируем виртуальное окружение Python
source venv/bin/activate

# Запускаем пайплайн и логируем вывод с меткой времени
echo "=== [$(date)] Автоматический запуск пайплайна ===" >> pipeline.log
python main.py >> pipeline.log 2>&1
echo "=== [$(date)] Запуск завершен ===" >> pipeline.log
