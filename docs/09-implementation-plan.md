# 9. План реализации (уровень кода)

**Дата:** 2026-11-01. **Статус:** принят, реализация начата (Фаза 1, MVP).
**Назначение:** документы 03–08 фиксируют «что и зачем», но не определяют ряд
решений, без которых нельзя писать код. Этот документ закрывает пробелы и
фиксирует принятые решения. При конфликте с 03–08 приоритет — у этого файла
до его актуализации.

## 9.1. Структура кода и сборка (не определена в docs)

**Решение: uv-workspace, 4 python-пакета.**

```
pyproject.toml            # виртуальный корень workspace; dev-зависимости (pytest, ruff)
packages/shared/          # toklib_core  — общее ядро (см. 9.2)
services/ingest/          # toklib_ingest — резолв метаданных, DownloadProvider, воркеры ingest/download
services/ai/              # toklib_ai    — Tier-0, интерфейсы AI, роутер, провайдеры, воркер ai
apps/api/                 # toklib_api   — FastAPI, SSE, запуск воркеров, CLI (doctor), Telegram-бот
```

- Python 3.12+, управление зависимостями — `uv sync`; линт — `ruff`; тесты — `pytest`.
- Зависимости направлены строго вниз: `toklib_api → toklib_ingest/toklib_ai → toklib_core`.
- Тяжёлые AI-зависимости (`faster-whisper`, `sentence-transformers`, `numpy`) —
  **opt-in extras** (`toklib-ai[stt,embed]`): импортируются лениво и с guard,
  отсутствие = провайдер недоступен, а не падение приложения (NFR-10: деградация, не отказ).
- `yt-dlp` — прямая зависимость `toklib_ingest` (изолирована внутри `DownloadProvider`).
- Один контейнер MVP: `python -m toklib_api.main` поднимает uvicorn + фоновые
  потоки-воркеры (`server.run_workers: true`; выключается для выноса AI-воркера
  на GPU-бокс — тогда второй контейнер с `run_workers: false, ai_only: true`).

## 9.2. Состав toklib_core

`config` (yaml+env, см. 9.3) · `enums` (все статусные перечисления из 5.4/5.9) ·
`schemas` (pydantic-модели API — контракт для web/мобильного/бота) · `db`
(SQLAlchemy-модели по 5.4 + прагмы SQLite) · `queue` (очередь задач, см. 9.4) ·
`normalize` (нормализация ссылок, см. 9.5) · `oembed` (клиент oEmbed, 9.6) ·
`paths` (канонические пути media/tmp по Р5) · `search` (FTS5-индекс и парсер
операторов, 9.7) · `state` (машина статусов конвейера, 9.8).

## 9.3. Конфигурация (5.8/8.5 задают только вид yaml)

- **Файл:** `$TOKLIB_CONFIG` (по умолчанию `<data_dir>/config.yaml`); если нет —
  значения по умолчанию; секреты — только env (`TOKLIB_TOKEN`, `TOKLIB_LLM_KEY`,
  `TOKLIB_STT_KEY`), env перекрывает yaml. Схема — pydantic-модель, невалидный
  конфиг = отказ старта с перечнем ошибок.
- Дополнительно к 8.5 добавлены секции: `server` (host, port, data_dir, token,
  run_workers, ai_only, cors_origins), `download` (provider, cookies_file,
  cookies_from_browser, proxy, cobalt_base_url), `ingest` (allow_extra_hosts),
  `storage` (дисковые пороги 90/95%), `ai.night_window` (01:00–07:00).
- Tier-0 словарь: `<data_dir>/tier0-dictionary.yaml`; при отсутствии копируется
  из пакета (дефолтный словарь в репозитории, правится пользователем).

## 9.4. Очередь задач (5.4 задаёт таблицу, но не семантику)

- Типы задач = стадии конвейера: `resolve`, `download`, `transcribe`, `analyze`,
  `embed`. Воркер, завершив стадию, сам ставит следующую задачу — chain.
