# TokLib

[![GitHub Release](https://img.shields.io/github/v/release/Ace4rr/TikTok-vids-Library?style=for-the-badge)](https://github.com/Ace4rr/TikTok-vids-Library/releases/latest)
[![License](https://img.shields.io/github/license/Ace4rr/TikTok-vids-Library?style=for-the-badge)](LICENSE)
[![CI](https://img.shields.io/github/actions/workflow/status/Ace4rr/TikTok-vids-Library/ci.yml?style=for-the-badge&label=CI)](https://github.com/Ace4rr/TikTok-vids-Library/actions/workflows/ci.yml)

[![self-hosted](https://img.shields.io/badge/self--hosted--first-orange.svg?style=for-the-badge)](docs/06-critique-and-decisions.md)

_Личная библиотека сохранённых TikTok-видео: скинул ссылку — получил карточку с тегами, саммари и поиском._

Supported Features:
 * Приём ссылок одним полем: полные URL, короткие `vm./vt.tiktok.com`, share-текст; дедупликация со счётчиком сохранений (FR-1.3/1.4).
 * Фоновый конвейер на DB-очереди (lease / retry / backoff / dead-letter): метаданные через публичный oEmbed → скачивание yt-dlp (cookies, прокси) → словарные автотеги Tier-0. Карточка живёт даже без файла — embed-режим как fallback.
 * Библиотека: папки (вложенность ≤ 2), теги (AI + пользовательские, слияние), заметки, транскрипты, «просмотрено/избранное», фильтры и сортировки.
 * Плеер: локальный файл с Range-перемоткой; при отсутствии файла — официальный embed TikTok.
 * Полнотекстовый поиск FTS5 с операторами `author:`, `tag:`, `folder:` (семантический поиск — Фаза 2).
 * Живые статусы обработки в UI через SSE; честные состояния: «частично обработано (Tier-0)», «мёртвая ссылка», ошибки с причиной и кнопкой повтора.
 * Single-token аутентификация, стриминг файлов только по id из БД, allowlist хостов — SSRF исключён.
 * AI по умолчанию локальный (`local_only`): faster-whisper, Ollama/llama.cpp, sentence-transformers подключаются конфигом без изменения кода (Фаза 2).
 * Каналы приёма: веб-панель, Telegram-бот, REST API.
 * Один контейнер: `docker compose up` поднимает API, воркеры и веб-панель; все данные — в одной папке.

## Установка

### Рекомендуется: Docker compose

1. Склонируйте репозиторий:
   ```bash
   git clone https://github.com/Ace4rr/TikTok-vids-Library.git
   cd TikTok-vids-Library
   ```
2. Создайте `.env` и задайте токен доступа:
   ```bash
   cp .env.example .env
   # в .env: TOKLIB_TOKEN=<ваш секретный токен>
   ```
3. Запустите:
   ```bash
   docker compose up --build -d
   ```
4. Откройте `http://localhost:8000` и введите тот же токен в UI.

Данные (SQLite, медиа, конфиг) живут в `./data` — бэкап = копирование одной папки (NFR-7).

### Альтернативы
<details>
<summary>Из исходников (разработка)</summary>

Требуется Python 3.12+ и [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-packages                          # все пакеты workspace
uv run --all-packages pytest                    # тесты
TOKLIB_TOKEN=dev uv run --all-packages python -m toklib_api.main   # API + воркеры на :8000
```

Веб-панель в dev-режиме (с проксированием `/api` на :8000):

```bash
cd apps/web && npm install && npm run dev       # http://localhost:3000
```

Прод-сборка статики: `npm run build` → каталог `out/` подхватывается сервером через `TOKLIB_WEB_DIST`.

</details>

<details>
<summary>Telegram-бот (канал приёма ссылок)</summary>

```bash
uv sync --all-packages --extra bot
# в .env: TOKLIB_TG_BOT_TOKEN=<токен от @BotFather>, опционально TOKLIB_TG_ALLOWED=<id через запятую>
uv run --all-packages python -m toklib_api.bot
```

Бот принимает ссылку в любом сообщении, `/status` — состояние очереди.

</details>

## Использование

Вставьте ссылку в поле веб-панели — карточка появится через пару минут (метаданные — сразу после резолва). Или отправьте ссылку боту. Или дерните API:

```bash
curl -X POST http://localhost:8000/api/inbox \
     -H "Authorization: Bearer $TOKLIB_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"text": "https://www.tiktok.com/@user/video/123...", "source": "web"}'
```

| Канал | Как | Статус |
|---|---|---|
| Веб-панель | поле ввода + Enter | MVP ✅ |
| Telegram-бот | сообщение со ссылкой, `/status` | MVP ✅ |
| REST API | `POST /api/inbox` (Bearer-токен) | MVP ✅ |
| iOS Shortcuts / Android PWA share | POST на `/api/inbox` | V1.1 |
| Импорт TikTok data export | загрузка JSON-архива | Фаза 3 |

## Конфигурация

Секреты — только через env; остальное — `config.yaml` в папке данных (шаблон: [`config.example.yaml`](config.example.yaml), схема: `docs/08-ai-providers.md` §8.5).

| Переменная | Назначение |
|---|---|
| `TOKLIB_TOKEN` | токен доступа API (single-user); обязателен в compose |
| `TOKLIB_DATA_DIR` | каталог данных (SQLite, media, config.yaml), по умолчанию `./data` |
| `TOKLIB_DATABASE_URL` | PostgreSQL вместо SQLite (SaaS-профиль) |
| `TOKLIB_WEB_DIST` | каталог статики веб-панели (в образе уже задан) |
| `TOKLIB_LLM_KEY` / `TOKLIB_STT_KEY` | ключи облачных AI-провайдеров — только явный opt-in |
| `TOKLIB_TG_BOT_TOKEN` / `TOKLIB_TG_ALLOWED` | Telegram-бот и белый список пользователей |

Основные секции `config.yaml`: `download` (провайдер, cookies, прокси), `storage` (дисковые пороги 90/95%), `ai` (режим `local_only` / `local_first` / `cloud_only`, STT/LLM/эмбеддинги, ночные бюджеты).

## CLI

```bash
uv run --all-packages toklib doctor             # проверка провайдеров, железа, конфига + пресет
uv run --all-packages toklib reindex-embeddings # пересборка векторов после смены модели
uv run --all-packages toklib benchmark --videos 10
```

## Устранение неполадок

* **UI просит токен / 401** — токен в `.env` (`TOKLIB_TOKEN`) и введённый в UI должны совпадать; медиа-эндпоинты принимают и `?token=`.
* **Карточка застряла в «резолвится»** — oEmbed недоступен или ссылка не TikTok; задача вернётся в очередь с backoff, кнопку «Повторить» видно на карточке с ошибкой.
* **Скачивание падает** — TikTok-экстрактор yt-dlp хрупок по своей природе: карточка автоматически живёт в embed-режиме; обновите yt-dlp (`uv sync`) и повторите загрузку.
* **`docker compose up` ругается на `TOKLIB_TOKEN`** — переменная обязательна: заполните `.env`.
* **Нет AI-тегов выше словарных** — режим `local_only` без сконфигурированных моделей: это норма до Фазы 2, `toklib doctor` покажет, чего не хватает (faster-whisper, Ollama, sentence-transformers).
* **Порт 8000 занят** — поменяйте `server.port` в `config.yaml` или маппинг портов в `docker-compose.yml`.

## Документация

| Документ | Содержание |
|---|---|
| [`docs/01-concept.md`](docs/01-concept.md) | Проблема, персоны, 4 концепции продукта, сравнение |
| [`docs/02-market-analysis.md`](docs/02-market-analysis.md) | Рынок, конкуренты, позиционирование |
| [`docs/03-tz.md`](docs/03-tz.md) | ТЗ: функциональные и нефункциональные требования, приёмка MVP |
| [`docs/04-pipeline-roadmap.md`](docs/04-pipeline-roadmap.md) | Пайплайны и дорожная карта по фазам |
| [`docs/05-architecture.md`](docs/05-architecture.md) | Архитектура, модель данных, API, стек |
| [`docs/06-critique-and-decisions.md`](docs/06-critique-and-decisions.md) | Критический разбор, решения ревизии v0.2 (Р1–Р5) |
| [`docs/07-ai-pipeline.md`](docs/07-ai-pipeline.md) | AI-пайплайн: каскад, экономия, бюджеты |
| [`docs/08-ai-providers.md`](docs/08-ai-providers.md) | Провайдеры AI, маршрутизация, пресеты железа |
| [`docs/09-implementation-plan.md`](docs/09-implementation-plan.md) | План реализации: решения на уровне кода |

## Статус

**Фаза 1 (MVP) — ядро готово:** приём ссылок, конвейер, Tier-0, библиотека, поиск, веб-панель, Docker — покрытие тестами, CI зелёный.
**Дальше — Фаза 2 «AI-слой»:** транскрипция (faster-whisper), LLM-теги/саммари одним JSON-вызовом, эмбеддинги и гибридный поиск, бюджеты и ночной бэклог (docs/07–08).
