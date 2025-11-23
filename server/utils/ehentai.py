import re

import httpx

from config.config import cfg
from utils.http_client import http

EX_BASE_URL = "https://exhentai.org"
EH_BASE_URL = "https://e-hentai.org"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36 Edg/135.0.0.0",
    "Cookie": cfg["eh_cookie"],
}


def _get_base_url():
    try:
        res = httpx.get(EX_BASE_URL, headers=headers, proxy=cfg["proxy"])
        if res.text != "":
            return EX_BASE_URL
    except:
        pass
    return EH_BASE_URL


base_url = _get_base_url()


async def get_gdata(gid, token):
    url = f"{base_url}/api.php"
    data = {"method": "gdata", "gidlist": [[gid, token]], "namespace": 1}
    response = await http.post(url, headers=headers, json=data)
    result = response.json().get("gmetadata")[0]
    return result


async def get_user_GP_cost(gid, token):
    url = f"{base_url}/archiver.php?gid={gid}&token={token}"
    response = await http.post(url, headers=headers)
    original_div = re.search(
        r"float:left.*float:right", response.text, re.DOTALL
    ).group()
    file_size = re.findall(r"<strong>(.*?)</strong>", original_div)[1]
    user_GP_cost = int(
        float((f := file_size.split())[0])
        * {"KiB": 20 / 1024, "MiB": 20, "GiB": 20480}[f[1]]
    )
    return user_GP_cost