- **Захват задачи — один атомарный UPDATE…RETURNING** (SQLite безопасен, lease):
  `status=pending → running`, `locked_by`, `locked_at`, `attempts+1`.
- Приоритеты: `high=0` (открытая карточка/reanalyze), `normal=1` (свежая ссылка),
  `backlog=2` (импорт). Порядок выборки: priority → run_after → id.
- Backoff при повторяемой ошибке: 1/5/30 мин, `max_attempts=5` (5.9); после
  исчерпания — dead-letter: `status=failed`, `video.status=error`,
  `last_error` с причиной; повтор из UI (`POST /api/system/retry/{video_id}`).
- Lease: `lease_seconds=600`; рестарт сервиса возвращает протухшие lease в
  `pending` (reap при старте и периодически).
- Идемпотентность: `idempotency_key = f"{type}:{video_id}"` — повторное
  разрешение дедупа/анализа не создаёт дублей (кэш анализа по
  `(model, prompt_version, input_hash)` — 7.4 №6).
- AI-бюджеты: `analysis_per_day` считается по `analysis.created_at` за сегодня;
  исчерпание → `analyze` уходит в `run_after` на утро (деградация, не затык — 7.6).
  Backlog-приоритет обрабатывается только в ночном окне (если `lazy_backlog`).

## 9.5. Нормализация ссылок (FR-1.3 — правила нигде не расписаны)

1. Из share-текста извлекается первый URL (регэкспом).
2. Allowlist хостов: `tiktok.com`, `www.|m.|vm.|vt.tiktok.com` (+`allow_extra_hosts`).
3. Паттерны: `/@user/video/{id}`, `/@user/photo/{id}`, `/v/{id}`, `/t/{code}`,
   `vm|vt.tiktok.com/{code}` (короткие — id неизвестен), `embed`-URL.
4. `tiktok_id` — только цифры. Короткие ссылки резолвятся позже (worker следует
   redirect и извлекает id) — до этого дедуп по полному URL после редиректа
   невозможен, что честно отражается в `inbox_item`.

## 9.6. Резолв метаданных (Р2, слой 1)

- Источник №1 — публичный `GET https://www.tiktok.com/oembed?url=…`:
  title→caption, author_name/author_url, thumbnail_url→cover. Не зависит от yt-dlp.
- Источник №2 (fallback/дополнение) — `yt-dlp --dump-json` (без скачивания):
  duration, дата публикации, полный caption с хэштегами.
- Ошибки: HTTP 404/видео недоступно → терминальная (`health=dead`); сеть/таймаут →
  повторяемая (backoff). Поле `duration` из oEmbed недоступно — заполняется
  yt-dlp'ом, иначе остаётся NULL (не блокирует карточку).
- Хэштеги парсятся из caption регэкспом `#\p{L}\w*` (без внешних запросов).

## 9.7. Поиск (M7)

- FTS5-таблица `video_fts(video_id UNINDEXED, caption, author, tags, notes, transcript, summary)`,
  внешнее содержимое: приложение синхронизирует строки после resolve/analyze/
  правки заметок/транскрипта (без триггеров — контролируемо и переносимо на PG).
- Парсер запроса: `author:`, `tag:`, `folder:` операторы + свободный текст;
  FTS-запрос по свободной части с rank (BM25). Операторы — фильтры SQL.
- Векторы/sqlite-vec — Фаза 2: слот `embedding` уже в схеме, гибрид RRF
  добавляется к этому же эндпоинту без смены контракта.

## 9.8. Машина статусов (5.9 → код)

- Явная таблица разрешённых переходов + guard-функция; недопустимый переход =
  ошибка воркера (не молча).
- Решение по dead-link: `health=dead`, стадия завершается best-effort →
  `status=ready` (карточка жива, media_mode=none/embed; не `error` — ошибки
  зарезервированы за сбоями пайплайна, а не свойствами контента).
