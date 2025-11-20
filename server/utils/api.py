import asyncio
import time
from collections import defaultdict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

from db.db import User
from utils.ehentai import get_GP_cost
from utils.GP_action import checkin, deduct_GP, get_current_GP
from utils.resolve import get_download_url

processing_tasks = {}
results_cache = defaultdict(dict)
lock = asyncio.Lock()

app = FastAPI()


async def clean_results_cache(_):
    now = time.time()
    keys_to_delete = []

    for key, value in results_cache.items():
        if not value or value.get("expire_time", 0) < now:
            keys_to_delete.append(key)

    for key in keys_to_delete:
        results_cache.pop(key, None)


def format_response(code: int, msg: str, data: dict = None):
    if data is None:
        data = {}
    return JSONResponse(
        content={"code": code, "msg": msg, "data": data}, status_code=200
    )


def handle_exception(e: Exception, default_code=99):
    return format_response(default_code, f"服务器内部错误: {str(e)}")


async def verify_user(apikey: str):
    if not apikey:
        return format_response(1, "参数不完整")

    user = await User.get_or_none(apikey=apikey).prefetch_related("GP_records")
    if not user:
        return format_response(2, "无效的 API Key")

    if user.group == "黑名单":
        return format_response(3, "您已被封禁")

    return user


async def process_resolve(user, gid, token):
    try:
        user_GP_cost, require_GP = await get_GP_cost(gid, token)
    except Exception:
        return 4, "获取画廊信息失败", None

    if get_current_GP(user) < user_GP_cost:
        return 5, "GP 不足", None

    d_url = await get_download_url(user, gid, token, require_GP)
    if d_url:
        await deduct_GP(user, user_GP_cost)
        return 0, "解析成功", d_url
    return 6, "解析失败", None


@app.post("/resolve")
async def handle_resolve(request: Request):
    try:
        data = await request.json()
        apikey = data.get("apikey")
        gid = data.get("gid")
        token = data.get("token")
        force_resolve = data.get("force_resolve", False)

        if not all([apikey, gid, token]):
            return format_response(1, "参数不完整")

        user = await verify_user(apikey)
        if isinstance(user, JSONResponse):
            return user

        key = f"{user.id}|{gid}"

        # 使用缓存
        cache = results_cache.get(key)
        if cache and cache.get("expire_time", 0) > time.time() and not force_resolve:
            return format_response(0, "使用缓存记录", {"archive_url": cache["d_url"]})

        task = processing_tasks.get(key)
        if not task:
            async with lock:
                task = processing_tasks.get(key)
                if not task:
                    task = asyncio.create_task(process_resolve(user, gid, token))
                    processing_tasks[key] = task

        try:
            code, msg, d_url = await task
            if not d_url:
                return format_response(code, msg)

            results_cache[key] = {"d_url": d_url, "expire_time": time.time() + 86400}
            return format_response(code, msg, {"archive_url": d_url})

        finally:
            async with lock:
                # 确保任务完成后无论成功失败都清理任务记录
                if processing_tasks.get(key) == task:
                    del processing_tasks[key]

    except Exception as e:
        return handle_exception(e)


@app.post("/balance")
async def balance(request: Request):
    try:
        data = await request.json()
        apikey = data.get("apikey")
        user = await verify_user(apikey)
        if isinstance(user, JSONResponse):
            return user

        current_GP = get_current_GP(user)
        return format_response(0, "查询成功", {"current_GP": current_GP})

    except Exception as e:
        return handle_exception(e)


@app.post("/checkin")
async def checkin_request(request: Request):
    try:
        data = await request.json()
        apikey = data.get("apikey")
        user = await verify_user(apikey)
        if isinstance(user, JSONResponse):
            return user

        amount, current_GP = await checkin(user)
        if not amount:
            return format_response(7, "今日已签到")

        return format_response(
            0, "签到成功", {"get_GP": amount, "current_GP": current_GP}
        )

    except Exception as e:
        return handle_exception(e)


@app.get("/")
async def redirect():
    return RedirectResponse(url="https://t.me/EH_ArBot", status_code=301)
