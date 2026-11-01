import datetime as dt

import pytest
from sqlalchemy.orm import sessionmaker
from toklib_core import queue as job_queue
from toklib_core.db import Base, Job, Video, make_engine, utcnow
from toklib_core.enums import JobPriority, JobStatus, JobType, VideoStatus


@pytest.fixture
def session(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path/'q.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    s = factory()
    yield s
    s.close()


def test_enqueue_and_claim_order(session):
    v_low = Video(url="https://www.tiktok.com/@a/video/1111111111111111111", status=VideoStatus.INBOX)
    v_high = Video(url="https://www.tiktok.com/@a/video/2222222222222222222", status=VideoStatus.INBOX)
    session.add_all([v_low, v_high])
    session.commit()

    job_queue.enqueue(session, JobType.RESOLVE, video_id=v_low.id, priority=int(JobPriority.BACKLOG))
    job_queue.enqueue(session, JobType.RESOLVE, video_id=v_high.id, priority=int(JobPriority.HIGH))

    claim = job_queue.claim_next(session, "w1", [JobType.RESOLVE])
    assert claim is not None
    assert claim.payload["video_id"] == v_high.id  # priority 0 идёт вперёд backlog


def test_claim_marks_running_with_lease(session):
    job_queue.enqueue(session, JobType.RESOLVE, video_id=1)
    claim = job_queue.claim_next(session, "w1", [JobType.RESOLVE], lease_seconds=600)
    assert claim is not None
    job = session.get(Job, claim.id)
    assert job.status == JobStatus.RUNNING
    assert job.locked_by == "w1"
    assert job.attempts == 1
    # второй воркер ту же задачу взять не может
    assert job_queue.claim_next(session, "w2", [JobType.RESOLVE]) is None


def test_fail_backoff_then_dead_letter(session):
    job_queue.enqueue(session, JobType.DOWNLOAD, video_id=1)
    claim = job_queue.claim_next(session, "w1", [JobType.DOWNLOAD])

    attempts = 0
    while True:
        status = job_queue.fail(session, claim.id, f"boom {attempts}")
        attempts += 1
        if status == "dead":
            break
        assert status == "retry"
        job = session.get(Job, claim.id)
        assert job.status == JobStatus.PENDING
        assert job.run_after > utcnow()
        assert job.attempts == attempts
        # симулируем истечение backoff и берём задачу снова
        job.run_after = utcnow() - dt.timedelta(seconds=1)
        session.commit()
        claim = job_queue.claim_next(session, "w1", [JobType.DOWNLOAD])
        assert claim is not None
        session.expire_all()  # claim — raw SQL: обновляем ORM-объекты

    assert attempts == 5
    job = session.get(Job, claim.id)
    assert job.status == JobStatus.FAILED
    assert job.last_error == "boom 4"


def test_idempotency_key_prevents_duplicate_pending(session):
    j1 = job_queue.enqueue(session, JobType.RESOLVE, video_id=1, idempotency_key="resolve:1")
    j2 = job_queue.enqueue(session, JobType.RESOLVE, video_id=1, idempotency_key="resolve:1")
    assert j1.id == j2.id


def test_reap_expired_leases(session):
    job_queue.enqueue(session, JobType.RESOLVE, video_id=1)
    claim = job_queue.claim_next(session, "w1", [JobType.RESOLVE], lease_seconds=600)
    assert claim is not None
    # lease не истёк — reap ничего не возвращает
    assert job_queue.reap_expired_leases(session, 600) == 0
    job = session.get(Job, claim.id)
    job.locked_at = utcnow() - dt.timedelta(hours=2)
    session.commit()
    assert job_queue.reap_expired_leases(session, 600) == 1
    job = session.get(Job, claim.id)
    assert job.status == JobStatus.PENDING
    assert job.locked_by is None


def test_queue_depths_and_download_ratio(session):
    job_queue.enqueue(session, JobType.RESOLVE, video_id=1)
    job_queue.enqueue(session, JobType.DOWNLOAD, video_id=2)
    claim = job_queue.claim_next(session, "w1", [JobType.DOWNLOAD])
    job_queue.complete(session, claim.id)
    job_queue.enqueue(session, JobType.DOWNLOAD, video_id=3)

    depths = job_queue.queue_depths(session)
    assert depths == {"resolve": 1, "download": 1}
    ratio = job_queue.download_success_ratio(session)
    assert ratio == pytest.approx(0.5)
