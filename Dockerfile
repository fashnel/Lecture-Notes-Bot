FROM python:3.11-slim

# Оставляем только ffmpeg и шрифты для PDF
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Установка зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование кода
COPY . .

# Создание директорий (важно для монтирования томов)
RUN mkdir -p /data/incoming /data/output /data/temp

ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]