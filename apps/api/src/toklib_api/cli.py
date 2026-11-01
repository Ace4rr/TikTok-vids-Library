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
    checks.append(("ai.mode", True, str(ai.mode)))
    checks.append(("ai.preset", True, f"{ai.preset} (S/M/L/XL/cloud, docs/09 §9.15)" if ai.preset != "custom" else "custom"))

    if ai.stt.provider in ("faster_whisper", "faster-whisper"):
        ok, msg = _check_import("faster_whisper")
        checks.append(("stt", ok, f"{ai.stt.provider}/{ai.stt.model}: {msg}"))
    else:
        ok, msg = _check_http(ai.stt.base_url or "http://127.0.0.1:9000")
        checks.append(("stt", ok, f"{ai.stt.provider}: {msg}"))
        from toklib_core.config import secret_from_env

        stt_key = secret_from_env(ai.stt.api_key_env)
        checks.append(
            (
                "stt_key",
                bool(stt_key),
                f"{ai.stt.api_key_env}: {'задан' if stt_key else 'НЕ задан — облачный STT не авторизуется'}",
            )
        )

    ok, msg = _check_http(ai.llm.base_url)
    checks.append(("llm", ok, f"{ai.llm.provider} {ai.llm.base_url} ({ai.llm.model}): {msg}"))
    from toklib_ai.providers.llm import _is_local

    if not _is_local(ai.llm.base_url):
        from toklib_core.config import secret_from_env

        llm_key = secret_from_env(ai.llm.api_key_env)
        checks.append(
            (
                "llm_key",
                bool(llm_key),
                f"{ai.llm.api_key_env}: {'задан' if llm_key else 'НЕ задан — облачный LLM не авторизуется'}",
            )
        )

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

    if ai.preset != "custom":
        preset = f"{ai.preset} (задан в конфиге, ai.preset)"
    else:
        preset = "custom → ориентировочно M (Desktop CPU); ключ ai.preset подставит S/M/L/XL/cloud целиком (docs/09 §9.15)"
        if not ffmpeg_ok and not _check_import("faster_whisper")[0]:
            preset = "custom → ориентировочно S (NAS): облако-дешёвое или только Tier-0"
    print("-" * 60)
    print(f"Пресет: {preset}")
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
    """Времена стадий AI на видео из библиотеки (docs/08 §8.6)."""
    import time as time_mod
    from types import SimpleNamespace

    from sqlalchemy.orm import sessionmaker
    from toklib_ai import worker as ai_worker
    from toklib_ai.worker import AiContext
    from toklib_core.config import load_settings
    from toklib_core.db import Video, make_engine

    settings = load_settings()
    engine = make_engine(settings.server.db_url())
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    videos = session.query(Video).filter(Video.deleted_at.is_(None)).limit(videos_n).all()
    if not videos:
        print("нет видео в библиотеке — сначала добавьте ссылки через /api/inbox")
        return 1

    from toklib_ai import tier0 as _tier0

    from .context import build_ai_router

    ctx = AiContext(
        settings=settings,
        router=build_ai_router(settings),
        dictionary=_tier0.load_dictionary(_tier0.ensure_dictionary(settings.server.data_dir)),
    )

    stage_times: dict[str, list[float]] = {}
    for video in videos:
        job_stub = SimpleNamespace(id=0, type="bench", payload={"video_id": video.id, "priority": 1}, priority=1)
        for stage_name, stage_fn in (
            ("transcribe", ai_worker.run_transcribe_job),
            ("analyze", ai_worker.run_analyze_job),
            ("embed", ai_worker.run_embed_job),
        ):
            t0 = time_mod.perf_counter()
            try:
                stage_fn(ctx, session, job_stub)
            except Exception as exc:  # noqa: BLE001 — замер идёт дальше
                print(f"  {stage_name} #{video.id}: ошибка: {exc}")
                session.rollback()
                continue
            stage_times.setdefault(stage_name, []).append(time_mod.perf_counter() - t0)

    print("toklib benchmark\n" + "-" * 50)
    for stage, times in stage_times.items():
        times.sort()
        print(f"{stage:12s} n={len(times):3d}  p50={times[len(times) // 2]:6.2f}s  total={sum(times):8.1f}s")
    if not stage_times:
        print("ни одна стадия не выполнена — проверьте toklib doctor")
    return 0


