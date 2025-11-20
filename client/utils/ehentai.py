import re

import httpx
from bs4 import BeautifulSoup
from config.config import config
from loguru import logger

http = httpx.AsyncClient(proxy=config["proxy"])


EX_BASE_URL = "https://exhentai.org"
EH_BASE_URL = "https://e-hentai.org"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36 Edg/135.0.0.0",
    "Cookie": config["ehentai"]["cookies"],
}


def _get_base_url():
    try:
        res = httpx.get(EX_BASE_URL, headers=headers, proxy=config["proxy"])
        if res.text != "":
            return EX_BASE_URL
        else:
            res = httpx.get(EH_BASE_URL, headers=headers, proxy=config["proxy"])
            return EH_BASE_URL
    except TimeoutError:
        return "访问超时"
    except Exception as e:
        logger.error(e)
        return "错误"


base_url = _get_base_url()


async def _archiver(gid, token, data=None):
    url = f"{base_url}/archiver.php?gid={gid}&token={token}"
    response = await http.post(url, headers=headers, data=data)
    return response.text


async def get_GP_cost(gid, token, image_quality):
    response = await _archiver(gid, token)
    soup = BeautifulSoup(response, "html.parser")
    GPs = soup.find_all("strong")
    if image_quality == "org":
        if GPs[0].text == "Free!":
            client_GP_cost = 0
        else:
            client_GP_cost = "".join([ch for ch in GPs[0].text if ch.isdigit()])
    else:
        if GPs[2].text == "Free!":
            client_GP_cost = 0
        else:
            client_GP_cost = "".join([ch for ch in GPs[2].text if ch.isdigit()])
    return client_GP_cost


async def get_download_url(gid, token, image_quality):
    response = await _archiver(
        gid,
        token,
        {
            "dltype": image_quality,
            "dlcheck": f"Download+{'Original' if image_quality == 'org' else 'Resample'}+Archive",
        },
    )
    d_url = re.search(r'document\.location = "(.*?)";', response, re.DOTALL).group(1)
    if not d_url:
        raise RuntimeError("归档链接获取失败")
    await _archiver(gid, token, {"invalidate_sessions": "1"})
    return f"{d_url.removesuffix('?autostart=1')}?start=1"
