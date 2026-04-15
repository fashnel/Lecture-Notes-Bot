# Lecture Notes Bot

Python-воркер для автоматической обработки видеолекций (.webm) в PDF-конспекты.

## Архитектура

```
.webm → FFmpeg → .wav → Faster-Whisper → Текст → DeepSeek API → Markdown → WeasyPrint → PDF
```

## Компоненты

| Этап | Технология |
|------|-----------|
| Мониторинг папки | Watchdog |
| Извлечение аудио | FFmpeg (16kHz, mono) |
| Транскрибация | Faster-Whisper (tiny, int8) |
| Генерация конспекта | DeepSeek API (OpenAI-compatible) |
| Генерация PDF | markdown2 + WeasyPrint |
| Retry логика | Tenacity |
| Конфигурация | Pydantic Settings |

## Быстрый старт

### 1. Настройка окружения

```bash
cp .env.example .env
# Отредактируйте .env, указав ваш DeepSeek API ключ
nano .env
```

### 2. Запуск через Docker Compose

```bash
docker compose up -d --build
```

### 3. Использование

Скопируйте `.webm` файл в папку `data/incoming/`:

```bash
cp lecture.webm ./data/incoming/
```

Воркер автоматически:
1. Извлечёт аудио
2. Транскрибирует речь
3. Создаст конспект через LLM
4. Сохранит PDF в `data/output/`

### Локальный запуск (без Docker)

```bash
# Системные зависимости (Ubuntu/Debian)
sudo apt install ffmpeg libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf2.0-0

# Python зависимости
pip install -r requirements.txt

# Запуск
python main.py
```

## Структура проекта

```
LectureNotesBot/
├── main.py          # Точка входа, watchdog, очередь
├── config.py        # Pydantic настройки
├── pipeline.py      # Пайплайн обработки
├── requirements.txt # Зависимости
├── Dockerfile       # Образ контейнера
├── docker-compose.yml
├── .env.example     # Пример конфигурации
└── README.md
```

## Переменные окружения

| Переменная | Описание | По умолчанию |
|-----------|----------|-------------|
| `DEEPSEEK_API_KEY` | API ключ DeepSeek | **Обязательно** |
| `DEEPSEEK_API_URL` | URL API | `https://api.deepseek.com/v1/chat/completions` |
| `DEEPSEEK_MODEL` | Модель | `deepseek-chat` |
| `INCOMING_DIR` | Папка входящих | `/data/incoming` |
| `OUTPUT_DIR` | Папка результатов | `/data/output` |
| `TEMP_DIR` | Временная папка | `/data/temp` |
| `WHISPER_MODEL_SIZE` | Модель Whisper | `tiny` |
| `WHISPER_DEVICE` | Устройство | `cpu` |
| `WHISPER_COMPUTE_TYPE` | Тип вычислений | `int8` |
| `WHISPER_CPU_THREADS` | Потоки CPU | `2` |

## Оптимизация для слабых серверов

- Модель Whisper `tiny` с квантованием `int8`
- Ограничение CPU threads = 2
- Обработка по одному файлу (очередь)
- Автоматическая очистка временных файлов
- Docker resource limits (1.5G RAM, 1.5 CPU)

## Лицензия

MIT
