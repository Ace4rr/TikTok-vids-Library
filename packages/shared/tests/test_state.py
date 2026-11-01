import pytest
from toklib_core.db import Video
from toklib_core.enums import Health, MediaMode, VideoStatus
from toklib_core.state import PipelineError, can_transition, mark_dead_link, transition


def make_video(status=VideoStatus.INBOX):
    return Video(url="https://www.tiktok.com/@a/video/1111111111111111111", status=status)


def test_happy_path_transitions():
    v = make_video()
    transition(v, VideoStatus.RESOLVING)
    transition(v, VideoStatus.DOWNLOADING)
    transition(v, VideoStatus.TRANSCRIBING)
    transition(v, VideoStatus.ANALYZING)
    transition(v, VideoStatus.READY)
    assert v.status == VideoStatus.READY


def test_invalid_transition_raises():
    v = make_video()
    with pytest.raises(PipelineError):
        transition(v, VideoStatus.READY)  # inbox → ready запрещён
    assert v.status == VideoStatus.INBOX


def test_reruns_from_ready():
    v = make_video(VideoStatus.READY)
    assert can_transition(VideoStatus.READY, VideoStatus.ANALYZING)  # reanalyze
    transition(v, VideoStatus.ANALYZING)


def test_retry_from_error():
    make_video(VideoStatus.ERROR)
    assert can_transition(VideoStatus.ERROR, VideoStatus.RESOLVING)


def test_mark_dead_link():
    v = make_video(VideoStatus.RESOLVING)
    v.media_mode = MediaMode.NONE
    mark_dead_link(v)
    assert v.health == Health.DEAD
    assert v.media_mode == MediaMode.EMBED
    assert v.status == VideoStatus.READY
