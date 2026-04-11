from biliparser.channel import Channel
from biliparser.model import Author, MediaConstraints, ParsedContent


class DummyChannel(Channel):
    @property
    def media_constraints(self):
        return MediaConstraints(
            max_upload_size=50 * 1024 * 1024,
            max_download_size=2 * 1024 * 1024 * 1024,
            caption_max_length=1024,
        )

    def format_caption(self, content):
        return content.url

    async def send_content(self, content, media, context):
        pass

    async def send_text(self, text, context):
        pass

    async def cache_sent_media(self, content, result):
        pass

    async def get_cached_media(self, filename):
        return None

    async def start(self, provider_registry):
        pass

    async def stop(self):
        pass


def test_channel_media_constraints():
    ch = DummyChannel()
    mc = ch.media_constraints
    assert mc.max_upload_size == 50 * 1024 * 1024


def test_channel_format_caption():
    ch = DummyChannel()
    pc = ParsedContent(url="https://example.com", author=Author())
    assert ch.format_caption(pc) == "https://example.com"
