from tortoise import fields
from tortoise.models import Model


class reply_cache(Model):
    oid = fields.IntField(pk=True, unique=True)
    reply_type = fields.IntField()
    content = fields.JSONField()
    created = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "reply_cache_db"


class dynamic_cache(Model):
    dynamic_id = fields.IntField(pk=True, unique=True)
    rid = fields.IntField(unique=True)
    content = fields.JSONField()
    created = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "dynamic_cache_db"


class clip_cache(Model):
    video_id = fields.IntField(pk=True, unique=True)
    content = fields.JSONField()
    created = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "clip_cache_db"


class audio_cache(Model):
    audio_id = fields.IntField(pk=True, unique=True)
    content = fields.JSONField()
    created = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "audio_cache_db"


class live_cache(Model):
    room_id = fields.IntField(pk=True, unique=True)
    content = fields.JSONField()
    created = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "live_cache_db"


class bangumi_cache(Model):
    epid = fields.IntField(pk=True, unique=True)
    ssid = fields.IntField()
    content = fields.JSONField()
    created = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "bangumi_cache_db"


class video_cache(Model):
    aid = fields.IntField(pk=True, unique=True)
    bvid = fields.CharField(max_length=12, unique=True)
    content = fields.JSONField()
    created = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "video_cache_db"
