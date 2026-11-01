# packages/shared — toklib_core

Общее ядро для api/ingest/ai — **реализовано**:

- `config` — yaml + env-секреты (схема docs/08 §8.5 + секции server/download/ingest/storage);
- `enums` — статус × media_mode × health, типы/приоритеты задач;
- `schemas` — pydantic-контракт API (единый для backend ↔ web ↔ бот);
- `db` — SQLAlchemy-модели по docs/05 §5.4 (+ partial_ai, deleted_at, content_language);
- `queue` — DB-очередь с lease (атомарный UPDATE…RETURNING), backoff, dead-letter, телеметрия скачиваний;
- `normalize` — нормализация ссылок TikTok и allowlist (FR-1.3, SSRF);
- `oembed` — клиент публичного oEmbed (слой 1 платформенной защиты, Р2);
- `paths` — канонические неизменяемые пути media (Р5);
- `search` — FTS5-индекс + операторы `author:/tag:/folder:`;
- `state` — машина переходов статусов (docs/05 §5.9 → код).

Тесты: `uv run --all-packages pytest packages/shared/tests`.
