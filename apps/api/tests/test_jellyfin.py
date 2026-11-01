"""Jellyfin-экспорт: NFO-метаданные и генерируемое дерево (FR-8.2, Р5)."""


from sqlalchemy.orm import sessionmaker
from toklib_api.jellyfin import build_nfo, export_jellyfin
from toklib_core.db import Analysis, Base, Video, make_engine


def _make_video(session, url="https://www.tiktok.com/@cook/video/7309999999999999900", tiktok_id="7309999999999999900", **kw):
    video = Video(url=url, tiktok_id=tiktok_id, **kw)
    session.add(video)
    session.commit()
    return video


def test_build_nfo_fields(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path/'nfo.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    video = _make_video(
        session,
        caption="рецепт пирога <с малиной>",
        author_username="cook",
        published_at=__import__("datetime").datetime(2025, 5, 1),
    )
    session.add(Analysis(video_id=video.id, model="m", prompt_version="v1", input_hash="h", summary="Готовим пирог.", key_idea="Холодное масло."))
    session.commit()

    nfo = build_nfo(video)
    assert "<title>рецепт пирога &lt;с малиной&gt;</title>" in nfo  # xml-эскейпинг
    assert "Готовим пирог." in nfo
    assert "Ключевая идея: Холодное масло." in nfo
    assert "<director>@cook</director>" in nfo
    assert "<year>2025</year>" in nfo


def test_export_tree_hardlink_nfo_poster(tmp_path):
    import datetime as dt

    engine = make_engine(f"sqlite:///{tmp_path/'jf.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    file_path = tmp_path / "media.mp4"
    file_path.write_bytes(b"VIDEO")
    video = _make_video(session, caption="пирог", file_path=str(file_path), published_at=dt.datetime(2025, 5, 1))
    session.add(Analysis(video_id=video.id, model="m", prompt_version="v1", input_hash="h", summary="С.", key_idea="И."))
    session.commit()

    # без папки → Unsorted; с файлом → экспорт
    summary = export_jellyfin(session, tmp_path)
    assert summary["exported"] == 1
    dest = tmp_path / "export" / "jellyfin" / "Unsorted" / "7309999999999999900"
    assert (dest / "video.mp4").is_file()
    assert (dest / "video.mp4").samefile(file_path)  # hardlink, не копия (Р5)
    assert (dest / "movie.nfo").is_file()
    nfo = (dest / "movie.nfo").read_text(encoding="utf-8")
    assert "7309999999999999900" in nfo

    # без файла — пропуск
    _make_video(session, tiktok_id="7309999999999999901", url="u2", caption="без файла")
    summary2 = export_jellyfin(session, tmp_path)
    assert summary2["skipped_no_file"] >= 1
