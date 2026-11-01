# services/ingest — Приём и загрузка

Воркеры обработки ссылок: нормализация URL → резолв метаданных → скачивание файла — **реализовано**.

- `providers.py` — `DownloadProvider`: `yt_dlp` (cookies/proxy, терминальные vs повторяемые ошибки), `cobalt` (FR-2.6), `embed_only` (последний рубеж Р2). yt-dlp изолирован здесь.
- `resolver.py` — oEmbed (слой 1) → yt-dlp probe (слой 2, дополнение); раскрытие коротких ссылок; хэштеги из подписи; обложка локально.
- `workers.py` — стадии `resolve` и `download`: дедуп со слиянием (FR-1.4), dead-link → best-effort карточка, embed-fallback при неудаче загрузки, дисковый watermark (FR-2.7), канонические пути.

Телеметрия доли успешных скачиваний — `toklib_core.queue.download_success_ratio` (видна в `/api/system/ai`).
Парсер TikTok data export — Фаза 3.

Тесты: `uv run --all-packages pytest services/ingest/tests`.
