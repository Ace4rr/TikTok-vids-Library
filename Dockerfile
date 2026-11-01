# ---- Stage 1: сборка web (Next.js static export, docs/09 §9.10) ----
FROM node:22-alpine AS web
WORKDIR /src/web
COPY apps/web/package.json apps/web/package-lock.json* ./
RUN npm ci || npm install
COPY apps/web/ ./
RUN npm run build

# ---- Stage 2: runtime (API + воркеры + статика web — один контейнер, docs/05 §5.8) ----
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

# ffmpeg — извлечение аудио для STT (docs/07 §7.3); git — установка из VCS при надобности
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock* .python-version ./
COPY packages/ ./packages/
COPY services/ ./services/
COPY apps/api/ ./apps/api/

# тяжёлые AI-зависимости — opt-in: добавьте "--extra ai" при сборке под себя
RUN uv sync --frozen --no-dev --all-packages || uv sync --no-dev --all-packages

COPY --from=web /src/web/out ./web/out
ENV TOKLIB_WEB_DIST=/app/web/out \
    TOKLIB_DATA_DIR=/data

VOLUME ["/data"]
EXPOSE 8000

CMD ["uv", "run", "--no-dev", "--all-packages", "python", "-m", "toklib_api.main"]
