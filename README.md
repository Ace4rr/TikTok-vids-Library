# TokLib — личная библиотека сохранённых TikTok-видео

> **Статус: Фаза 1 (MVP) в разработке.** Реализовано ядро backend-конвейера (инбокс → метаданные → скачивание → Tier-0 теги), API, веб-панель, очередь, Docker — 84 теста зелёные. Полный AI-слой (STT/LLM/эмбеддинги) — Фаза 2. Решения по реализации: `docs/09-implementation-plan.md`. **Основной режим — self-hosted** (решение Р1, `docs/06`).

**Проблема.** Сохранённые видео в TikTok копятся сотнями и тысячами в виде плоской очереди. Искать, пересматривать и систематизировать их невозможно: встроенные коллекции неудобны, видео удаляются авторами, а «зачем я это сохранил» забывается через неделю.

**Решение.** Мини-приложение-библиотека: пересылка ссылки из TikTok в один тап → автоматическая загрузка, транскрипция, AI-теги, краткое содержание и ключевая идея → папки, теги, семантический поиск, свои заметки → интеграция с домашними хранилищами (NAS/Jellyfin) и Home Assistant.

## Документация

| Документ | Содержание |
|---|---|
| [`docs/01-concept.md`](docs/01-concept.md) | Проблема, персоны, 4 концепции продукта, сравнение и рекомендация |
| [`docs/02-market-analysis.md`](docs/02-market-analysis.md) | Рынок, конкуренты, позиционирование, монетизация, риски |
| [`docs/03-tz.md`](docs/03-tz.md) | Техническое задание: функциональные и нефункциональные требования, критерии приёмки MVP |
| [`docs/04-pipeline-roadmap.md`](docs/04-pipeline-roadmap.md) | Пайплайны действий и обработки данных, дорожная карта по фазам |
| [`docs/05-architecture.md`](docs/05-architecture.md) | Архитектура, модель данных, API, стек, интеграции |
| [`docs/06-critique-and-decisions.md`](docs/06-critique-and-decisions.md) | Критический разбор идей (здравость/актуальность/применимость), решения ревизии v0.2 |
| [`docs/07-ai-pipeline.md`](docs/07-ai-pipeline.md) | AI-пайплайн: каскадная обработка, экономия токенов и расходов, бюджеты |
| [`docs/08-ai-providers.md`](docs/08-ai-providers.md) | Провайдеры AI (STT/LLM/эмбеддинги), маршрутизация, пресеты железа |
| [`docs/09-implementation-plan.md`](docs/09-implementation-plan.md) | План реализации: решения на уровне кода, структура репозитория, отклонения |

## Быстрый старт (self-hosted)

```bash
cp .env.example .env          # задайте TOKLIB_TOKEN
docker compose up --build     # один контейнер: API + воркеры + веб-панель
# → http://127.0.0.1:8000  (токен запросят в UI)
```

Локальная разработка без Docker: `uv sync --all-packages`, затем
`TOKLIB_TOKEN=dev uv run --all-packages python -m toklib_api.main`
(веб: `cd apps/web && npm install && npm run dev` — проксирует `/api` на :8000).

Диагностика: `uv run --all-packages toklib doctor`. Telegram-бот:
`uv sync --all-packages --extra bot && python -m toklib_api.bot`.

## Рекомендованная концепция (кратко)

Гибридная, поэтапная: **MVP как мобильный «инбокс-компаньон» (Концепция A)** с self-hosted-совместимым ядром → **AI-слой «второй мозг» (Концепция D)** → **Docker-образ и интеграции с NAS/Home Assistant (Концепция B)**. Браузерное расширение (Концепция C) — опционально позже. Обоснование — в `docs/01-concept.md`; развёртывание — self-hosted в первую очередь (решение Р1 в `docs/06`), SaaS — опциональный путь того же кода.

## Структура репозитория

```
apps/
  api/            # toklib-api: FastAPI + SSE, запуск воркеров, CLI (doctor), Telegram-бот
  web/            # Next.js (static export) — веб-панель; раздаётся FastAPI в одном контейнере
  mobile/         # Flutter-клиент (Фаза 2; до неё — бот + PWA)
services/
  ingest/         # toklib-ingest: резолв метаданных (oEmbed → yt-dlp), DownloadProvider
  ai/             # toklib-ai: Tier-0 словарь, Transcriber/TextAnalyzer/Embedder, роутер
integrations/
  homeassistant/  # Кастомная интеграция HA (Фаза 3)
packages/
  shared/         # toklib-core: конфиг, схемы, БД, очередь, нормализация, oEmbed, FTS-поиск
infra → корень    # Dockerfile, docker-compose.yml, config.example.yaml, CI (.github)
docs/             # Проектная документация
```

Сборка/тесты/линт: `uv sync --all-packages && uv run --all-packages pytest && uv run ruff check .`
