import httpx

from config.config import cfg

# 创建一个全局的 AsyncClient 实例
http = httpx.AsyncClient(proxy=cfg["proxy"])
