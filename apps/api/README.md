# apps/api — Backend TokLib

REST API + SSE (статусы обработки) + оркестрация очередей — **реализовано**.

- Стек: Python 3.12+, FastAPI, SQLAlchemy 2 (SQLite → PostgreSQL), uv-workspace (`toklib-api`).
- Эндпоинты: `docs/05-architecture.md` §5.5; решения уровня кода — `docs/09-implementation-plan.md`.
- Точка входа одного контейнера MVP: `python -m toklib_api.main` (uvicorn + потоки-воркеры).
- Воркеры: `ingest-worker` (resolve, download) и `ai-worker` (transcribe, analyze, embed) — DB-очередь с lease, backoff 1/5/30 мин, dead-letter и кнопкой повтора из UI.
- CLI: `toklib doctor` / `toklib reindex-embeddings` / `toklib benchmark` (docs/08 §8.6).
- Telegram-бот: `python -m toklib_api.bot` (opt-in extra `bot`).

Запуск в разработке: `uv sync --all-packages && TOKLIB_TOKEN=dev uv run --all-packages python -m toklib_api.main`.
Тесты: `uv run --all-packages pytest apps/api/tests`.

Статус: Фаза 1 (MVP) — приём ссылок, дедуп, конвейер, Tier-0, поиск FTS5, папки/теги/заметки, SSE, auth, Range-раздача файлов. Далее: Alembic, генерация TS-типов из OpenAPI, уведомления бота.
