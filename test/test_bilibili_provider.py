from biliparser.provider.bilibili import BilibiliProvider


def test_can_handle_video():
    p = BilibiliProvider()
    assert p.can_handle("https://www.bilibili.com/video/BV1bW411n7fY")


def test_can_handle_live():
    p = BilibiliProvider()
    assert p.can_handle("https://live.bilibili.com/115")


def test_can_handle_audio():
    p = BilibiliProvider()
    assert p.can_handle("https://www.bilibili.com/audio/au1360511")


def test_can_handle_dynamic():
    p = BilibiliProvider()
    assert p.can_handle("https://t.bilibili.com/379593676394065939")


def test_cannot_handle_other():
    p = BilibiliProvider()
    assert not p.can_handle("https://youtube.com/watch?v=abc")
