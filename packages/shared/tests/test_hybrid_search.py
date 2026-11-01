from array import array

import pytest
from sqlalchemy.orm import sessionmaker
from toklib_core import search as search_mod
from toklib_core.db import Base, Embedding, Video, make_engine


@pytest.fixture
def db(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path/'h.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    with engine.connect() as conn:
        search_mod.ensure_fts(conn)

    # v1: и по подписи, и по вектору ближе всех
    v1 = Video(url="u1", tiktok_id="7300000000000000001", caption="рецепт холодного фарша")
    v2 = Video(url="u2", tiktok_id="7300000000000000002", caption="тренировка на пресс")
    v3 = Video(url="u3", tiktok_id="7300000000000000003", caption="про фарш и пасту")
    session.add_all([v1, v2, v3])
    session.commit()

    # векторы: v1 ближе всего к запросу «фарш»
    model = "test-embed"
    vectors = {v1.id: [1.0, 0.0], v3.id: [0.6, 0.8], v2.id: [0.0, 1.0]}
    for vid, vec in vectors.items():
        session.add(
            Embedding(
                entity_type="video",
                entity_id=vid,
                model_id=model,
                dim=len(vec),
                content_hash="h",
                vector=array("f", vec).tobytes(),
            )
        )
    session.commit()
    for video in (v1, v2, v3):
        session.refresh(video)
        search_mod.index_video_orm(engine, video)
    yield engine, session, model
    session.close()


def query_vector_provider(vector):
    def provider(text: str):
        return vector, "test-embed"

    return provider


def test_rrf_merge_prefises_overlap():
    # совпадение в обоих списках поднимает элемент наверх
    merged = search_mod._rrf_merge([1, 2, 3], [2, 4, 1])
    assert merged[0] == 2
    assert set(merged) == {1, 2, 3, 4}


def test_hybrid_ranks_by_vector_when_fts_weak(db):
    engine, _session, _model = db
    result = search_mod.search(
        engine,
        "фарш",
        vector_provider=query_vector_provider([1.0, 0.0]),
    )
    assert result["mode"] == "hybrid"
    assert result["ids"][0] == 1  # вектор тянет v1 наверх


def test_hybrid_works_via_sql_or_python_fallback(db):
    """sqlite-vec может быть недоступен (часть сборок CPython запрещает расширения) —
    тогда SQL-путь падает и внутри _vector_branch срабатывает питонный fallback.
    Результат одинаков на обоих путях."""
    engine, _session, model = db
    ids = search_mod._vector_branch(engine, search_mod.parse_query("фарш"), [1.0, 0.0], model)
    assert ids[0] == 1
    result = search_mod.search(engine, "фарш", vector_provider=query_vector_provider([1.0, 0.0]))
    assert result["mode"] == "hybrid"
    assert result["ids"][0] == 1


def test_provider_failure_degrades_to_fts(db):
    engine, _session, _model = db

    def broken_provider(text):
        raise RuntimeError("embedder down")

    result = search_mod.search(engine, "фарш", vector_provider=broken_provider)
    assert result["mode"] == "fts"
    assert result["total"] >= 1


def test_no_provider_is_plain_fts(db):
    engine, _session, _model = db
    result = search_mod.search(engine, "фарш", vector_provider=None)
    assert result["mode"] == "fts"


def test_vector_respects_operator_filters(db):
    engine, _session, _model = db
    # author-оператор, которому не соответствует ни одно видео, опустошает и векторную ветку
    result = search_mod.search(
        engine,
        "фарш author:nobody",
        vector_provider=query_vector_provider([1.0, 0.0]),
    )
    assert result["total"] == 0
    assert result["ids"] == []


def test_python_cosine_rank_skips_wrong_dim(db):
    rows = [(10, array("f", [1.0, 0.0, 0.0]).tobytes()), (11, array("f", [0.9, 0.1]).tobytes())]
    ranked = search_mod._cosine_rank_python(rows, [1.0, 0.0], top_k=5)
    assert ranked == [11]  # трёхмерный вектор другой модели пропущен
