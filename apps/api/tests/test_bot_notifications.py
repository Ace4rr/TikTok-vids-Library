"""Форматирование Telegram-уведомлений бота (SSE → текст сообщения)."""

from toklib_api.bot import notification_text

DETAIL = {"caption": "рецепт холодного фарша #recipes", "author_username": "user"}


def test_ready_notification():
    text = notification_text({"kind": "video", "video_id": 1, "status": "ready"}, DETAIL)
    assert text is not None
    assert text.startswith("✅")
    assert "рецепт холодного фарша" in text
    assert "@user" in text


def test_dead_link_notification():
    text = notification_text({"kind": "video", "video_id": 2, "status": "ready", "health": "dead"}, DETAIL)
    assert text is not None
    assert text.startswith("💀")


def test_error_notification():
    text = notification_text({"kind": "video", "video_id": 3, "status": "error"}, None)
    assert text is not None
    assert text.startswith("⚠️")


def test_intermediate_statuses_silent():
    assert notification_text({"kind": "video", "video_id": 1, "status": "resolving"}, None) is None
    assert notification_text({"kind": "video", "video_id": 1, "status": "downloading"}, None) is None
    assert notification_text({"kind": "inbox", "status": "new"}, None) is None


def test_long_caption_truncated():
    detail = {"caption": "х" * 300, "author_username": None}
    text = notification_text({"kind": "video", "video_id": 1, "status": "ready"}, detail)
    assert text is not None
    assert len(text) < 160
    assert text.endswith("…")
