# 8. Провайдеры AI: варианты и пути подключения

**Задача документа:** как TokLib подключается к AI — локально и в облако — без изменения кода, и какие пресеты выдавать пользователю под его железо. Пайплайн и экономика — `docs/07-ai-pipeline.md`.

## 8.1. Архитектура подключения

Три независимых интерфейса + маршрутизатор. Воркеры не знают о конкретных вендорах:

```text
Transcriber   — аудио → Transcript(segments, language)      # STT
TextAnalyzer  — AnalyzeInput → AnalyzeOutput (JSON)         # LLM: теги/саммари/идея/категория
Embedder      — тексты → векторы                            # поиск
```

**AI Router** для каждой функции: основной провайдер → fallback-цепочка → circuit breaker (N неудач = пауза M минут) → политики режима: `local_only` (облако недоступно физически), `local_first` (облако при ошибке/таймауте), `cloud_only` (слабое железо, осознанно).

```mermaid
flowchart LR
    W[TokLib AI-воркер] --> R[AI Router]
    R -->|in-process| P1[faster-whisper\nsentence-transformers]
    R -->|HTTP localhost| P2[Ollama / llama-server /\nvLLM / LM Studio]
    R -->|HTTP LAN| P3[GPU-бокс в домашней сети\n(base_url)]
    R -->|HTTPS + ключ, opt-in| P4[Облака: OpenRouter,\nGroq, DeepSeek, Gemini, OpenAI]
```

**Четыре пути присоединения:**

| Путь | Что это | Когда выбирать | Нюансы |
|---|---|---|---|
| **1. In-process** | python-библиотеки внутри AI-воркера (faster-whisper, sentence-transformers) | STT и эмбеддинги — всегда по умолчанию | ноль сетевых слоёв; модели версионируются с образом; RAM воркера |
| **2. Локальный HTTP-сервис** | Ollama / llama.cpp llama-server / vLLM соседним контейнером в compose | LLM — default для self-hosted | GPU пробрасывается устройством; модели управляются отдельно от TokLib |
| **3. Бокс в LAN** | тот же HTTP, `base_url` указывает на GPU-машину в сети | профиль S: TokLib на NAS, инференс на десктопе | один конфиг; в LAN доверять точечно, не открывать наружу |
| **4. Облако** | любой OpenAI-совместимый endpoint + ключи пользователя | бёрст-импорт бэклога, профили без локального инференса | только opt-in (NFR-9); секреты в env |

## 8.2. LLM-провайдеры

| Провайдер | Тип | Когда выбирать | Нюансы |
|---|---|---|---|
| **Ollama** | локальный раннер | default self-hosted | `keep_alive` (модель резидентна), structured outputs (JSON-схема), простой менеджмент моделей |
| **llama.cpp (llama-server)** | локальный сервер | максимальный контроль, слабое железо | GBNF-грамматики = жёсткий JSON; prompt cache; самый лёгкий |
| **LM Studio** | локальный GUI-раннер | «пользовательский» вариант | OpenAI-совместимый endpoint |
| **vLLM** | локальный GPU-сервер | SaaS-режим, батчи, много пользователей | prefix caching, максимальная утилизация GPU |
| **OpenRouter** | облачный агрегатор | универсальный fallback: 300+ моделей, один ключ | платить за то, что вызвано; RU-качество зависит от модели |
| **Дешёвый облачный класс** (Gemini Flash, Groq free tier, DeepSeek и аналоги) | облако | бэклог-бёрст, слабое железо | центы на 1000 видео (см. 07.7); цены волатильны — проверять при внедрении |
| **OpenAI / Anthropic** | облако | качество-максимум, opt-in | дорого для массовой обработки; для точечных сложных случаев |

**Классы моделей для нашей задачи** (теги+саммари по короткому транскрипту; список — не догма, финальный выбор по сету из 20 видео на языке пользователя в Фазе 0):

| Класс | Примеры (проверить RU-качество) | CPU Q4 (профиль M) | GPU (профиль L) | Комментарий |
|---|---|---|---|---|
| 3–4B instruct | Qwen3 4B, Gemma 3 4B | ~4–15 т/с — рабочий вариант | 50+ т/с | базовый self-hosted; саммари простые |
| 7–9B instruct | Qwen3 8B, Llama 3.1 8B, GLM-класс | ~2–8 т/с — терпимо для бэклога | 40–80 т/с | целевой класс качества |
| 12B+ | Gemma 3 12B/27B (Q4) | нет | нужно 12+ ГБ VRAM | заметно лучше саммари; только профиль L/XL |

Обязательные требования к любой модели: instruct (не reasoning/thinking — иначе тратим токены на рассуждения), поддержка структурированного вывода (JSON-схема/грамматика) или строгий валидатор+ремонт на нашей стороне, знание языка контента пользователя.

## 8.3. STT-провайдеры

| Провайдер | Режим | Модели | Когда | Комментарий |
|---|---|---|---|---|
| **faster-whisper** (in-process) | локально, default | tiny…small…medium, large-v3-turbo | весь self-hosted | int8; VAD; для русского — small минимум, лучше medium/turbo |
| **whisper.cpp** (in-process/server) | локально | ggml-квантованные | очень слабый CPU/ARM (профиль S) | медленнее faster-whisper на x86, но живёт на N100/RPi |
| **Groq Whisper API** | облако, opt-in | large-v3-turbo (~$0.04/ч) | бэрст большого бэклога | быстро и дёшево; проверить актуальную цену |
| **OpenAI Whisper API** | облако, opt-in | whisper-1 / transcribe-класс | альтернатива | ~$0.006/мин; для массовой обработки дорого |

