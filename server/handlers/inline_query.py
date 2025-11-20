import re
import uuid

from loguru import logger
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InlineQueryResultPhoto,
    InlineQueryResultsButton,
    InputTextMessageContent,
    Update,
)
from telegram.ext import CallbackQueryHandler, ContextTypes, InlineQueryHandler
from tortoise.functions import Count

from db.db import User
from utils.GP_action import checkin
from utils.resolve import get_gallery_info


async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query.strip()

    button = InlineQueryResultsButton(text="到Bot查看更多信息", start_parameter="start")

    # 没输入时提示
    if not query:
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "签到", callback_data=f"checkin|{update.effective_user.id}"
                    )
                ]
            ]
        )
        results = [
            InlineQueryResultArticle(
                id=str(uuid.uuid4()),
                title="请输入 eh/ex 链接以获取预览",
                input_message_content=InputTextMessageContent("请输入链接"),
            ),
            InlineQueryResultArticle(
                id=str(uuid.uuid4()),
                title="我的信息（签到）",
                input_message_content=InputTextMessageContent("点击按钮进行签到"),
                description="签到并查看自己的信息",
                reply_markup=keyboard,
            ),
        ]

        await update.inline_query.answer(results, button=button, cache_time=0)
        return

    # 正则匹配合法链接（严格格式）
    pattern = r"^https://e[-x]hentai\.org/g/(\d+)/([0-9a-f]{10})/?$"
    match = re.match(pattern, query)
    if not match:
        results = [
            InlineQueryResultArticle(
                id=str(uuid.uuid4()),
                title="链接格式错误",
                input_message_content=InputTextMessageContent("请输入合法链接"),
            )
        ]
        await update.inline_query.answer(results)
        return

    gid, token = match.groups()

    logger.info(f"解析画廊 {query}")
    try:
        text, _, thumb, _, _ = await get_gallery_info(gid, token)
    except:
        results = [
            InlineQueryResultArticle(
                id=str(uuid.uuid4()),
                title="获取画廊信息失败",
                input_message_content=InputTextMessageContent("请检查链接或稍后再试"),
            )
        ]
        await update.inline_query.answer(results, cache_time=0)
        return

    # 按钮
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🌐 跳转画廊", url=query),
                InlineKeyboardButton(
                    "🤖 在 Bot 中打开",
                    url=f"https://t.me/{context.application.bot.username}?start={gid}_{token}",
                ),
            ],
        ]
    )

    results = [
        InlineQueryResultPhoto(
            id=str(uuid.uuid4()),
            photo_url=thumb,
            thumbnail_url=thumb,
            title="画廊预览",
            caption=text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    ]

    await update.inline_query.answer(results)


async def handle_checkin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    user_id = update.effective_user.id
    if user_id != int(query.data.split("|")[1]):
        await query.answer("是你的东西吗？你就点！")
        return
    await query.answer()

    user = (
        await User.annotate(history_count=Count("archive_histories"))
        .prefetch_related("GP_records")
        .get_or_none(id=user_id)
    )
    if not user:
        keyboard = [
            [
                InlineKeyboardButton(
                    "🤖 打开 Bot",
                    url=f"https://t.me/{context.application.bot.username}?start",
                )
            ]
        ]

        await query.edit_message_text(
            "请先注册", reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    amount, balance = await checkin(user)

    text = (
        f"✅ 签到成功！获得 {amount} GP！\n"
        if amount
        else "📌 你今天已经签过到了~\n"
        f"💰 当前余额：{balance} GP\n"
        f"📊 使用次数：{user.history_count} 次"
    )
    await query.edit_message_text(text)


def register(app):
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(CallbackQueryHandler(handle_checkin, pattern=r"^checkin"))
