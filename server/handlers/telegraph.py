"""Telegraph 推送功能"""

import re
import asyncio
from datetime import datetime

from loguru import logger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from telepress import publish_text, TelePressError, ValidationError
from utils.ehentai import get_gdata, get_gallery_html
from utils import resolve
from config.config import cfg


async def publish_to_telegraph(gid: str, token: str) -> tuple[str | None, str | None]:
    """将画廊信息发布到 Telegraph
    
    Args:
        gid: 画廊 ID
        token: 画廊 token
    
    Returns:
        tuple: (telegraph_url, error_message)
    """
    try:
        if not gid or not token:
            return None, "画廊 ID 或 token 为空"
        
        gallery = await get_gdata(gid, token)
        
        if gallery.get("error"):
            return None, f"画廊错误: {gallery.get('error')}"
            
        # 获取预览图
        previews = []
        try:
            html = await get_gallery_html(gid, token)
            found_urls = re.findall(r'https?://(?:[a-z0-9-]+\.)*(?:ehgt|exhentai|e-hentai)\.org/[a-z]/[0-9a-f/]+\.jpg', html)
            
            seen = set()
            for url in found_urls:
                if url not in seen and ('/t/' in url or '/m/' in url):
                    seen.add(url)
                    previews.append(url)
            previews = previews[:20]
        except Exception as e:
            logger.warning(f"获取预览图失败: {e}")
        
        title = gallery.get("title", "未知标题")
        title_jpn = gallery.get("title_jpn", "")
        category = gallery.get("category", "未知")
        uploader = gallery.get("uploader", "未知")
        posted = gallery.get("posted", "")
        filecount = gallery.get("filecount", "0")
        filesize = gallery.get("filesize", 0)
        rating = gallery.get("rating", "0")
        tags = gallery.get("tags", [])
        
        # 转换时间戳
        if posted:
            try:
                posted_time = datetime.fromtimestamp(float(posted)).strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, OSError, OverflowError):
                posted_time = posted
        else:
            posted_time = "未知"
        
        # 转换文件大小
        if filesize:
            if filesize > 1024 * 1024 * 1024:
                size_str = f"{filesize / 1024 / 1024 / 1024:.2f} GB"
            elif filesize > 1024 * 1024:
                size_str = f"{filesize / 1024 / 1024:.2f} MB"
            else:
                size_str = f"{filesize / 1024:.2f} KB"
        else:
            size_str = "未知"
        
        # 整理标签（使用项目的 tag_map 翻译，安全获取）
        tags_by_type = {}
        try:
            tag_map = resolve.tag_map
        except AttributeError:
            tag_map = {}
        
        for tag in tags:
            if ":" in tag:
                ns, tag_name = tag.split(":", 1)
                if tag_map:
                    ns_info = tag_map.get(ns)
                    if ns_info:
                        ns_cn = ns_info.get("name", ns)
                        tag_cn = ns_info.get("data", {}).get(tag_name, tag_name)
                    else:
                        ns_cn = ns
                        tag_cn = tag_name
                else:
                    # tag_map 未初始化时使用原始值
                    ns_cn = ns
                    tag_cn = tag_name
            else:
                ns_cn = "其他"
                tag_cn = tag
            
            if ns_cn not in tags_by_type:
                tags_by_type[ns_cn] = []
            tags_by_type[ns_cn].append(tag_cn)
        
        # 构建 Markdown 内容
        content = f"# {title}\n\n"
        
        # 添加封面
        thumb = gallery.get("thumb", "")
        if thumb:
            thumb = thumb.replace("s.exhentai.org", "ehgt.org")
            content += f"<img src='{thumb}'/>\n\n"
            
        if title_jpn:
            content += f"**日文标题**: {title_jpn}\n\n"
        
        content += f"""## 基本信息

- **类型**: {category}
- **上传者**: {uploader}
- **发布时间**: {posted_time}
- **页数**: {filecount}
- **大小**: {size_str}
- **评分**: {rating}

## 画廊链接

- [ExHentai](https://exhentai.org/g/{gid}/{token}/)
- [E-Hentai](https://e-hentai.org/g/{gid}/{token}/)

## 标签

"""
        for ns_cn, tag_list in tags_by_type.items():
            content += f"**{ns_cn}**: {', '.join(tag_list)}\n\n"
            
        # 添加预览图
        if previews:
            content += "## 预览\n\n"
            for p in previews:
                content += f"<img src='{p}'/> "
            content += "\n\n"
        
        content += "\n---\n\n*由 Archive@Home Bot 生成*\n"
        
        # 使用 telepress 发布
        telegraph_url = await asyncio.to_thread(
            publish_text,
            content,
            title=title[:256],
            token=cfg.get("telegraph_token")
        )
        
        return telegraph_url, None
        
    except ValidationError as e:
        return None, f"验证错误: {str(e)}"
    except TelePressError as e:
        return None, f"发布错误: {str(e)}"
    except Exception as e:
        logger.error(f"Telegraph 发布失败: {e}")
        return None, f"发生错误: {str(e)}"


async def telegraph_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 Telegraph 按钮回调"""
    query = update.callback_query
    data = query.data.split("|")
    
    if len(data) < 3:
        await query.answer("数据格式错误", show_alert=False)
        return
    
    _, gid, token = data[0], data[1], data[2]
    
    await query.answer("正在推送到 Telegraph，请稍候...", show_alert=False)
    
    telegraph_url, error = await publish_to_telegraph(gid, token)
    
    if telegraph_url:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📖 查看 Telegraph 页面", url=telegraph_url)]
        ])
        await update.effective_message.reply_text(
            f"✅ 已成功推送到 Telegraph！\n\n📖 链接：{telegraph_url}",
            reply_markup=keyboard,
            disable_web_page_preview=False,
        )
        logger.info(f"画廊 {gid}/{token} 推送到 Telegraph 成功: {telegraph_url}")
    else:
        await update.effective_message.reply_text(f"❌ 推送到 Telegraph 失败\n错误信息：{error}")
        logger.error(f"画廊 {gid}/{token} 推送到 Telegraph 失败: {error}")


def register(app):
    app.add_handler(CallbackQueryHandler(telegraph_callback, pattern=r"^telegraph"))
