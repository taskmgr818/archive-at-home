import re
import time
from collections import deque

import httpx
from loguru import logger

from config.config import config
from utils.ehentai import get_GP_cost

GP_usage_log = deque()

res = httpx.get("https://e-hentai.org/", proxy=config["proxy"])
test = re.search(r"https://e-hentai\.org/g/(\d+)/([0-9a-f]{10})", res.text).groups()


def is_within_global_gp_limit() -> bool:
    """检查是否仍在 GP 限制范围内"""
    max_gp = config["ehentai"]["max_GP_cost"]
    if max_gp == -1:
        return True  # 无限制
    if max_gp == 0:
        return False  # 禁止GP消耗

    now = time.time()
    one_day_ago = now - 86400
    while GP_usage_log and GP_usage_log[0][0] < one_day_ago:
        GP_usage_log.popleft()
    total_used = sum(gp for _, gp in GP_usage_log)
    return total_used < max_gp


async def get_status():
    try:
        require_GP = await get_GP_cost(*test)
        result = "无免费额度" if require_GP else "正常"
    except Exception as e:
        logger.error(e)
        result = "解析功能异常"

    return {"msg": result, "enable_GP_cost": is_within_global_gp_limit()}
