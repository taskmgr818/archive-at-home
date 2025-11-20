from collections import defaultdict
from datetime import datetime, timedelta, timezone
from io import BytesIO
from zoneinfo import ZoneInfo

from openpyxl import Workbook

from db.db import ArchiveHistory, Client, User
from utils.GP_action import get_current_GP


async def get_client_statistics(clients=None):
    collect_abnormal = clients is None

    clients = clients or await Client.all().select_related("provider")

    status_labels = ["正常", "无免费额度", "网络异常", "解析功能异常", "停用"]
    stats = defaultdict(int)
    abnormal_clients = []

    for client in clients:
        if client.status in status_labels:
            stats[client.status] += 1

        if collect_abnormal and client.status != "正常":
            abnormal_clients.append(client)

    # 构建状态摘要
    status_lines = ["📊 节点状态：", f"<blockquote expandable>    总计：{len(clients)}"]

    status_lines += [
        f"    {label}：{stats[label]}" for label in status_labels if stats[label] > 0
    ]

    status_lines.append("</blockquote>")
    status_str = "\n".join(status_lines)

    # 按需构建异常信息
    if collect_abnormal and abnormal_clients:
        abnormal_str = "🚨 异常节点列表：\n<blockquote expandable>"
        for c in abnormal_clients:
            abnormal_str += (
                f"🔹 ID：{c.id}\n"
                f"    提供者：<a href='tg://user?id={c.provider.id}'>{c.provider.name}</a>\n"
                f"    状态：{c.status}\n\n"
            )
        abnormal_str += "</blockquote>"
    else:
        abnormal_str = ""

    return (status_str, abnormal_str) if collect_abnormal else status_str


async def get_usage_statistics(clients=None, user=None):
    now = datetime.now(tz=timezone.utc)
    past_24h = now - timedelta(hours=24)

    total_count = total_GP = recent_count = recent_GP = 0

    if clients:
        histories = [h for c in clients for h in c.archive_histories]
    elif user:
        histories = user.archive_histories
    else:
        histories = await ArchiveHistory.all()

    for record in histories:
        total_count += 1
        total_GP += record.GP_cost
        if record.time >= past_24h:
            recent_count += 1
            recent_GP += record.GP_cost

    if user:
        return recent_count, recent_GP, total_count, total_GP

    return (
        "📈 使用统计：\n"
        "<blockquote expandable>"
        "    过去 24 小时：\n"
        f"        解析次数：{recent_count}\n"
        f"        消耗 GP：{recent_GP}\n"
        "    总计：\n"
        f"        解析次数：{total_count}\n"
        f"        消耗 GP：{total_GP}"
        "</blockquote>"
    )


async def _create_excel(data: list[list], title_row: list[str]) -> BytesIO:
    wb = Workbook()
    ws = wb.active
    ws.append(title_row)
    for row in data:
        ws.append(row)
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer


async def get_user_list_file():
    users = await User.all().prefetch_related("archive_histories", "GP_records")
    title = [
        "用户 ID",
        "用户名",
        "用户组",
        "当前剩余 GP",
        "近 24 小时解析次数",
        "近 24 小时消耗节点 GP",
        "累计解析次数",
        "累计消耗节点 GP",
    ]

    data = [
        [
            str(user.id),
            user.name,
            user.group,
            get_current_GP(user),
            *(await get_usage_statistics(user=user)),
        ]
        for user in users
    ]

    return await _create_excel(data, title)


async def get_archive_history_file():
    histories = await ArchiveHistory.all().select_related("user", "client__provider")
    title = [
        "解析时间",
        "画廊链接",
        "用户名",
        "用户 ID",
        "消耗节点 GP",
        "节点提供者",
    ]

    data = []
    for history in histories:
        provider_name = (
            history.client.provider.name
            if history.client and history.client.provider
            else "（已删除）"
        )
        row = [
            history.time.astimezone(ZoneInfo("Asia/Shanghai")).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            f"https://e-hentai.org/g/{history.gid}/{history.token}/",
            history.user.name,
            str(history.user_id),
            history.GP_cost,
            provider_name,
        ]
        data.append(row)

    return await _create_excel(data, title)
