import asyncio
import random
from urllib.parse import urljoin
from tortoise.queryset import Q

from loguru import logger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from db.db import Client, User
from utils.http_client import http


async def fetch_status(url: str) -> tuple[str, bool | None]:
    """请求节点状态信息"""
    try:
        resp = await http.get(urljoin(url, "/status"), timeout=15)
        data = resp.json()
        return data["status"]["msg"], data["status"]["enable_GP_cost"]
    except Exception as e:
        logger.error(f"获取节点 {url} 状态失败：{e}")
        return "网络异常", None


async def refresh_client_status(client: Client, app=None) -> tuple[str, bool | None]:
    """刷新单个节点状态"""
    status, enable_GP_cost = await fetch_status(client.url)
    if enable_GP_cost is not None:
        client.enable_GP_cost = enable_GP_cost
    client.status = "正常"
    if status == "网络异常":
        client.status = "网络异常"
    else:
        if status["EX"] != "EX":
            client.status = "无法访问ex站点! "
        elif not status['Free'] and not enable_GP_cost:
            client.status = "配额不足! "
        else:
            if status['GP'] and status['Credits']:
                if int(status['GP']) < 50000 and int(status['Credits']) < 10000:
                    client.status = "GP和C不足! "
            else:
                client.status = "无法获得GP和C! "
        client.EX = status['EX']
        client.Free = status['Free']
        client.GP = status['GP']
        client.Credits = status['Credits']
        if "http" in status['EX'] and app:
            text = f"节点异常\nURL：{client.url}\n状态：{status['EX']}"
            keyboard = [
                [InlineKeyboardButton("管理节点", callback_data=f"client|{client.id}")]
            ]
            await app.bot.sendMessage(
                client.provider_id,
                text,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
    await client.save()

    return status, client.enable_GP_cost


async def refresh_all_clients(app=None):
    """刷新所有节点状态"""
    clients = await Client.all()
    tasks = [refresh_client_status(c, app) for c in clients if c.status != "停用"]
    await asyncio.gather(*tasks)


async def add_client(user_id: int, url: str) -> tuple[bool, str, bool | None]:
    """添加新节点"""
    status, enable_GP_cost = await fetch_status(url)
    if "异常" in status:
        return False, status, None

    await Client.create(
        provider=await User.get(id=user_id),
        url=url,
        status="正常",
        enable_GP_cost=enable_GP_cost,
        EX = status['EX'],
        Free = status['Free'],
        GP = status['GP'],
        Credits = status['Credits'],
    )
    return True, status, enable_GP_cost


async def get_available_clients(require_GP: int) -> list[Client]:
    """获取可用节点"""
    clients = []
    c = await Client.all()
    for x in c:
        if x.enable_GP_cost == 0 and x.Free == 0:
            continue
        if x.status == "正常":
            if int(x.GP) >= int(require_GP):
                clients.append(x)

    random.shuffle(clients)
    return clients
