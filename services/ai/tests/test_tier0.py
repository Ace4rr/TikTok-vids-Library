from sqlalchemy.orm import sessionmaker
from toklib_ai import tier0
from toklib_core.db import Base, Tag, Video, VideoTag, make_engine
from toklib_core.enums import TagSource


def test_load_default_dictionary(tmp_path):
    """ensure_dictionary копирует package data; работает и при установке из wheel."""
    path = tier0.ensure_dictionary(tmp_path)
    dictionary = tier0.load_dictionary(path)
    assert "рецепт" in dictionary["categories"]
    assert dictionary["max_tags"] >= 1


def test_dictionary_fallback_without_package_data(tmp_path):
    """Если package data потеряна — встроенный запасной словарь, не падение."""

    def no_file():
        return None

    original = tier0.default_dictionary_text
    tier0.default_dictionary_text = no_file
    try:
        path = tier0.ensure_dictionary(tmp_path)
        dictionary = tier0.load_dictionary(path)
    finally:
        tier0.default_dictionary_text = original
    assert "рецепт" in dictionary["categories"]


def test_tier0_tags_by_caption(tmp_path):
    dictionary = tier0.load_dictionary(tier0.ensure_dictionary(tmp_path))
    tags = tier0.tier0_tags("Простой рецепт холодного фарша на ужин", [], dictionary)
    assert "рецепт" in tags


def test_tier0_tags_by_hashtag_alias(tmp_path):
    dictionary = tier0.load_dictionary(tier0.ensure_dictionary(tmp_path))
    tags = tier0.tier0_tags("смотрите что вышло", ["recipes"], dictionary)
    assert "рецепт" in tags


def test_tier0_tags_empty(tmp_path):
    dictionary = tier0.load_dictionary(tier0.ensure_dictionary(tmp_path))
    assert tier0.tier0_tags("ничего особенного", [], dictionary) == []


def test_detect_language():
    assert tier0.detect_language("привет, это рецепт") == "ru"
    assert tier0.detect_language("hello world recipe") == "en"
    assert tier0.detect_language("12345 !!!") is None
    assert tier0.detect_language(None) is None


def test_apply_tier0_idempotent(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path/'t.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    dictionary = {"max_tags": 5, "categories": {"рецепт": ["рецепт"]}, "tag_aliases": {"#еда": "рецепт"}}

    video = Video(url="u", tiktok_id="1", caption="рецепт борща", hashtags=["еда"])
    session.add(video)
    session.commit()

    first = tier0.apply_tier0(session, video, dictionary)
    assert first == ["рецепт"]
    second = tier0.apply_tier0(session, video, dictionary)
    assert second == ["рецепт"]
    # дубликатов нет, источник ai
    links = session.query(VideoTag).filter(VideoTag.video_id == video.id).all()
    assert len(links) == 1
    assert links[0].source == TagSource.AI


def test_clear_ai_tags_keeps_user(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path/'t2.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    video = Video(url="u", tiktok_id="2")
    user_tag = Tag(name="моё")
    session.add_all([video, user_tag])
    session.flush()
    from toklib_core.db import VideoTag as VT
    from toklib_core.enums import TagSource as TS

    session.add(VT(video_id=video.id, tag_id=user_tag.id, source=TS.USER))
    session.commit()

    tier0.clear_ai_tags(session, video)
    session.commit()
    assert len(session.query(VT).filter(VT.video_id == video.id).all()) == 1  # пользовательский жив
