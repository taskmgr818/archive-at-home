import datetime

from loguru import logger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
from tortoise.functions import Count

from config.config import cfg
from db.db import GPRecord, User
from utils.GP_action import get_current_GP


async def get_user_by_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message.reply_to_message:
        user = (
            await User.annotate(history_count=Count("archive_histories"))
            .prefetch_related("GP_records")
            .get_or_none(id=message.reply_to_message.from_user.id)
        )
        if user:
            markup, text = usermgr_text(user)
            await context.bot.send_message(
                message.from_user.id, text, reply_markup=markup
            )
        else:
            await message.reply_text("未找到对应的用户信息")
    else:
        await message.reply_text("请使用此命令回复一条消息")


async def start_usermgr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """进入用户管理菜单"""
    if update.effective_user.id not in cfg["admin"]:
        await update.effective_message.reply_text("您没有权限执行此命令")
        return ConversationHandler.END

    await update.effective_message.reply_text(
        "请发送目标用户的 ID，或直接转发一条该用户的消息\n/cancel 取消"
    )
    return 0


async def handle_user_id_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """接收用户 ID 或转发的消息并展示操作选项"""
    message = update.effective_message
    queryset = User.annotate(history_count=Count("archive_histories")).prefetch_related(
        "GP_records"
    )
    user = None
    if message.forward_origin:
        forward_origin = message.forward_origin
        if hasattr(forward_origin, "sender_user"):
            user = await queryset.get_or_none(id=forward_origin.sender_user.id)
        elif hasattr(forward_origin, "sender_user_name"):
            user = await queryset.get_or_none(name=forward_origin.sender_user_name)
    else:
        try:
            user_id = int(message.text)
            user = await queryset.get_or_none(id=user_id)
        except ValueError:
            await message.reply_text("无效的用户 ID，请重新输入\n/cancel 取消")
            return 0

    if not user:
        await message.reply_text("未找到对应的用户信息")
        return ConversationHandler.END

    markup, text = usermgr_text(user)
    await message.reply_text(text, reply_markup=markup)

    return ConversationHandler.END


def usermgr_text(user):
    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("切换用户组", callback_data=f"set_group|{user.id}")],
            [
                InlineKeyboardButton("添加 GP", callback_data=f"add_GP|{user.id}"),
                InlineKeyboardButton("清空 GP", callback_data=f"reset_GP|{user.id}"),
            ],
        ]
    )

    remaining_GP = get_current_GP(user)

    text = (
        f"管理用户：{user.name}\n"
        f"用户组：{user.group}\n"
        f"历史使用次数：{user.history_count}\n"
        f"剩余 GP：{remaining_GP}"
    )
    return markup, text


async def prompt_add_GP(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """提示输入 GP 数量"""
    query = update.callback_query
    await query.answer()

    user_id = query.data.split("|")[1]
    context.user_data["user_id"] = user_id

    await query.delete_message()
    await update.effective_user.send_message("请输入要添加的 GP 数量\n/cancel 取消")
    return 0


async def handle_GP_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """接收并处理 GP 数量输入"""
    try:
        amount = int(update.effective_message.text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text("请输入大于 0 的整数\n/cancel 取消")
        return 0

    user_id = context.user_data.get("user_id")
    user = await User.get(id=user_id).prefetch_related("GP_records")
    original_balance = get_current_GP(user)

    await GPRecord.create(
        user=user,
        amount=amount,
        source="管理员发放",
        expire_time=datetime.datetime.max,
    )

    await update.effective_message.reply_text(
        f"为用户 {user.name} 添加了 {amount} GP\n当前剩余：{original_balance + amount} GP"
    )
    logger.info(
        f"管理员 {update.effective_user.name} 为用户 {user.name} 添加 {amount} GP"
    )
    return ConversationHandler.END


async def handle_reset_GP(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.data.split("|")[1]
    context.user_data["user_id"] = user_id

    user = await User.get(id=user_id)
    await user.GP_records.all().delete()

    await query.edit_message_text(f"用户 {user.name} GP 已清空")
    logger.info(f"管理员 {update.effective_user.name} 清空用户 {user.name} GP")


async def cancel_operation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """取消当前操作"""
    await update.effective_message.reply_text("❎ 操作已取消")
    return ConversationHandler.END


async def show_group_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """展示用户组切换选项"""
    query = update.callback_query
    await query.answer()

    user_id = query.data.split("|")[1]
    context.user_data["user_id"] = user_id

    keyboard = [
        [InlineKeyboardButton("普通用户", callback_data=f"group|{user_id}|普通用户")],
        [
            InlineKeyboardButton(
                "节点提供者", callback_data=f"group|{user_id}|节点提供者"
            )
        ],
        [InlineKeyboardButton("黑名单", callback_data=f"group|{user_id}|黑名单")],
    ]

    await query.edit_message_text(
        "请选择要设置的用户组：", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_group_change(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理用户组切换"""
    query = update.callback_query
    await query.answer()

    _, user_id, group = query.data.split("|")
    user = await User.get(id=user_id)
    user.group = group
    await user.save()

    await query.edit_message_text(f"用户 {user.name} 已切换至【{group}】用户组")
    logger.info(
        f"管理员 {update.effective_user.name} 切换用户 {user.name} 至【{group}】用户组"
    )


def register(app):
    """注册 handler 到 bot"""
    usermgr_handler = ConversationHandler(
        entry_points=[
            CommandHandler("usermgr", start_usermgr, filters.ChatType.PRIVATE)
        ],
        states={
            0: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_user_id_input)]
        },
        fallbacks=[CommandHandler("cancel", cancel_operation)],
    )

    add_GP_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(prompt_add_GP, pattern=r"^add_GP\|\d+$")],
        states={0: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_GP_input)]},
        fallbacks=[CommandHandler("cancel", cancel_operation)],
    )

    app.add_handler(usermgr_handler)
    app.add_handler(add_GP_handler)
    app.add_handler(
        CallbackQueryHandler(show_group_options, pattern=r"^set_group\|\d+$")
    )
    app.add_handler(
        CallbackQueryHandler(handle_group_change, pattern=r"^group\|\d+\|.+$")
    )
    app.add_handler(
        CommandHandler("usermgr", get_user_by_reply, filters.ChatType.GROUPS)
    )
    app.add_handler(CallbackQueryHandler(handle_reset_GP, pattern=r"^reset_GP\|\d+$"))
