# Archive-at-Home Server

Archive-at-Home 分布式归档链接解析系统的中控服务器。

## 功能

- HTTP API 接口（用户请求归档链接解析）
- WebSocket Hub（Node 通信）
- Redis 任务调度（Lua 原子脚本）
- PostgreSQL 数据持久化
- 用户认证与余额系统
- 每日签到系统
- 管理员后台

## 快速开始

### 方式一：Docker Compose（推荐）

1. 编辑 `docker-compose.yml`，配置环境变量（如 `ADMIN_TOKEN`、`NODE_VERIFY_KEY` 等）

2. 启动服务：
```bash
docker-compose up -d
```

### 方式二：二进制部署

1. 从 [Releases](https://github.com/taskmgr818/archive-at-home/releases) 下载 `archive-at-home-server-linux-amd64.tar.gz`

2. 解压并运行：
```bash
tar -xzf archive-at-home-server-linux-amd64.tar.gz

# 设置环境变量
export SERVER_ADDR=:8080
export DB_HOST=localhost
export REDIS_ADDR=localhost:6379
# ... 其他配置

./archive-at-home-server
```

## API 文档

### 认证

所有业务接口使用 Bearer Token 认证：

```
Authorization: Bearer sk-xxxxxxxxxxxx
```

管理员接口使用独立的 Admin Token：

```
Authorization: Bearer <ADMIN_TOKEN>
```

---

## 用户 API

### POST /auth/register

邮箱注册。

**请求体:**
```json
{
  "email": "user@example.com",
  "password": "secret123",
  "nickname": "用户昵称"
}
```

**响应:**
```json
{
  "user": {
    "id": "abc123",
    "email": "user@example.com",
    "nickname": "用户昵称",
    "provider": "email",
    "api_key": "sk-xxxxxxxxxxxx",
    "status": "active",
    "last_checkin_at": "2026-02-10T08:00:00Z",
    "created_at": "2026-02-11T00:00:00Z",
    "updated_at": "2026-02-11T00:00:00Z"
  },
  "api_key": "sk-xxxxxxxxxxxx"
}
```

### POST /auth/login

邮箱登录。

**请求体:**
```json
{
  "email": "user@example.com",
  "password": "secret123"
}
```

**响应:**
```json
{
  "user": {
    "id": "abc123",
    "email": "user@example.com",
    "nickname": "用户昵称",
    "provider": "email",
    "api_key": "sk-xxxxxxxxxxxx",
    "status": "active",
    "last_checkin_at": "2026-02-10T08:00:00Z",
    "created_at": "2026-02-11T00:00:00Z",
    "updated_at": "2026-02-11T00:00:00Z"
  },
  "api_key": "sk-xxxxxxxxxxxx"
}
```

### GET /auth/telegram/login

Telegram 第三方登录中转页面。

**URL 参数:**
- `redirect_url` (可选): 登录成功后跳转地址
- `param_name` (可选): API Key 参数名，默认 `start`

**示例:**
```
https://your-domain.com/auth/telegram/login?redirect_url=https://t.me/YourBot
```

登录成功后跳转到：`https://t.me/YourBot?start=<api-key>`

### POST /auth/telegram/callback

Telegram OAuth 登录回调（内部接口，由前端调用）。

---

### GET /api/v1/me 🔒

获取当前用户信息和余额。

**响应:**
```json
{
  "user": {
    "id": "abc123",
    "email": "user@example.com",
    "nickname": "用户昵称",
    "provider": "email",
    "api_key": "sk-xxxxxxxxxxxx",
    "status": "active",
    "last_checkin_at": "2026-02-10T08:00:00Z",
    "created_at": "2026-02-11T00:00:00Z",
    "updated_at": "2026-02-11T00:00:00Z"
  },
  "balance": 900
}
```

### POST /api/v1/me/reset-key 🔒

重置 API Key（旧 Key 立即失效）。

### GET /api/v1/me/balance 🔒

获取 GP 余额。

**响应:**
```json
{
  "balance": 900
}
```

### POST /api/v1/me/checkin 🔒

每日签到，获取随机 GP 奖励（每天一次）。

**响应:**
```json
{
  "success": true,
  "reward": 120,
  "balance": 1020,
  "message": "签到成功"
}
```

---

### POST /api/v1/parse 🔒

**核心接口** - 请求解析画廊的归档下载链接。

**请求头:** `Authorization: Bearer sk-xxxxxxxxxxxx`

**请求体:**
```json
{
  "gallery_id": "2845710",
  "gallery_key": "a1b2c3d4e5",
  "force": false
}
```

**响应（成功）:**
```json
{
  "cached": false,
  "gp_cost": 180,
  "archive_url": "https://..."
}
```

**响应（从缓存）:**
```json
{
  "cached": true,
  "archive_url": "https://..."
}
```

**响应（失败）:**
```json
{
  "error": "insufficient balance"
}
```

---

## 管理员 API

### GET /api/v1/admin/users/:id 🔑

获取指定用户信息。

**请求头:** `Authorization: Bearer <ADMIN_TOKEN>`

**响应:**
```json
{
  "user": {
    "id": "abc123",
    "email": "user@example.com",
    "nickname": "用户昵称",
    "provider": "email",
    "api_key": "sk-xxxxxxxxxxxx",
    "status": "active",
    "last_checkin_at": "2026-02-10T08:00:00Z",
    "created_at": "2026-02-11T00:00:00Z",
    "updated_at": "2026-02-11T00:00:00Z"
  },
  "balance": 900
}
```

### PUT /api/v1/admin/users/:id/status 🔑

设置用户状态。

**请求头:** `Authorization: Bearer <ADMIN_TOKEN>`

**请求体:**
```json
{
  "status": "active"  // active | banned | suspended
}
```

**响应:**
```json
{
  "success": true,
  "message": "status updated to active"
}
```

### POST /api/v1/admin/users/:id/credits 🔑

为用户充值 GP。

**请求体:**
```json
{
  "amount": 50000,
  "remark": "活动奖励"
}
```

**响应:**
```json
{
  "success": true,
  "balance": 50900,
  "message": "credits added successfully"
}
```

---

## WebSocket 协议

Node 通过 WebSocket 连接到 `/ws`。

### 认证

使用 `X-Auth-Token` header，格式：`NodeID:Signature`（ED25519 签名，Base64 编码）

### Server → Node

| 消息类型 | 说明 | Payload |
|---------|------|---------|
| `TASK_ANNOUNCEMENT` | 任务广播 | `{trace_id, free_tier, estimated_gp, queue_len}` |
| `TASK_ASSIGNED` | 任务分配 | `{trace_id, gallery_id, gallery_key}` |
| `TASK_GONE` | 任务已被抢占 | `{trace_id}` |

### Node → Server

| 消息类型 | 说明 | Payload |
|---------|------|---------|
| `FETCH_TASK` | 抢占任务 | `{trace_id, node_id}` |
| `TASK_RESULT` | 任务结果 | `{trace_id, node_id, success, actual_gp, archive_url, error}` |

**消息格式:**
```json
{
  "type": "TASK_ANNOUNCEMENT",
  "payload": { ... }
}
```

---

## 核心设计

### 无状态广播调度

- Server 发布任务时仅广播轻量信号，不指定执行者
- Node 自主决策是否抢占
- Redis Lua 脚本原子保证互斥

### 私有化缓存

- 缓存 Key: `cache:{UserID}:{GalleryID}`
- TTL: 7 天（可配置）
- 不同用户独立缓存

### 请求合并

- Key: `inflight:{UserID}:{GalleryID}`
- 同一用户短时间内重复请求自动合并

### 租约机制

- Node claim 任务后设置 TTL（默认 2 分钟）
- 超时自动过期，Watchdog 重新入队

### GP 成本追踪

- Server 请求 E-Hentai 获取预估 GP
- 任务创建时冻结余额
- Node 回报实际消耗，结算或退款

---

## 任务状态机

```
   ┌─────────┐     Node FETCH     ┌────────────┐    Result    ┌───────────┐
   │ PENDING  │ ──────────────────▶  │ PROCESSING │ ──────────▶  │ COMPLETED │
   └─────────┘                      └────────────┘              └───────────┘
        │                                 │
        │           Lease Expired         │
        ◀─────────────────────────────────┘
              (Watchdog re-enqueue)
```

---

## Redis Lua 脚本

| 脚本 | 功能 |
|------|------|
| `LuaPublishTask` | 原子创建任务 + 请求合并检查 |
| `LuaFetchTask` | 原子抢占：PENDING → PROCESSING + 租约 |
| `LuaCompleteTask` | 原子完成：写入缓存 + 清理 sentinel |
| `LuaReclaimTask` | 租约过期任务重新入队 |

---

## 环境变量完整列表

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SERVER_ADDR` | `:8080` | HTTP 监听地址 |
| `REDIS_ADDR` | `localhost:6379` | Redis 地址 |
| `REDIS_PASSWORD` | (空) | Redis 密码 |
| `REDIS_DB` | `0` | Redis DB |
| `CACHE_TTL` | `168h` | 缓存有效期 |
| `TASK_LEASE_TTL` | `2m` | 任务租约超时 |
| `TASK_WAIT_TIMEOUT` | `90s` | HTTP 等待超时 |
| `DB_HOST` | `localhost` | PostgreSQL 主机 |
| `DB_PORT` | `5432` | PostgreSQL 端口 |
| `DB_USER` | `postgres` | PostgreSQL 用户 |
| `DB_PASSWORD` | `postgres` | PostgreSQL 密码 |
| `DB_NAME` | `ehentai` | 数据库名 |
| `DB_SSLMODE` | `disable` | SSL 模式 |
| `USE_EXHENTAI` | `false` | 是否使用 ExHentai |
| `EH_COOKIE` | (空) | E-Hentai Cookie |
| `TELEGRAM_BOT_TOKEN` | (空) | Telegram Bot Token |
| `TELEGRAM_BOT_USERNAME` | (空) | Telegram Bot 用户名 |
| `NODE_VERIFY_KEY` | (空) | ED25519 公钥 |
| `ADMIN_TOKEN` | (空) | 管理员 Token |
| `CHECKIN_MIN_GP` | `10000` | 签到最小奖励 |
| `CHECKIN_MAX_GP` | `20000` | 签到最大奖励 |

---
