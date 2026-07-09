import pytest
from bilibili_api import video

from biliparser.provider.bilibili.video import _resolve_video_codec


@pytest.mark.parametrize(
    ("codec_name", "expected"),
    [
        ("avc", video.VideoCodecs.AVC),
        ("AVC", video.VideoCodecs.AVC),
        ("hev", video.VideoCodecs.HEV),
        ("hvc", video.VideoCodecs.HEV),
        ("av1", video.VideoCodecs.AV1),
        ("av01", video.VideoCodecs.AV1),
        ("", video.VideoCodecs.AVC),
    ],
)
def test_resolve_video_codec_supports_names_and_aliases(codec_name, expected):
    assert _resolve_video_codec(codec_name) is expected
