import re
import time

from collections import deque
from bs4 import BeautifulSoup

import httpx
from loguru import logger

from config.config import config
from utils.ehentai import get_GP_cost, _get_base_url

GP_usage_log = deque()

res = httpx.get("https://e-hentai.org/", proxy=config["proxy"])
test = re.search(r"https://e-hentai\.org/g/(\d+)/([0-9a-f]{10})", res.text).groups()

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36 Edg/135.0.0.0",
    "Cookie": config["ehentai"]["cookies"],
}

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
    status = {
        "EX": "",
        "Free": "",
        "GP": "",
        "Credits": ""
    }
    text = _get_base_url()
    if text == "https://exhentai.org":
        status["EX"] = "EX"
    elif text == "https://e-hentai.org":
        status["EX"] = "EH"
    else:
        status["EX"] = text
    try:
        res = httpx.get("https://e-hentai.org/archiver.php?gid=3614913&token=135f66307e", headers=headers, proxy=config["proxy"])
        soup = BeautifulSoup(res.text, 'html.parser')
        divs_with_left = soup.find('div', style=lambda value: value and 'left' in value)
        if divs_with_left:
            strong = divs_with_left.find_all('strong')
            if strong[0].text == 'Free!':
                status["Free"] = 1
            else:
                status["Free"] = 0
        p_tag = soup.find_all('p')[1] if len(soup.find_all('p')) > 1 else None
        if p_tag:
            # 这里是正则替换，将字母和数字字符删除掉
            G_and_C = re.sub(r'[^a-zA-Z0-9]', '', p_tag.get_text(strip=True)).split('GP')
            if len(G_and_C) == 2:
                GC = [G_and_C[0], G_and_C[1].replace('Credits', '')]
                status["GP"] = GC[0]
                status["Credits"] = GC[1]
    except Exception as e:
        logger.error(e)
    return {"msg": status, "enable_GP_cost": is_within_global_gp_limit()}
