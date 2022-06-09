from datetime import timedelta

from tortoise import fields
from tortoise.models import Model


class reply_cache(Model):
    oid = fields.BigIntField(pk=True, unique=True)
    reply_type = fields.IntField()
    content: dict = fields.JSONField()
    created = fields.DatetimeField(auto_now=True)
    timeout = timedelta(minutes=20)

    class Meta:
        table = "reply"


class dynamic_cache(Model):
    dynamic_id = fields.BigIntField(pk=True, unique=True)
    rid = fields.BigIntField(unique=True)
    content: dict = fields.JSONField()
    created = fields.DatetimeField(auto_now=True)
    timeout = timedelta(days=10)

    class Meta:
        table = "dynamic"


class audio_cache(Model):
    audio_id = fields.IntField(pk=True, unique=True)
    content: dict = fields.JSONField()
    created = fields.DatetimeField(auto_now=True)
    timeout = timedelta(days=10)

    class Meta:
        table = "audio"


class live_cache(Model):
    room_id = fields.IntField(pk=True, unique=True)
    content: dict = fields.JSONField()
    created = fields.DatetimeField(auto_now=True)
    timeout = timedelta(minutes=5)

    class Meta:
        table = "live"


class bangumi_cache(Model):
    epid = fields.IntField(pk=True, unique=True)
    ssid = fields.IntField()
    content: dict = fields.JSONField()
    created = fields.DatetimeField(auto_now=True)
    timeout = timedelta(days=10)

    class Meta:
        table = "bangumi"


class video_cache(Model):
    aid = fields.BigIntField(pk=True, unique=True)
    bvid = fields.CharField(max_length=12, unique=True)
    content: dict = fields.JSONField()
    created = fields.DatetimeField(auto_now=True)
    timeout = timedelta(days=10)

    class Meta:
        table = "video"

class read_cache(Model):
    read_id = fields.IntField(pk=True, unique=True)
    graphurl = fields.TextField()
    created = fields.DatetimeField(auto_now=True)
    timeout = timedelta(days=10)

    class Meta:
        table = "read"
