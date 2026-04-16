FROM python:3.11-slim

<<<<<<< HEAD
=======
# Оставляем только ffmpeg и шрифты для PDF
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    wkhtmltopdf \
    fonts-liberation \
    xfonts-75dpi \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Установка зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование кода
COPY . .

# Создание директорий (важно для монтирования томов)
RUN mkdir -p /data/incoming /data/output /data/temp

>>>>>>> 806b0bf6028af9035da04d21e9195fdad955fc9b
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-liberation \
    xfonts-75dpi \
    wget \
    ca-certificates \
    && wget https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-2/wkhtmltopdf_0.12.6.1-2.bullseye_amd64.deb \
    && apt-get install -y ./wkhtmltopdf_0.12.6.1-2.bullseye_amd64.deb \
    && rm wkhtmltopdf_0.12.6.1-2.bullseye_amd64.deb \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

CMD ["python", "main.py"]