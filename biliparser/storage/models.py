import os

from tortoise import fields
from tortoise.models import Model


class TelegramFileCache(Model):
    """Telegram Channel 专用：mediafilename -> file_id 映射"""

    mediafilename = fields.CharField(64, pk=True, unique=True)
    file_id = fields.CharField(128, unique=True)
    created = fields.DatetimeField(auto_now=True)

    class Meta(Model.Meta):
        table = os.environ.get("FILE_TABLE", "file")
