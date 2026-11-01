"""Импорт TikTok data export — эвристический парсер и ленивая очередь (FR-1.5/9.3)."""

import json

from sqlalchemy.orm import sessionmaker
from toklib_core import queue as job_queue
from toklib_core.db import Base, InboxItem, Video, make_engine
from toklib_core.enums import InboxStatus, JobPriority, VideoStatus
from toklib_ingest.importer import extract_tiktok_links, import_links, parse_data_export


def test_extract_links_from_tiktok_export_shape():
    """Реальная структура архива: ActivityList с Date/Link — как в «Favorite Videos.json»."""
    archive = {
        "ActivityList": [
            {"Date": "2025-06-01 12:00:00", "Link": "https://www.tiktok.com/@cook/video/7301111111111111111"},
            {"Date": "2025-06-02 13:30:00", "Link": "https://vm.tiktok.com/ZMabc123/"},
            {"Other": "без ссылки"},
        ]
    }
    links = extract_tiktok_links(archive)
    assert len(links) == 2
    assert links[0] == ("https://www.tiktok.com/@cook/video/7301111111111111111", "2025-06-01 12:00:00")
    assert links[1][1] == "2025-06-02 13:30:00"


def test_extract_links_dedup_and_urls_inside_text():
    node = {"a": "смотри https://www.tiktok.com/@x/video/7302222222222222222 огонь",
            "b": "https://www.tiktok.com/@x/video/7302222222222222222"}
    links = extract_tiktok_links(node)
    assert len(links) == 1


def test_parse_data_export_json_and_txt():
    archive = json.dumps({"ActivityList": [{"Link": "https://www.tiktok.com/@x/video/7303333333333333333"}]})
    assert len(parse_data_export(archive)) == 1
    txt = "строка без ссылки\nhttps://www.tiktok.com/@y/video/7304444444444444444,\n"
    parsed = parse_data_export(txt)
    assert len(parsed) == 1
    assert parsed[0][0].endswith("7304444444444444444")


def test_import_links_lazy_backlog(tmp_path):
    """Импорт: backlog-приоритет (полный AI ночью), дедуп, связь с инбоксом (FR-1.4)."""
    engine = make_engine(f"sqlite:///{tmp_path/'imp.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()

    links = [
        ("https://www.tiktok.com/@x/video/7305555555555555551", "2025-06-01 12:00:00"),
        ("https://www.tiktok.com/@x/video/7305555555555555552", None),
    ]
    summary = import_links(session, links)
    assert summary == {"added": 2, "duplicates": 0, "skipped": 0, "links_found": 2}

    videos = session.query(Video).all()
    assert len(videos) == 2
    assert all(v.status == VideoStatus.INBOX for v in videos)
    assert videos[0].saved_at.year == 2025  # дата из архива сохранена

    job = session.query(job_queue.Job).filter(job_queue.Job.type == "resolve").first()
    assert job is not None
    assert job.priority == int(JobPriority.BACKLOG)  # ленивый импорт

    items = session.query(InboxItem).all()
    assert all(item.source == "import" and item.status == InboxStatus.NEW for item in items)

    # повторный импорт тех же ссылок — дубликаты, счётчик +1
    summary2 = import_links(session, links)
    assert summary2["added"] == 0
    assert summary2["duplicates"] == 2
    assert all(v.save_count == 2 for v in session.query(Video).all())


def test_import_ignores_garbage_links(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path/'g.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    summary = import_links(session, [("https://youtube.com/watch?v=1", None), ("мусор", None)])
    assert summary["added"] == 0
