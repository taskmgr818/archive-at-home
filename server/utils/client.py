import asyncio
import random
from urllib.parse import urljoin

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
    client.status = status
    if enable_GP_cost is not None:
        client.enable_GP_cost = enable_GP_cost
    await client.save()

    if "异常" in status and app:
        text = f"节点异常\nURL：{client.url}\n状态：{status}"
        keyboard = [
            [InlineKeyboardButton("管理节点", callback_data=f"client|{client.id}")]
        ]
        await app.bot.sendMessage(
            client.provider_id,
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

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
        status=status,
        enable_GP_cost=enable_GP_cost,
    )
    return True, status, enable_GP_cost


async def get_available_clients() -> list[Client]:
    """获取可用节点"""
    clients = await Client.filter(status__in=["正常", "无免费额度"])

    normal = [c for c in clients if c.status == "正常"]
    fallback = [c for c in clients if c.status == "无免费额度" and c.enable_GP_cost]

    random.shuffle(normal)
    random.shuffle(fallback)

    return normal + fallback
