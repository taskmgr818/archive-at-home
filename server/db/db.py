import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from tortoise import Tortoise, fields
from tortoise.models import Model


class User(Model):
    id = fields.IntField(pk=True)
    name = fields.CharField(max_length=255)
    apikey = fields.UUIDField(default=uuid4)
    group = fields.CharField(max_length=50, default="普通用户")

    GP_records = fields.ReverseRelation["GPRecord"]
    clients = fields.ReverseRelation["Client"]
    archive_histories = fields.ReverseRelation["ArchiveHistory"]


class GPRecord(Model):
    user = fields.ForeignKeyField("models.User", related_name="GP_records")
    amount = fields.IntField()
    expire_time = fields.DatetimeField(
        default=lambda: datetime.now(tz=timezone.utc) + timedelta(days=7)
    )
    source = fields.CharField(max_length=50, default="签到")


class Client(Model):
    url = fields.CharField(max_length=255)
    enable_GP_cost = fields.BooleanField()
    status = fields.CharField(max_length=50)
    EX = fields.CharField(max_length=255, default="None")
    Free = fields.CharField(max_length=255, default="None")
    GP = fields.CharField(max_length=255, default="None")
    Credits = fields.CharField(max_length=255, default="None")

    provider = fields.ForeignKeyField("models.User", related_name="clients")
    archive_histories = fields.ReverseRelation["ArchiveHistory"]


class ArchiveHistory(Model):
    user = fields.ForeignKeyField("models.User", related_name="archive_histories")
    gid = fields.CharField(max_length=20)
    token = fields.CharField(max_length=20)
    GP_cost = fields.IntField()
    client = fields.ForeignKeyField(
        "models.Client",
        related_name="archive_histories",
        null=True,
        on_delete=fields.SET_NULL,
    )
    time = fields.DatetimeField(default=lambda: datetime.now(tz=timezone.utc))


# 初始化数据库
async def init_db():
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DB_PATH = os.path.join(BASE_DIR, "bot_data.db")

    await Tortoise.init(db_url=f"sqlite://{DB_PATH}", modules={"models": [__name__]})
    await Tortoise.generate_schemas()
