import re

from loguru import logger
from telegram import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes, MessageHandler, filters

from config.config import cfg
from db.db import User
from utils.GP_action import deduct_GP, get_current_GP
from utils.resolve import get_download_url, get_gallery_info


async def reply_gallery_info(
    update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, gid: str, token: str
):
    msg = await update.effective_message.reply_text("🔍 正在解析画廊信息...")
    logger.info(f"解析画廊 {url}")

    try:
        text, has_spoiler, thumb, require_GP, timeout = await get_gallery_info(gid, token)
    except Exception as e:
        await msg.edit_text("❌ 画廊解析失败，请检查链接或稍后再试")
        logger.error(f"画廊 {url} 解析失败：{e}")
        return

    keyboard = [
        [InlineKeyboardButton("🌐 跳转画廊", url=url)],
    ]
    if update.effective_chat.type == "private":
        has_spoiler = False
        keyboard.append(
            [
                InlineKeyboardButton(
                    "📦 原图归档下载",
                    callback_data=f"download|{gid}|{token}|org|{require_GP['org']}|{timeout}",
                ),
                InlineKeyboardButton(
                    "📦 重采样归档下载",
                    callback_data=f"download|{gid}|{token}|res|{require_GP['res']}|{timeout}",
                ),
            ]
        )
        if cfg["AD"]["text"] and cfg["AD"]["url"]:
            keyboard.append(
                [InlineKeyboardButton(cfg["AD"]["text"], url=cfg["AD"]["url"])]
            )
    else:
        keyboard[0].append(
            InlineKeyboardButton(
                "🤖 在 Bot 中打开",
                url=f"https://t.me/{context.application.bot.username}?start={gid}_{token}",
            )
        )

    await msg.delete()
    await update.effective_message.reply_photo(
        photo=thumb,
        caption=text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        has_spoiler=has_spoiler,
        parse_mode="HTML",
    )


async def resolve_gallery(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.effective_message.text
    url, gid, token = re.search(
        r"https://e[-x]hentai\.org/g/(\d+)/([0-9a-f]{10})", text
    ).group(0, 1, 2)
    await reply_gallery_info(update, context, url, gid, token)


async def download(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await User.get_or_none(id=update.effective_user.id).prefetch_related(
        "GP_records"
    )

    if not user:
        await update.effective_message.reply_text("📌 请先使用 /start 注册")
        return

    if user.group == "黑名单":
        await update.effective_message.reply_text("🚫 您已被封禁")
        return

    _, gid, token, image_quality, require_GP, timeout = query.data.split("|")

    current_GP = get_current_GP(user)
    if current_GP < int(require_GP):
        await update.effective_message.reply_text(f"⚠️ GP 不足，当前余额：{current_GP}")
        return

    caption = re.sub(
        r"\n\n❌ 下载链接获取失败，请稍后再试$",
        "",
        update.effective_message.caption,
    )

    await update.effective_message.edit_caption(
        caption=f"{caption}\n\n⏳ 正在获取下载链接，请稍等...",
        reply_markup=update.effective_message.reply_markup,
        parse_mode="HTML",
    )
    logger.info(f"获取 https://e-hentai.org/g/{gid}/{token}/ 下载链接")

    d_url = await get_download_url(user, gid, token, image_quality, int(require_GP), timeout)
    if d_url:
        await deduct_GP(user, int(require_GP))
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🌐 跳转画廊", url=f"https://e-hentai.org/g/{gid}/{token}/"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔗 复制下载链接", copy_text=CopyTextButton(d_url)
                    ),
                    InlineKeyboardButton("📥 跳转下载", url=d_url),
                ],
            ]
        )

        await update.effective_message.edit_caption(
            caption=f"<blockquote expandable>{caption}</blockquote>\n\n✅ 下载链接获取成功",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    elif d_url == None:
        await update.effective_message.edit_caption(
            caption=f"{caption}\n\n❌ 暂无可用服务器",
            reply_markup=update.effective_message.reply_markup,
            parse_mode="HTML",
        )
        logger.error(f"https://e-hentai.org/g/{gid}/{token}/ 下载链接获取失败")
    else:
        await update.effective_message.edit_caption(
            caption=f"{caption}\n\n❌ 获取下载链接失败",
            reply_markup=update.effective_message.reply_markup,
            parse_mode="HTML",
        )
        logger.error(f"https://e-hentai.org/g/{gid}/{token}/ 下载链接获取失败")


def register(app):
    app.add_handler(
        MessageHandler(
            filters.Regex(r"https://e[-x]hentai\.org/g/\d+/[0-9a-f]{10}"),
            resolve_gallery,
        )
    )
    app.add_handler(CallbackQueryHandler(download, pattern=r"^download"))