def spike(links_file: str, out: str | None, limit: int) -> int:
    """Спайк (docs/06 §6.5.1): сухой прогон конвейера на реальных ссылках с замером
    стадий; в библиотеку ничего не пишет. Вердикт go/no-go по критерию docs/04 Фаза 0."""
    import json as json_mod
    import shutil
    import time as time_mod
    import uuid
    from pathlib import Path as _Path

    from toklib_ai import tier0
    from toklib_ai.interfaces import AnalyzeInput
    from toklib_core.config import load_settings
    from toklib_core.oembed import MetadataError
    from toklib_ingest import resolver as resolver_mod
    from toklib_ingest.providers import ProviderError, build_provider

    from .context import build_ai_router

    settings = load_settings()
    router = build_ai_router(settings)
    provider = build_provider(settings.download)
    dictionary = tier0.load_dictionary(tier0.ensure_dictionary(settings.server.data_dir))

    links = [
        line.strip()
        for line in _Path(links_file).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ][:limit]
    if not links:
        print(f"в {links_file} нет ссылок")
        return 1

    results: list[dict] = []
    for url in links:
        rec: dict = {
            "url": url,
            "resolve_s": None,
            "download_s": None,
            "stt_s": None,
            "llm_s": None,
            "embed_s": None,
            "download_ok": False,
            "media_mode": None,
            "transcript_chars": 0,
            "analysis_ok": False,
            "error": None,
        }
        t0 = time_mod.perf_counter()
        try:
            fields = resolver_mod.resolve_metadata(url=url, tiktok_id=None, settings=settings)
            rec["resolve_s"] = round(time_mod.perf_counter() - t0, 2)
        except MetadataError as exc:
            rec["error"] = f"resolve({exc.kind.value}): {exc}"
            results.append(rec)
            continue

        dest = settings.server.data_dir / "tmp" / f"spike-{uuid.uuid4().hex[:8]}"
        dl = None
        transcript_text = ""
        try:
            t0 = time_mod.perf_counter()
            dl = provider.download(fields.get("url") or url, dest)
            rec["download_s"] = round(time_mod.perf_counter() - t0, 2)
            rec["download_ok"] = dl.file_path is not None
            rec["media_mode"] = str(dl.media_mode.value)
        except ProviderError as exc:
            rec["error"] = f"download({exc.kind.value}): {exc}"

        if dl is not None and dl.file_path is not None and router.transcriber is not None:
            audio = dest / "audio.wav"
            try:
                from toklib_ai.worker import extract_audio_ffmpeg

                t0 = time_mod.perf_counter()
                extract_audio_ffmpeg(str(dl.file_path), audio)
                stt = router.transcribe(audio, None)
                rec["stt_s"] = round(time_mod.perf_counter() - t0, 2)
                transcript_text = stt.full_text()
                rec["transcript_chars"] = len(transcript_text)
            except Exception as exc:  # noqa: BLE001 — спайк продолжает
                rec["error"] = rec["error"] or f"stt: {exc}"

        if router.primary_analyzer is not None:
            inp = AnalyzeInput(
                caption=fields.get("caption"),
                hashtags=fields.get("hashtags", []),
                categories=list(dictionary.get("categories", {}).keys()),
                transcript_text=transcript_text or None,
            )
            t0 = time_mod.perf_counter()
            try:
                router.analyze(inp)
                rec["llm_s"] = round(time_mod.perf_counter() - t0, 2)
                rec["analysis_ok"] = True
            except Exception as exc:  # noqa: BLE001
                rec["error"] = rec["error"] or f"llm: {exc}"

        if router.embedder is not None:
            doc = " ".join(filter(None, [transcript_text, fields.get("caption")]))
            if doc:
                t0 = time_mod.perf_counter()
                try:
                    router.embed_query(doc)
                    rec["embed_s"] = round(time_mod.perf_counter() - t0, 3)
                except Exception as exc:  # noqa: BLE001
                    rec["error"] = rec["error"] or f"embed: {exc}"

        shutil.rmtree(dest, ignore_errors=True)
        results.append(rec)

    resolved = sum(1 for r in results if r["resolve_s"] is not None)
    downloaded = sum(1 for r in results if r["download_ok"])
    analyzed = sum(1 for r in results if r["analysis_ok"])
    print("toklib spike\n" + "-" * 70)
    print(f"ссылок: {len(results)}  резолв ok: {resolved}  скачивание ok: {downloaded}  анализ ok: {analyzed}")
    for key in ("resolve_s", "download_s", "stt_s", "llm_s", "embed_s"):
        times = sorted(r[key] for r in results if r[key] is not None)
        if times:
            print(f"{key:12s} p50={times[len(times) // 2]:7.2f}s  max={times[-1]:7.2f}s")
    for r in results:
        if r["error"]:
            print(f"  ошибка: {r['url']}\n    {r['error']}")
    go = resolved >= 0.8 * len(results) and downloaded >= 0.6 * len(results)
    print(f"критерий go/no-go (docs/04, Фаза 0): {'GO' if go else 'NO-GO — упор на embed+метаданные'}")
    if out:
        _Path(out).write_text(json_mod.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"отчёт: {out}")
    return 0


