import re

import httpx

from bs4 import BeautifulSoup

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


async def get_GP_cost(gid, token):
    def convert_to_mib(size_str):
        # 匹配数字和单位，并允许单位后有额外空格
        match = re.match(r"(\d+\.?\d*)\s*(\w+)?", size_str.strip())
        
        if not match:
            raise ValueError(f"Invalid size format: {size_str}")  # 捕获具体的大小格式
        
        size = float(match.group(1))  # 获取数字部分
        unit = match.group(2)  # 获取单位部分，如果没有单位则返回 None

        # 如果没有单位，默认按 MiB 计算
        if unit is None:
            unit = "MiB"

        # 根据单位转换为 MiB
        conversion_factors = {
            "gib": 1024,         # 1 GiB = 1024 MiB
            "gb": 1024 / 1.048576,  # 1 GB = 976.5625 MiB
            "mib": 1,            # 1 MiB = 1 MiB
            "mb": 1 / 1.048576,   # 1 MB = 0.953674 MiB
            "kib": 1 / 1024,     # 1 KiB = 0.0009765625 MiB
            "kb": 1 / 1024 / 1.048576,  # 1 KB = 0.000931322 MiB
        }

        if unit.lower() in conversion_factors:
            return (size * conversion_factors[unit.lower()]) * 21
        else:
            raise ValueError(f"Unsupported unit: {unit}")

    
    require_GP = {"org": None, "res": None}
    url = f"{base_url}/archiver.php?gid={gid}&token={token}"
    response = await http.post(url, headers=headers)
    soup = BeautifulSoup(response.text, 'html.parser')
    GPs = soup.find_all('strong')
    if GPs:
        if GPs[2].text == "Free!":
            require_GP["org"], require_GP["res"] = round(float(convert_to_mib(GPs[1].text))), round(float(convert_to_mib(GPs[3].text)))
        else:
            require_GP['org'], require_GP["res"] = ''.join([ch for ch in GPs[0].text if ch.isdigit()]), ''.join([ch for ch in GPs[2].text if ch.isdigit()])
    else:
        if response.url == "https://e-hentai.org/bounce_login.php?b=d&bt=1-4":
            return "服务器cookie异常"
    return require_GP
