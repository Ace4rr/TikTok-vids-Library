import pytest
from sqlalchemy.orm import sessionmaker
from toklib_core import search as search_mod
from toklib_core.db import Analysis, Base, Note, Tag, Video, VideoTag, make_engine
from toklib_core.enums import NoteKind, TagKind, TagSource


@pytest.fixture
def db(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path/'s.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    with engine.connect() as conn:
        search_mod.ensure_fts(conn)

    v1 = Video(url="u1", tiktok_id="7300000000000000001", caption="рецепт холодного фарша", author_username="cook", author_display="Кок")
    v2 = Video(url="u2", tiktok_id="7300000000000000002", caption="тренировка на пресс", author_username="gymrat")
    v3 = Video(url="u3", tiktok_id="7300000000000000003", caption="кот играет", author_username="cook")
    session.add_all([v1, v2, v3])
    session.flush()

    tag = Tag(name="еда", kind=TagKind.AI)
    session.add(tag)
    session.flush()
    session.add(VideoTag(video_id=v1.id, tag_id=tag.id, source=TagSource.AI))
    session.add(Note(video_id=v1.id, kind=NoteKind.USER, text="попробовать в выходные"))
    session.add(Analysis(video_id=v1.id, model="fake", prompt_version="v1", input_hash="h", summary="готовим фарш"))

    for v in (v1, v2, v3):
        session.commit()
        session.refresh(v)
        search_mod.index_video_orm(engine, v)
    yield engine, session
    session.close()


def test_fts_finds_by_caption(db):
    engine, _ = db
    result = search_mod.search(engine, "фарш")
    assert result["total"] == 1
    assert len(result["ids"]) == 1


def test_fts_finds_notes_and_summary(db):
    engine, _ = db
    assert search_mod.search(engine, "выходные")["total"] == 1
    assert search_mod.search(engine, "фарш готовим")["total"] >= 1


def test_prefix_search(db):
    engine, _ = db
    assert search_mod.search(engine, "тренир")["total"] == 1


def test_operator_author(db):
    engine, _ = db
    result = search_mod.search(engine, "author:cook")
    assert result["total"] == 2
    assert search_mod.search(engine, "author:cook кот")["total"] == 1


def test_operator_tag(db):
    engine, _ = db
    assert search_mod.search(engine, "tag:еда")["total"] == 1
    assert search_mod.search(engine, "tag:еда фарш")["total"] == 1
    assert search_mod.search(engine, "tag:еда пресс")["total"] == 0


def test_no_results_on_garbage(db):
    engine, _ = db
    assert search_mod.search(engine, "zxqv")["total"] == 0


def test_reindex_all(db):
    engine, _session = db
    count = search_mod.reindex_all(engine)
    assert count == 3
    assert search_mod.search(engine, "пресс")["total"] == 1
