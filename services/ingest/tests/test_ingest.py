import pytest
from sqlalchemy.orm import sessionmaker
from toklib_core.config import Settings
from toklib_core.db import Base, Video, make_engine
from toklib_core.enums import MediaMode, VideoStatus
from toklib_ingest import resolver as resolver_mod
from toklib_ingest.providers import classify_ytdlp_error
from toklib_ingest.workers import IngestContext, run_download_job, run_resolve_job


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite:///{tmp_path/'i.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    settings = Settings()
    settings.server.data_dir = tmp_path / "data"
    settings.download.provider = "none"  # embed-only: без сети
    monkeypatch.setattr("toklib_ingest.workers.disk_usage_pct", lambda path: 10.0)  # реальный диск вне теста
    ctx = IngestContext(settings=settings)
    yield engine, session, ctx
    session.close()


RESOLVE_RESULT = {
    "tiktok_id": "7301234567890123456",
    "url": "https://www.tiktok.com/@user/video/7301234567890123456",
    "caption": "рецепт пирога #recipes",
    "author_display": "Пекарь",
    "author_username": "user",
    "cover_url": None,
    "duration_sec": 45.0,
    "published_at": None,
    "hashtags": ["recipes"],
    "health_set": "alive",
}


def test_classify_ytdlp_errors():
    assert classify_ytdlp_error("ERROR: This video has been removed") == "terminal"
    assert classify_ytdlp_error("Private video. Sign in") == "terminal"
    assert classify_ytdlp_error("urllib error timeout") == "transient"


def test_parse_hashtags():
    assert resolver_mod.parse_hashtags("рецепт #рецепты и #Food #food") == ["рецепты", "food"]


def test_resolve_full_flow_embed_fallback(db, monkeypatch):
    _engine, session, ctx = db
    monkeypatch.setattr(resolver_mod, "resolve_metadata", lambda **kw: dict(RESOLVE_RESULT))
    monkeypatch.setattr(resolver_mod, "download_cover", lambda *a, **k: True)

    video = Video(url="https://www.tiktok.com/@user/video/7301234567890123456", tiktok_id="7301234567890123456")
    session.add(video)
    session.commit()

    outcome = run_resolve_job(ctx, session, {"video_id": video.id, "priority": 1})
    assert outcome == "resolved"
    session.expire_all()
    video = session.get(Video, video.id)
    assert video.author_username == "user"
    assert video.caption == "рецепт пирога #recipes"
    assert video.hashtags == ["recipes"]
    assert video.status == VideoStatus.RESOLVING  # resolve ставит download; статус меняет download-воркер

    outcome = run_download_job(ctx, session, {"video_id": video.id, "priority": 1})
    assert outcome == "downloaded"
    session.expire_all()
    video = session.get(Video, video.id)
    assert video.media_mode == MediaMode.EMBED
    assert video.status == VideoStatus.READY  # embed-fallback: AI отложен


def test_resolve_terminal_dead_link(db, monkeypatch):
    from toklib_core.enums import ErrorKind, Health
    from toklib_core.oembed import MetadataError

    _engine, session, ctx = db

    def dead(**kw):
        raise MetadataError(ErrorKind.TERMINAL, "oEmbed 404")

    monkeypatch.setattr(resolver_mod, "resolve_metadata", dead)

    video = Video(url="https://www.tiktok.com/@user/video/7301234567890123456", tiktok_id="7301234567890123456")
    session.add(video)
    session.commit()

    outcome = run_resolve_job(ctx, session, {"video_id": video.id, "priority": 1})
    assert outcome == "dead"
    session.expire_all()
    video = session.get(Video, video.id)
    assert video.health == Health.DEAD
    assert video.status == VideoStatus.READY  # best-effort, карточка живёт


def test_duplicate_merge(db, monkeypatch):
    _engine, session, ctx = db
    monkeypatch.setattr(resolver_mod, "resolve_metadata", lambda **kw: dict(RESOLVE_RESULT))
    monkeypatch.setattr(resolver_mod, "download_cover", lambda *a, **k: True)

    first = Video(url="https://www.tiktok.com/@user/video/7301234567890123456", tiktok_id="7301234567890123456")
    session.add(first)
    session.commit()
    run_resolve_job(ctx, session, {"video_id": first.id, "priority": 1})

    # повтор пришёл короткой ссылкой: id ещё неизвестен, раскрывается на резолве
    monkeypatch.setattr(
        resolver_mod,
        "follow_short_link",
        lambda url, timeout=10.0: "https://www.tiktok.com/@user/video/7301234567890123456",
    )
    second = Video(url="https://vm.tiktok.com/ZMabc/")
    session.add(second)
    session.commit()
    outcome = run_resolve_job(ctx, session, {"video_id": second.id, "priority": 1})
    assert outcome == "duplicate"
    session.expire_all()
    assert session.get(Video, second.id) is None  # дубль удалён
    assert session.get(Video, first.id).save_count == 2  # FR-1.4