- Деградация AI: после исчерпания попыток STT/LLM → `status=ready` +
  `video.partial_ai=1`, теги Tier-0 сохраняются; повтор — ночью/reanalyze (FR-6.9).
- Дополнения схемы 5.4 (введены кодом, документированы здесь): `video.partial_ai`
  (флаг деградации), `video.deleted_at` (корзина FR-9.2 с восстановлением ≤30 дней),
  `inbox_item.video_id` (связь элемента инбокса с карточкой).

## 9.9. API и аутентификация (5.5)

- Реализуются все эндпоинты 5.5, кроме помеченных: `POST /api/import/data-export`
  и `POST /api/system/reindex` (reindex — enqueue embed-задач, готов) — import
  возвращает 501 до Фазы 3.
- Аутентификация: если `TOKLIB_TOKEN` задан — все `/api/*` требуют
  `Authorization: Bearer <token>`; для медиа допускается `?token=` (теги
  `<video>/<img>` не управляют заголовками). Если токен не задан — режим без
  аутентификации (локальная разработка; в compose токен обязателен).
- Раздача файлов: по id из БД, `FileResponse` c ручным Range (206) для перемотки;
  canonical path вычисляется из `tiktok_id`, пользовательские пути исключены (5.11).
- SSE `GET /api/events`: `video` (id, status, media_mode, health), `inbox`
  (id, status), heartbeat-комментарий 15 с. Мост потоки→asyncio: воркеры живут в
  threads и публикуют через `loop.call_soon_threadsafe`.

## 9.10. Web (5.3 говорит Next.js SSR — уточнение под one-container)

- Next.js 15, **static export** (`output: 'export'`), отдаётся FastAPI как статика —
  выполняет критерий «один контейнер» (5.8) без node-рантайма в проде.
  Отказ от SSR осознанный: лента и так клиентская (PWA), данные через JSON API.
- Дев-режим: `npm run dev` + rewrites `/api` → `localhost:8000`.
- TS-типы контракта — hand-written зеркало pydantic-схем (`src/lib/types.ts`);
  генерация из OpenAPI — потом (пакет shared, README).
- PWA: manifest + базовые иконки; share target — V1.1 (нужен service worker).

## 9.11. Telegram-бот (Р4, MVP-канал)

- `aiogram` v3, отдельный процесс `python -m toklib_api.bot` (long polling).
- Команды: любое сообщение с URL → `POST /api/inbox` (source=tg) → ответ карточкой;
  `/start` — проверка доступа по `TOKLIB_TG_ALLOWED` (id через запятую; пусто = все);
  `/status` — счётчики очереди. Уведомления о готовности — след. итерация
  (подписка бота на SSE).

## 9.12. Миграции и прочие отклонения от спеки

- MVP: `create_all` при старте вместо Alembic (схема ещё движется); Alembic
  подключается при первом изменении схемы после релиза (5.12 — цель, не старт).
- `POST /api/export` — JSON метаданных (видео, теги, заметки, транскрипты, анализ);
  файлы архивом — следующим шагом.
- Телеметрия деградации (5.7) — суточный job считается из `job`-истории
  (`успешных download / всего` за 24 ч) и отдаётся в `/api/system/ai`; алерт — на
  стороне HA-интеграции (Фаза 3).
- Web не собран в этой сессии (нет гарантии сети для npm); backend покрыт тестами.

## 9.13. Срезы реализации (порядок работ)

1. ✅ Ядро: конфиг, схемы, БД, очередь, нормализация, oEmbed — **этот коммит**
2. ✅ Воркеры: resolve → download → Tier-0; AI-каркас с фейками — **этот коммит**
3. ✅ API + SSE + web-каркас + compose — **этот коммит**
4. След.: реальные STT/LLM/эмбеддинги (Фаза 2, спайк на 20 видео — 06.5), Alembic,
   генерация TS-типов из OpenAPI, уведомления бота, data-export импорт, cobalt-тесты.
