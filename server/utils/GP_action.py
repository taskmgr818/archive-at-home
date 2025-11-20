import random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from loguru import logger
from tortoise.expressions import Q

from db.db import GPRecord, User


# 获取用户当前有效 GP 总额
def get_current_GP(user: User) -> int:
    now = datetime.now(tz=timezone.utc)
    return sum(
        r.amount for r in user.GP_records if r.expire_time > now and r.amount > 0
    )


async def checkin(user: User):
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()

    already_checked = any(
        record.source == "签到"
        and record.expire_time.astimezone(ZoneInfo("Asia/Shanghai")).date()
        == today + timedelta(days=7)
        for record in user.GP_records
    )

    original_balance = get_current_GP(user)
    if already_checked:
        return 0, original_balance

    amount = random.randint(10000, 20000)
    await GPRecord.create(user=user, amount=amount)
    logger.info(f"{user.name}（{user.id}）签到成功，获得 {amount} GP")

    return amount, original_balance + amount


# 扣除 GP
async def deduct_GP(user: User, amount: int):
    now = datetime.now()
    valid_GP = (
        await user.GP_records.filter(expire_time__gt=now, amount__gt=0)
        .order_by("expire_time")
        .all()
    )

    total_deducted = 0
    for record in valid_GP:
        if total_deducted >= amount:
            break
        deduct_amount = min(record.amount, amount - total_deducted)
        record.amount -= deduct_amount
        total_deducted += deduct_amount
        await record.save()


async def clean_GP_records(_):
    now = datetime.now()
    await GPRecord.filter(Q(expire_time__lte=now) | Q(amount__lte=0)).delete()
