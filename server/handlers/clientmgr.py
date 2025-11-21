import re

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

from db.db import Client, User
from utils.client import add_client, refresh_client_status
from utils.statistics import get_client_statistics, get_usage_statistics


async def clientmgr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = await User.get_or_none(id=user_id).prefetch_related(
        "clients__archive_histories"
    )

    if not user or user.group != "节点提供者":
        await update.effective_message.reply_text(
            "您没有权限执行此命令，请向管理员申请成为节点提供者"
        )
        return

    keyboard, text = await clientmgr_text(user)

    keyboard = (
        keyboard
        if update.effective_chat.type == "private"
        else [
            [
                InlineKeyboardButton(
                    "🛠 管理节点", callback_data=f"clientmgr_private|{user_id}"
                )
            ]
        ]
    )

    await update.effective_message.reply_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
    )


async def clientmgr_text(user):
    clients = user.clients
    keyboard = [[InlineKeyboardButton("➕ 添加节点", callback_data="add_client")]]

    if clients:
        stats_text = await get_client_statistics(clients)
        usage_text = await get_usage_statistics(clients=clients)
        text = f"{stats_text}{usage_text}"
        keyboard.append(
            [InlineKeyboardButton("🛠 管理节点", callback_data="manage_client")]
        )
    else:
        text = "您当前没有节点，请先添加一个节点"
    return keyboard, text


async def clientmgr_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.data.split("|")[1]
    if user_id != str(update.effective_user.id):
        await query.answer("是你的东西吗？你就点！")
        return
    await query.answer()
    user = await User.get(id=user_id).prefetch_related("clients__archive_histories")
    keyboard, text = await clientmgr_text(user)
    await query.delete_message()
    await update.effective_user.send_message(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
    )


async def handle_add_client(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.delete_message()
    await update.effective_user.send_message("请输入要添加的节点 URL\n/cancel 取消操作")
    return 0


async def get_url_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.effective_message.text.strip()
    if not re.match(r"^https?://[^\s/$.?#].[^\s]*$", url):
        await update.effective_message.reply_text(
            "❌ 请输入合法的 URL\n/cancel 取消操作"
        )
        return 0

    success, status, enable_GP_cost = await add_client(
        update.effective_message.from_user.id, url
    )
    if success:
        text = (
            f"✅ 添加成功\n"
            f"🌐 URL：{url}\n"
            f"📡 状态：{status}\n"
            f"💸 允许 GP 消耗：{'是 ✅' if enable_GP_cost else '否 ❌'}"
        )
        logger.info(f"{update.effective_message.from_user.name} 添加节点 {url}")
    else:
        text = f"❌ 添加失败\n原因：{status}"

    await update.effective_message.reply_text(text)
    return ConversationHandler.END


async def client_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    user = await User.get(id=user_id).prefetch_related("clients")
    clients = user.clients

    if not clients:
        keyboard = [[InlineKeyboardButton("➕ 添加节点", callback_data="add_client")]]
        await query.edit_message_text(
            "您还没有添加任何节点", reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    text_lines = ["📝 节点列表："]
    keyboard = []

    for idx, client in enumerate(clients, start=1):
        text_lines.append(
            f"🔹 节点 {idx}:\n    🌐 URL：{client.url}\n    📡 状态：{client.status}"
        )
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"管理 节点 {idx}", callback_data=f"client|{client.id}"
                )
            ]
        )

    text = "\n".join(text_lines)
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def client_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    client_id = query.data.split("|")[1]
    client = await Client.get(id=client_id).prefetch_related("archive_histories")
    usage_text = await get_usage_statistics(clients=[client])

    text = (
        f"📄 节点信息：\n"
        f"🌐 URL：{client.url}\n"
        f"📡 状态：{client.status}\n"
        f"站点: {client.EX}， 免费配额: {'充足' if client.Free == "1" else '不足'}\n"
        f"Ⓖ GP: {client.GP}， Ⓒ Credits: {client.Credits}\n"
        f"💸 允许 GP 消耗：{'是 ✅' if client.enable_GP_cost else '否 ❌'}\n\n"
        f"{usage_text}"
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "🔄 刷新状态 / 启用", callback_data=f"edit_client|{client_id}|refresh"
            ),
            InlineKeyboardButton(
                "⏸️ 停用节点", callback_data=f"edit_client|{client_id}|suspend"
            ),
        ],
        [
            InlineKeyboardButton("⌨ 编辑 URL", callback_data=f"edit_url|{client_id}"),
            InlineKeyboardButton(
                "🗑 删除节点", callback_data=f"edit_client|{client_id}|delete"
            ),
        ],
    ]

    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
    )


