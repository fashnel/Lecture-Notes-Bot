# 🎓 Lecture-Notes-Bot

> Бот, который превращает запись лекции в структурированный PDF-конспект.  
> Отправь `.webm` — получи готовый документ через несколько минут.

---

## ✨ Как это работает

```
Пользователь                 ffmpeg-worker          api-worker                pdf-worker
     │                            │                      │                         │
     │  отправляет .webm          │                      │                         │
     │ ──────────────────────────►│                      │                         │
     │                            │  режет на чанки      │                         │
     │                            │  ≤50 мин, .ogg 16kHz │                         │
     │                            │  моно, 12kbps <5MB   │                         │
     │                            │                      │                         │
     │                            │ chunk_1.ogg ────────►│                         │
     │                            │ chunk_2.ogg ────────►│ Whisper транскрибация   │
     │                            │ ...       (конвейер) │ LLaMA суммаризация      │
     │                            │                      │ генерация HTML          │
     │                            │                      │ ──────────────────────► │
     │                            │                      │                         │  WeasyPrint
     │◄────────────────────────────────────────────────────────────────────────────│  HTML → PDF
     │  готовый PDF-конспект                                                       │
```

Воркеры общаются через папку `shared-data/` и JSON-файлы статусов — никаких брокеров, только файловая система.

---

## 🗂 Структура проекта

```
Lecture-Notes-Bot/
├── ffmpeg-worker/          # Нарезка .webm на чанки, сжатие в .ogg
├── api-worker/             # Транскрибация (Whisper) + суммаризация (LLaMA) + HTML
├── pdf-worker/             # Конвертация HTML → PDF через WeasyPrint
├── shared-data/
│   ├── input/              # Входные .webm файлы
│   ├── chunks/{task_id}/   # .ogg чанки
│   ├── status/{task_id}.json  # Статусы задач
│   └── results/            # Готовые .html и .pdf
├── docker-compose.yml
└── .env.example
```

---

## 📊 Статусы задачи

Каждый файл `shared-data/status/{task_id}.json` проходит через цепочку:

```
ffmpeg_processing → ffmpeg_done → transcribing → summarizing
                                                       ↓
         done ← generating_pdf ← html_done ← generating_html
```

---

## 🛠 Стек

| Компонент         | Технология                                 |
|-------------------|--------------------------------------------|
| HTTP-клиент       | `httpx` (с поддержкой прокси через `.env`) |
| Транскрибация     | Groq API — Whisper                         |
| Суммаризация      | Groq API - LLaMA 3                         |
| Конвертация аудио | FFmpeg — моно, 16kHz, 12kbps, < 5MB        |
| Генерация PDF     | WeasyPrint                                 |
| Окружение         | Docker Compose                             | 
| Конфиг            | python-dotenv                              |

---

## 🔑 BYOK — Bring Your Own Key

Бот работает по модели **BYOK**: каждый пользователь вставляет свой [Groq API ключ](https://console.groq.com/keys) при первом запуске.

---

## 🚀 Деплой

### 1. Клонируй репозиторий

```bash
git clone https://github.com/fashnel/Lecture-Notes-Bot.git
cd Lecture-Notes-Bot
```

### 2. Создай `.env` из примера

```bash
cp .env.example .env
```

Заполни переменные:

```env
GROQ_API_KEY=your_api_key

# Прокси для Groq API (если нужен)
HTTP_PROXY=http://user:pass@host:port
HTTPS_PROXY=http://user:pass@host:port
```

### 3. Запусти

```bash
docker compose up --build -d
```

### 4. Проверь логи

```bash
docker compose logs -f
```

---

## 💻 Минимальные требования

Бот оптимизирован для self-hosted деплоя на слабом железе:

- **CPU:** 2 ядра (тестировалось на Celeron E3200)
- **RAM:** 2 GB
- **OS:** Linux (Debian/Ubuntu)

> WeasyPrint намеренно выбран вместо Puppeteer/Chromium — потребление памяти на порядок ниже.

---

## 📄 Пример результата

Ниже — PDF-конспект, сгенерированный из записи лекции по "Основам работы в ОС Linux".

**[📥 Скачать пример конспекта (PDF)]([docs/example_output.pdf](https://github.com/fashnel/Lecture-Notes-Bot/releases/download/untagged-08e8b418bdc30e8c3ec8/Linux_summary.pdf))**

> _Лекция: Деревья поиска — обходы, поиск, вставка, удаление узлов._

---

## 🗺 Roadmap

- [ ] Обернуть серверное взаимодействие в удобное интерфейс с телеграм ботом
- [ ] Redis-очередь с AOF-персистентностью вместо файловых статусов
- [ ] Поддержка `.mp4` и `.mp3` на входе
- [ ] Веб-интерфейс для просмотра истории конспектов

---

## 📝 Лицензия

MIT
