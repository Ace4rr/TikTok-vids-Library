"""CLI: `toklib doctor` / `toklib reindex-embeddings` (docs/08 §8.6, docs/05 §5.12)."""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import socket
import sys
from urllib.parse import urlparse

from toklib_core.config import load_settings
from toklib_core.paths import disk_usage_pct, ensure_dirs


def _check_ffmpeg() -> tuple[bool, str]:
    path = shutil.which("ffmpeg")
    return (True, path) if path else (False, "ffmpeg не найден в PATH (нужен для STT)")


def _check_http(base_url: str, timeout: float = 2.0) -> tuple[bool, str]:
    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"{host}:{port} доступен"
    except OSError as exc:
        return False, f"{host}:{port} недоступен ({exc})"


def _check_import(name: str) -> tuple[bool, str]:
    if importlib.util.find_spec(name) is not None:
        return True, f"{name}: установлен"
    return False, f"{name}: не установлен (opt-in extra)"


def doctor() -> int:
    """Проверка конфигурации, железа и провайдеров; вердикт + рекомендованный пресет."""
    settings = load_settings()
    data_dir = settings.server.data_dir
    ensure_dirs(data_dir)
    checks: list[tuple[str, bool, str]] = []

    checks.append(("data_dir", data_dir.is_dir(), str(data_dir)))

    usage = disk_usage_pct(data_dir)
    disk_ok = usage < settings.storage.disk_warn_pct
    checks.append(("disk", disk_ok, f"занято {usage:.0f}% (порог предупреждения {settings.storage.disk_warn_pct}%)"))

    ffmpeg_ok, ffmpeg_msg = _check_ffmpeg()
    checks.append(("ffmpeg", ffmpeg_ok, ffmpeg_msg))

    ai = settings.ai
    checks.append(("ai.mode", True, str(ai.mode.value)))

    if ai.stt.provider in ("faster_whisper", "faster-whisper"):
        ok, msg = _check_import("faster_whisper")
        checks.append(("stt", ok, f"{ai.stt.provider}/{ai.stt.model}: {msg}"))
    else:
        ok, msg = _check_http(ai.stt.base_url or "http://127.0.0.1:9000")
        checks.append(("stt", ok, f"{ai.stt.provider}: {msg}"))

    ok, msg = _check_http(ai.llm.base_url)
    checks.append(("llm", ok, f"{ai.llm.provider} {ai.llm.base_url} ({ai.llm.model}): {msg}"))

    if ai.llm_fallback.configured and str(ai.mode.value) == "local_first":
        checks.append(("llm_fallback", True, f"fallback: {ai.llm_fallback.base_url} ({ai.llm_fallback.model})"))

    if ai.embeddings.provider == "sentence_transformers":
        ok, msg = _check_import("sentence_transformers")
        checks.append(("embeddings", ok, f"{ai.embeddings.model}: {msg}"))
    else:
        ok, msg = _check_http(ai.embeddings.base_url or "http://127.0.0.1:11434")
        checks.append(("embeddings", ok, f"{ai.embeddings.provider}: {msg}"))

    checks.append(
        (
            "token",
            bool(settings.token),
            "задан" if settings.token else "НЕ задан — API открыт для локальной сети без аутентификации",
        )
    )

    print("toklib doctor\n" + "-" * 60)
    failed = 0
    for name, ok, msg in checks:
        mark = "OK  " if ok else "FAIL"
        if not ok and name not in ("token",):
            failed += 1
        print(f"[{mark}] {name:12s} {msg}")

    preset = "M (Desktop CPU)"
    if not ffmpeg_ok and not _check_import("faster_whisper")[0]:
        preset = "S (NAS): whisper.cpp/облако-дешёвое или только Tier-0"
    elif _check_import("torch")[0] or ai.llm.base_url.startswith("http://127.0.0.1"):
        preset = "M/L: локальные модели по docs/08 §8.6"
    print("-" * 60)
    print(f"Рекомендованный пресет железа: {preset} (docs/08 §8.6)")
    return 1 if failed else 0


def reindex_embeddings() -> int:
    """Перестроить векторы под текущую модель эмбеддингов (8.4)."""
    from sqlalchemy.orm import sessionmaker
    from toklib_core import queue as job_queue
    from toklib_core.db import Video, make_engine
    from toklib_core.enums import JobPriority, JobType

    settings = load_settings()
    engine = make_engine(settings.server.db_url())
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    videos = session.query(Video).filter(Video.deleted_at.is_(None)).all()
    for video in videos:
        job_queue.enqueue(
            session,
            JobType.EMBED,
            video_id=video.id,
            payload={"priority": int(JobPriority.BACKLOG), "force": True},
            priority=int(JobPriority.BACKLOG),
            idempotency_key=f"embed:{video.id}:reindex",
        )
    print(f"поставлено задач эмбеддинга: {len(videos)} — выполнит AI-воркер")
    return 0


def benchmark(videos_n: int = 10) -> int:
    print("toklib benchmark: полный прогон конвейера — появится вместе с Фазой 2 (docs/08 §8.6).")
    print(f"Сейчас доступно: doctor, reindex-embeddings (запрошено видео: {videos_n})")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="toklib", description="TokLib CLI (docs/08 §8.6)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="проверка провайдеров, железа, конфигурации")
    sub.add_parser("reindex-embeddings", help="перестроить векторы после смены модели")
    bench = sub.add_parser("benchmark", help="прогон конвейера на N видео")
    bench.add_argument("--videos", type=int, default=10)
    args = parser.parse_args()

    if args.command == "doctor":
        sys.exit(doctor())
    if args.command == "reindex-embeddings":
        sys.exit(reindex_embeddings())
    if args.command == "benchmark":
        sys.exit(benchmark(args.videos))


if __name__ == "__main__":
    main()
