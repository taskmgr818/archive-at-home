from telegram import BotCommand

from . import clientmgr, inline_query, resolver, statistics, user_action, usermgr

BOT_COMMANDS = [
    BotCommand("clientmgr", "节点管理"),
    BotCommand("statistics", "统计信息"),
    BotCommand("checkin", "签到"),
    BotCommand("myinfo", "我的信息"),
    BotCommand("usermgr", "用户管理"),
    BotCommand("help", "帮助"),
]


def register_all_handlers(app):
    usermgr.register(app)
    clientmgr.register(app)
    user_action.register(app)
    resolver.register(app)
    statistics.register(app)
    inline_query.register(app)