Ориентиры скорости (зависят от CPU — замерить в Фазе 0): small int8 на десктопном CPU ≈ 5–10× реального времени; на NAS-классе (N100) ≈ 1–3×; large-v3-turbo на GPU 8 ГБ — десятки× реального времени.

## 8.4. Эмбеддинги

| Провайдер | Модель | Размерность | Когда |
|---|---|---|---|
| **sentence-transformers** (in-process, default) | multilingual-e5-small (~120M) | 384 | CPU любого профиля, доли секунды на документ |
| sentence-transformers / GPU | bge-m3 (~560M) | 1024 | профиль L: качество выше, есть sparse-компонента |
| Ollama embeddings endpoint | любые emb-модели реестра | — | если Ollama уже есть, отдельная зависимость не нужна |
| OpenAI | text-embedding-3-small | 1536 | opt-in; ~$0.02/1M ток. — центы даже на больших библиотеках |

Правило совместимости: векторы разных моделей не смешиваются; `embedding.model_id` хранится с каждым вектором; смена модели = `toklib reindex-embeddings` (минуты на тысячи документов на CPU).

## 8.5. Конфигурация (полный пример)

```yaml
ai:
  mode: local_first                    # local_only | local_first | cloud_only
  stt:
    provider: faster_whisper           # faster_whisper | whisper_cpp | openai_api | groq_api
    model: small                       # tiny|base|small|medium|large-v3-turbo
    compute: int8                      # int8 (CPU) | float16 (GPU)
    vad: true
    beam: 1
    language: auto
  llm:
    provider: ollama                   # ollama | llama_cpp | openai_compat
    base_url: http://ollama:11434      # или http://gpu-box.lan:11434 (путь 3)
    model: qwen3:4b-instruct-q4_K_M
    keep_alive: 30m
    temperature: 0.2
    max_tokens: 256
    json_mode: schema                  # schema | grammar | validate_repair
    fallback:                          # активен только при mode=local_first
      provider: openai_compat
      base_url: https://openrouter.ai/api/v1
      model: <дешёвый flash-класс на момент внедрения>
      api_key_env: TOKLIB_LLM_KEY
      on: [error, timeout_s=120]
      circuit_breaker: {failures: 3, pause_min: 15}
  embeddings:
    provider: sentence_transformers    # sentence_transformers | ollama | openai_compat
    model: multilingual-e5-small
    dim: 384
  budgets:
    analysis_per_day: 0                # 0 = без лимита
    nightly_backlog: 400
    lazy_backlog: true
    stt_concurrency: 1
```

Секреты — только через env (`TOKLIB_LLM_KEY`, `TOKLIB_STT_KEY`), никогда в yaml и БД. Смена любого провайдера/модели — правкой конфига, без кода и без пересборки образа.

## 8.6. Пресеты железа

| Пресет | Железо | STT | LLM | Эмбеддинги | Ожидание (видео/день, ориентир) |
|---|---|---|---|---|---|
| **S «NAS»** | N100 / RPi 8 ГБ | whisper.cpp small (ggml) | облако-дешёвое или 3B ночью | e5-small | 10–30 (полный AI), Tier-0 — без ограничений |
| **M «Desktop CPU»** | Ryzen 5 / 16 ГБ | faster-whisper small/medium int8 | 3–4B Q4, 10–15 т/с | e5-small | 100–300 |
| **L «GPU»** | RTX 3060 12 ГБ | large-v3-turbo float16 | 7–9B Q4 (до 12B) | bge-m3 | 500+ |
| **XL «бокс в LAN»** | TokLib на NAS (S) + `base_url` на GPU-машину | по NAS | LLM на GPU-боксе | e5-small на NAS | как L |

Диагностика:

- `toklib doctor` — проверяет каждого сконфигурированного провайдера (один короткий вызов), железо, место на диске; печатает вердикт и рекомендованный пресет.
- `toklib benchmark --videos 10` — прогоняет конвейер на 10 случайных видео, печатает времена по стадиям и долю валидного JSON.

## 8.7. Миграции и совместимость

- `analysis.prompt_version` + `input_hash` — старые записи не перегенерируются молча; `toklib reanalyze [--model M] [--older-than ...]` — осознанный перезапуск анализа.
- Смена STT-модели не перегенерирует транскрипты автоматически (дорого): точечно per-video и массово — командой для выбранного подмножества.
- Смена модели эмбеддингов — обязательно `toklib reindex-embeddings` (8.4).
- Результаты разных моделей помечены `model` в `analysis` — история прогонов сохраняется, UI показывает, чем сделано.

## 8.8. Приватность и режим `local_only`

- `local_only`: у маршрутизатора физически нет облачных провайдеров; airgap-тест в CI (контейнеру запрещены внешние домены) гарантирует отсутствие внешних вызовов (NFR-4, NFR-9).
- Облачные режимы — только с ключами пользователя; что и когда уходит наружу — видно в `toklib doctor` и логах (запись: провайдер, модель, число токенов/секунд).
- Telegram-бот и oEmbed — не AI-провайдеры, но тоже внешние вызовы; в максимально закрытом контуре бот отключается, резолв метаданных остаётся (это единственный способ жить без него — считать открытым вопросом эксплуатации).