def import_data_export(file: str) -> int:
    """Разобрать архив TikTok «Скачать мои данные» и поставить ссылки в ленивую очередь."""
    from pathlib import Path as _Path

    from sqlalchemy.orm import sessionmaker
    from toklib_core.config import load_settings
    from toklib_core.db import make_engine
    from toklib_ingest.importer import import_links, parse_data_export

    settings = load_settings()
    engine = make_engine(settings.server.db_url())
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        links = parse_data_export(_Path(file).read_bytes())
        if not links:
            print(f"в {file} не найдено ни одной ссылки TikTok")
            return 1
        summary = import_links(session, links)
        print(
            f"найдено ссылок: {summary['links_found']}, новых: {summary['added']}, "
            f"дубликатов: {summary['duplicates']} — Tier-0 после резолва, полный AI ночью (lazy_backlog)"
        )
        return 0
    finally:
        session.close()


def export_jellyfin() -> int:
    """Пересобрать Jellyfin-дерево: <data>/export/jellyfin/<Папка>/<id>/{video, poster, movie.nfo}."""
    from pathlib import Path as _Path

    from sqlalchemy.orm import sessionmaker
    from toklib_core.config import load_settings
    from toklib_core.db import make_engine

    from .jellyfin import export_jellyfin

    settings = load_settings()
    engine = make_engine(settings.server.db_url())
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        summary = export_jellyfin(session, _Path(settings.server.data_dir))
        print(
            f"экспортировано: {summary['exported']} (без файла пропущено: {summary['skipped_no_file']}) → {summary['root']}"
        )
        return 0
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="toklib", description="TokLib CLI (docs/08 §8.6)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="проверка провайдеров, железа, конфигурации")
    sub.add_parser("reindex-embeddings", help="перестроить векторы после смены модели")
    bench = sub.add_parser("benchmark", help="времена стадий AI на видео библиотеки")
    bench.add_argument("--videos", type=int, default=10)
    spk = sub.add_parser("spike", help="сухой прогон конвейера на реальных ссылках (go/no-go)")
    spk.add_argument("--links", required=True, help="файл со ссылками TikTok (по одной в строке)")
    spk.add_argument("--out", default=None, help="json-файл отчёта")
    spk.add_argument("--limit", type=int, default=20)
    imp = sub.add_parser("import-data-export", help="импорт архива TikTok «Скачать мои данные» (ленивая очередь)")
    imp.add_argument("--file", required=True, help="путь к JSON/TXT-архиву избранного")
    sub.add_parser("export-jellyfin", help="пересобрать Jellyfin-дерево (hardlink + NFO + постеры)")
    args = parser.parse_args()

    if args.command == "doctor":
        sys.exit(doctor())
    if args.command == "reindex-embeddings":
        sys.exit(reindex_embeddings())
    if args.command == "benchmark":
        sys.exit(benchmark(args.videos))
    if args.command == "spike":
        sys.exit(spike(args.links, args.out, args.limit))
    if args.command == "import-data-export":
        sys.exit(import_data_export(args.file))
    if args.command == "export-jellyfin":
        sys.exit(export_jellyfin())


if __name__ == "__main__":
    main()
