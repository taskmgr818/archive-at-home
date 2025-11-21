from collections import defaultdict
from datetime import datetime
import time
from urllib.parse import urljoin

from loguru import logger

from db.db import ArchiveHistory
from utils.client import get_available_clients
from utils.ehentai import get_gdata, get_GP_cost
from utils.http_client import http


async def fetch_tag_map(_):
    db = (
        await http.get(
            "https://github.com/EhTagTranslation/Database/releases/latest/download/db.text.json",
            follow_redirects=True,
        )
    ).json()

    global tag_map
    tag_map = defaultdict(lambda: {"name": "", "data": {}})

    for entry in db["data"][2:]:
        namespace = entry["namespace"]
        tag_map[namespace]["name"] = entry["frontMatters"]["name"]
        tag_map[namespace]["data"].update(
            {key: value["name"] for key, value in entry["data"].items()}
        )


async def get_gallery_info(gid, token):
    """获取画廊基础信息 + 缩略图"""
    require_GP = await get_GP_cost(gid, token)
    gallery_info = await get_gdata(gid, token)

    new_tags = defaultdict(list)
    for item in gallery_info["tags"]:
        ns, sep, tag = item.partition(":")
        if not sep:
            continue
        if (ns_info := tag_map.get(ns)) and (tag_name := ns_info["data"].get(tag)):
            new_tags[ns_info["name"]].append(f"#{tag_name}")

    tag_text = "\n".join(
        f"{ns_name}：{' '.join(tags_list)}" for ns_name, tags_list in new_tags.items()
    )

    text = (
        f"📌 主标题：{gallery_info['title']}\n"
        + (
            f"⭐ 评分：{gallery_info['rating']}\n"
            if float(gallery_info["posted"]) < datetime.now().timestamp() - 172800
            else ""
        )
        + f"<blockquote expandable>📙 副标题：{gallery_info['title_jpn']}\n"
        f"📂 类型：{gallery_info['category']}\n"
        f"👤 上传者：<a href='https://e-hentai.org/uploader/{gallery_info['uploader']}'>{gallery_info['uploader']}</a>\n"
        f"🕒 上传时间：{datetime.fromtimestamp(float(gallery_info['posted'])):%Y-%m-%d %H:%M}\n"
        f"📄 页数：{gallery_info['filecount']}\n\n"
        f"{tag_text}\n\n"
        f"💰 归档消耗 GP：原图({require_GP['org']}) 重采样({require_GP['res']})</blockquote>"
    )

    posted_ts = float(gallery_info['posted'])
    now_ts = time.time()
    return (
        text,
        gallery_info["category"] != "Non-H",
        gallery_info["thumb"].replace("s.exhentai", "ehgt"),
        require_GP,
        1 if now_ts - posted_ts > 365 * 24 * 3600 else 0
    )


async def get_download_url(user, gid, token, image_quality, require_GP, timeout):
    """向可用节点请求下载链接"""
    clients = await get_available_clients(int(require_GP), timeout)
    if not clients:
        return None
    for client in clients:
        try:
            response = await http.post(
                urljoin(client.url, "/resolve"),
                json={
                    "username": user.name,
                    "gid": gid,
                    "token": token,
                    "image_quality": image_quality,
                },
                timeout=60,
            )
            data = response.json()

            # 更新节点状态
            status = data["status"]["msg"]
            if status["Free"] == 0 and client.enable_GP_cost == 0:
                client.status = "配额不足，停止解析"
            client.EX = status["EX"]
            client.GP = status["GP"]
            client.Credits = status["Credits"]
            client.enable_GP_cost = data["status"]["enable_GP_cost"]
            await client.save()

            if data.get("msg") == "Success":
                await ArchiveHistory.create(
                    user=user,
                    gid=gid,
                    token=token,
                    GP_cost=data["require_GP"],
                    client=client,
                )
                logger.info(
                    f"节点 {client.url} 解析 https://e-hentai.org/g/{gid}/{token}/ 成功"
                )
                return data["d_url"].replace("?autostart=1", "")
            error_msg = data.get("msg")
        except Exception as e:
            client.status = "异常"
            await client.save()
            error_msg = e
        logger.error(
            f"节点 {client.url} 解析 https://e-hentai.org/g/{gid}/{token}/ 失败：{error_msg}"
        )
