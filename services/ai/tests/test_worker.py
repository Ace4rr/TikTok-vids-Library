import datetime as dt
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import sessionmaker
from toklib_ai import worker as ai_worker
from toklib_ai.interfaces import AnalyzeOutput
from toklib_ai.router import AnalysisResult
from toklib_core.config import Settings
from toklib_core.db import Analysis, Base, Video, make_engine
from toklib_core.enums import JobType, MediaMode, VideoStatus


class FakeRouter:
    def __init__(self):
        self.transcriber = None
        self.embedder = None
        self.embed_result = [0.1] * 8
        self.analyze_calls = 0
        self.analyze_result = AnalysisResult(
            output=AnalyzeOutput(
                tags=["рецепт", "выпечка"],
                summary="Готовим яблочный пирог за 10 минут.",
                key_idea="Секрет — холодное масло.",
                category="рецепт",
                content_language="ru",
            ),
            model="fake-model",
            provider="fake",
            attempts=1,
        )
        self.embed_result = [0.1] * 8
        self.analyze_calls = 0

    @property
    def primary_analyzer(self):
        return SimpleNamespace(model_id="fake-model")

    def analyze(self, inp):
        self.analyze_calls += 1
        return self.analyze_result

    def embed(self, texts):
        if self.embedder is None:
            from toklib_ai.interfaces import NoProviderError

            raise NoProviderError("провайдер эмбеддингов не сконфигурирован")
        return [self.embed_result for _ in texts], "fake-embed"


def make_ctx(tmp_path, dictionary=None):
    settings = Settings()
    settings.server.data_dir = tmp_path
    router = FakeRouter()
    ctx = ai_worker.AiContext(
        settings=settings,
        router=router,
        dictionary=dictionary or {"max_tags": 5, "categories": {"рецепт": ["рецепт"]}, "tag_aliases": {}},
    )
    return ctx, router


@pytest.fixture
def db(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path/'a.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield engine, session
    session.close()


def make_job(video_id, priority=1, force=False):
    return SimpleNamespace(id=1, type=JobType.ANALYZE, payload={"video_id": video_id, "priority": priority, "force": force}, priority=priority)


def test_analyze_job_full_flow(db, tmp_path):
    _engine, session = db
    ctx, _router = make_ctx(tmp_path)
    video = Video(url="u", tiktok_id="7300000000000000011", caption="рецепт пирога", status=VideoStatus.READY)
    session.add(video)
    session.commit()

    outcome = ai_worker.run_analyze_job(ctx, session, make_job(video.id))
    assert outcome == "analyzed"
    session.expire_all()
    video = session.get(Video, video.id)
    assert video.status == VideoStatus.READY
    assert video.partial_ai is False
    analysis = session.query(Analysis).one()
    assert analysis.summary.startswith("Готовим")
    assert analysis.model == "fake-model"
    tag_names = [link.tag.name for link in video.tag_rows]
    assert "рецепт" in tag_names and "выпечка" in tag_names


def test_analyze_cache_prevents_rerun(db, tmp_path):
    _engine, session = db
    ctx, router = make_ctx(tmp_path)
    video = Video(url="u", tiktok_id="7300000000000000012", caption="одинаково", status=VideoStatus.READY)
    session.add(video)
    session.commit()

    ai_worker.run_analyze_job(ctx, session, make_job(video.id))
    assert router.analyze_calls == 1
    ai_worker.run_analyze_job(ctx, session, make_job(video.id))
    assert router.analyze_calls == 1  # кэш по input_hash (docs/07 №6)


def test_analyze_force_bypasses_cache(db, tmp_path):
    _engine, session = db
    ctx, router = make_ctx(tmp_path)
    video = Video(url="u", tiktok_id="7300000000000000013", caption="x", status=VideoStatus.READY)
    session.add(video)
    session.commit()
    ai_worker.run_analyze_job(ctx, session, make_job(video.id))
    ai_worker.run_analyze_job(ctx, session, make_job(video.id, force=True))
    assert router.analyze_calls == 2


def test_analyze_budget_defers_to_night(db, tmp_path):
    from toklib_core.db import utcnow

    _engine, session = db
    ctx, _router = make_ctx(tmp_path)
    ctx.settings.ai.budgets.analysis_per_day = 0
    video = Video(url="u", tiktok_id="7300000000000000014", status=VideoStatus.READY)
    session.add(video)
    session.commit()
    session.add(Analysis(video_id=video.id, model="m", prompt_version="v1", input_hash="h", created_at=utcnow()))
    session.commit()

    ctx.settings.ai.budgets.analysis_per_day = 1  # лимит исчерпан
    job = make_job(video.id)
    outcome = ai_worker.run_analyze_job(ctx, session, job)
    assert outcome == "rescheduled"
    # run_after — naive UTC; отложено в ближайшее ночное окно (максимум ~24ч)
    assert utcnow() < job.run_after <= utcnow() + dt.timedelta(hours=24)


def test_analyze_degradation_on_failure(db, tmp_path):
    from toklib_ai.interfaces import AnalysisFailed

    _engine, session = db
    ctx, router = make_ctx(tmp_path)

    def fail(inp):
        raise AnalysisFailed("все провайдеры мертвы")

    router.analyze = fail
    video = Video(url="u", tiktok_id="7300000000000000015", caption="подпись", status=VideoStatus.READY)
    session.add(video)
    session.commit()

    outcome = ai_worker.run_analyze_job(ctx, session, make_job(video.id))
    assert outcome == "degraded"
    session.expire_all()
    video = session.get(Video, video.id)
    assert video.partial_ai is True
    assert video.status == VideoStatus.READY  # деградация, не блокировка


def test_transcribe_without_media_is_noop(db, tmp_path):
    _engine, session = db
    ctx, _ = make_ctx(tmp_path)
    video = Video(url="u", tiktok_id="7300000000000000016", status=VideoStatus.READY, media_mode=MediaMode.EMBED)
    session.add(video)
    session.commit()
    assert ai_worker.run_transcribe_job(ctx, session, make_job(video.id)) == "no-media"


def test_embed_job_no_provider_skips(db, tmp_path):
    _engine, session = db
    ctx, _ = make_ctx(tmp_path)
    video = Video(url="u", tiktok_id="7300000000000000017", caption="текст", status=VideoStatus.READY)
    session.add(video)
    session.commit()
    assert ai_worker.run_embed_job(ctx, session, make_job(video.id)) == "skipped"
