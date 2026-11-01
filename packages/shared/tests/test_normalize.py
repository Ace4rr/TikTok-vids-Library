import pytest
from toklib_core.normalize import LinkError, extract_url, parse_link


def test_extract_url_from_share_text():
    text = 'смотри что нашёл https://www.tiktok.com/@user/video/7301234567890123456 — огонь'
    assert extract_url(text) == "https://www.tiktok.com/@user/video/7301234567890123456"


def test_extract_url_none():
    assert extract_url("ничего нет") is None
    assert extract_url("") is None


def test_parse_full_video_url():
    parsed = parse_link("https://www.tiktok.com/@user/video/7301234567890123456")
    assert parsed.tiktok_id == "7301234567890123456"
    assert parsed.is_short is False
    assert parsed.url == "https://www.tiktok.com/@-/video/7301234567890123456"


def test_parse_photo_url():
    parsed = parse_link("https://www.tiktok.com/@user/photo/7301234567890123456")
    assert parsed.tiktok_id == "7301234567890123456"


def test_parse_v_short_path():
    parsed = parse_link("https://www.tiktok.com/v/7301234567890123456")
    assert parsed.tiktok_id == "7301234567890123456"


def test_parse_short_link_no_id():
    parsed = parse_link("https://vm.tiktok.com/ZMabcdef/")
    assert parsed.tiktok_id is None
    assert parsed.is_short is True


def test_parse_t_path():
    parsed = parse_link("https://www.tiktok.com/t/ZTabc123/")
    assert parsed.is_short is True
    assert parsed.tiktok_id is None


def test_embed_url():
    parsed = parse_link("https://www.tiktok.com/embed/v2/7301234567890123456")
    assert parsed.tiktok_id == "7301234567890123456"


def test_host_allowlist_rejects_foreign():
    with pytest.raises(LinkError):
        parse_link("https://evil.example.com/video/123")


def test_host_allowlist_rejects_lookalike():
    with pytest.raises(LinkError):
        parse_link("https://tiktok.com.evil.io/video/123")


def test_no_url_raises():
    with pytest.raises(LinkError):
        parse_link("просто текст без ссылки")


def test_extra_hosts_allowed():
    parsed = parse_link("https://mirror.internal/video/7301234567890123456", extra_hosts=("mirror.internal",))
    assert parsed.tiktok_id == "7301234567890123456"


def test_share_text_with_punctuation():
    text = "https://vm.tiktok.com/ZMabc/."
    assert extract_url(text) == "https://vm.tiktok.com/ZMabc/"
