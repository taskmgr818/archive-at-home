from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from config.config import cfg
from utils.statistics import (
    get_archive_history_file,
    get_client_statistics,
    get_usage_statistics,
    get_user_list_file,
)


async def statistics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_str, abnormal_str = await get_client_statistics()

    if update.effective_chat.id in cfg["allowed_group"] or (
        update.effective_user.id in cfg["admin"]
        and update.effective_chat.type == "private"
    ):
        text = f"{await get_usage_statistics()}{status_str}{abnormal_str}"
        keyboard = [
            [InlineKeyboardButton("获取用户列表", callback_data="user_list_file")],
            [
                InlineKeyboardButton(
                    "获取解析记录", callback_data="archive_history_file"
                )
            ],
        ]
        await update.effective_message.reply_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
        )
    else:
        await update.effective_message.reply_text(status_str, parse_mode="HTML")


async def user_list_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    file = await get_user_list_file()
    await update.effective_message.reply_document(file, filename="user_list.xlsx")


async def archive_history_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    file = await get_archive_history_file()
    await update.effective_message.reply_document(file, filename="archive_history.xlsx")


def register(app):
    app.add_handler(CommandHandler("statistics", statistics))
    app.add_handler(CallbackQueryHandler(user_list_file, pattern=r"^user_list_file$"))
    app.add_handler(
        CallbackQueryHandler(archive_history_file, pattern=r"^archive_history_file$")
    )