async def edit_client(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, client_id, action = query.data.split("|")
    client = await Client.get(id=client_id)

    if action == "refresh":
        await refresh_client_status(client)
        text = (
            f"🔄 已刷新节点状态\n"
            f"状态: {client.status}"
            f"站点: {client.EX}， 免费配额: {'充足' if client.Free == 1 else '不足'}\n"
            f"Ⓖ GP: {client.GP}， Ⓒ Credits: {client.Credits}\n"
            f"💸 允许 GP 消耗：{'是 ✅' if client.enable_GP_cost else '否 ❌'}\n\n"
        )
        logger.info(f"{update.effective_user.name} 刷新/启用节点 {client.url}")
    elif action == "suspend":
        client.status = "停用"
        await client.save()
        text = "⏸️ 节点已停用"
        logger.info(f"{update.effective_user.name} 停用节点 {client.url}")
    elif action == "delete":
        await client.delete()
        text = "🗑 节点已删除"
        logger.info(f"{update.effective_user.name} 删除节点 {client.url}")

    keyboard = [[InlineKeyboardButton("⬅ 返回", callback_data="manage_client")]]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_edit_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.delete_message()

    client_id = query.data.split("|")[1]
    context.user_data["client_id"] = client_id

    await update.effective_user.send_message("请输入新的节点 URL\n/cancel 取消操作")
    return 0


async def get_new_url_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.effective_message.text.strip()
    if not re.match(r"^https?://[^\s/$.?#].[^\s]*$", url):
        await update.effective_message.reply_text(
            "❌ 请输入合法的 URL\n/cancel 取消操作"
        )
        return 0

    client_id = context.user_data.get("client_id")
    client = await Client.get(id=client_id)
    client.url = url
    await client.save()

    await refresh_client_status(client)
    text = (
        f"✅ 编辑成功\n"
        f"🌐 URL：{url}\n"
        f"📡 状态：{client.status}\n"
        f"💸 允许 GP 消耗：{'是 ✅' if client.enable_GP_cost else '否 ❌'}"
    )
    logger.info(f"{update.effective_user.name} 编辑节点 URL {url}")

    keyboard = [[InlineKeyboardButton("⬅ 返回", callback_data="manage_client")]]

    await update.effective_message.reply_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("❎ 操作已取消")
    return ConversationHandler.END


def register(app):
    app.add_handler(CommandHandler("clientmgr", clientmgr))

    add_client_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_add_client, pattern=r"^add_client$")],
        states={0: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_url_input)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    edit_url_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_edit_url, pattern=r"^edit_url")],
        states={
            0: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_new_url_input)]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(add_client_handler)
    app.add_handler(edit_url_handler)
    app.add_handler(
        CallbackQueryHandler(
            edit_client, pattern=r"^edit_client\|\d+\|(?:refresh|suspend|delete)$"
        )
    )
    app.add_handler(CallbackQueryHandler(client_info, pattern=r"^client\|\d+$"))
    app.add_handler(CallbackQueryHandler(client_list, pattern=r"^manage_client$"))
    app.add_handler(
        CallbackQueryHandler(clientmgr_private, pattern=r"^clientmgr_private")
    )
