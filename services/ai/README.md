# services/ai — AI-обработка

Каскадный пайплайн: **Tier-0** → **Tier-1** → **Tier-2** — каркас и Tier-0 реализованы, полный слой (реальные модели) — Фаза 2.

- `tier0.py` — словарные автотеги по подписи/хэштегам, детект языка по алфавиту; словарь пользователя в `<data_dir>/tier0-dictionary.yaml` (дефолт копируется из пакета).
- `interfaces.py` — `Transcriber` / `TextAnalyzer` / `Embedder` + валидация JSON-схемы ответа (docs/07 §7.5).
- `prompts.py` — компактный системный промпт, обрезка транскрипта, промпт ремонта JSON.
- `router.py` — цепочка провайдеров по режиму (`local_only` физически исключает облако — NFR-9), circuit breaker, одна попытка ремонта.
- `providers/` — faster-whisper (in-process, opt-in extra `stt`), OpenAI-совместимый LLM (ollama/llama.cpp/vLLM/OpenRouter), sentence-transformers (extra `embed`); отсутствие зависимости = провайдер выключен, а не падение.
- `worker.py` — стадии `transcribe` (ffmpeg 16 кГц mono → STT → tmp удаляется), `analyze` (кэш по input_hash, бюджеты → перенос на ночь), `embed` (составной документ).

- Пайплайн, экономия токенов/расходов, бюджеты: `docs/07-ai-pipeline.md`
- Провайдеры, конфигурация, пресеты железа S/M/L, `toklib doctor`: `docs/08-ai-providers.md`

Тесты: `uv run --all-packages pytest services/ai/tests`.
