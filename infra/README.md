# infra — Развёртывание и CI (файлы в корне репозитория)

- **`Dockerfile`** — two-stage: сборка Next.js static export → python:3.12-slim + ffmpeg + uv; один контейнер API + воркеры + веб-статика (NFR-5). AI-зависимости — opt-in.
- **`docker-compose.yml`** — сервис `toklib` + volume `./data:/data` (SQLite, media, config.yaml); закомментирован Ollama-сервис (путь 2, docs/08) и GPU-профиль L.
- **`config.example.yaml`** — полный конфиг: сервер, скачивание, watermark, AI-секции по docs/08 §8.5.
- **`.env.example`** — секреты только через env (`TOKLIB_TOKEN`, ключи AI, бот).
- **`.github/workflows/ci.yml`** — backend: uv sync + ruff + pytest; web: npm ci + build.

Статус: каркас готов; дальше — публикация образа, SaaS-профиль (Postgres/Redis/Qdrant), airgap-тест `local_only` в CI.
