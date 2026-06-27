# llm-rpg

Telegram LLM 文字 RPG 后端。当前以 `SPEC.md` 为唯一事实来源，按 GitHub issues 中的 P0-P12 分阶段实现。

## 本地准备

需要 Python 3.11+、uv、Docker 和 Docker Compose。

```bash
uv sync
cp .env.example .env
docker compose up -d postgres redis
```

## 常用命令

```bash
uv run ruff check .
uv run ruff format --check .
uv run ruff format .
uv run pytest
uv run pytest tests/path/test_file.py -q
docker compose up --build
docker compose --profile e2e run --rm e2e
```

后续引入 Alembic 后使用：

```bash
uv run alembic upgrade head
```

## 服务入口

- API: `uv run uvicorn llm_rpg.api.main:app --reload`
- worker: `uv run python -m llm_rpg.worker.main`
- world-build vertical slice: `uv run python -m llm_rpg.scripts.run_worldbuild "<seed>"`
- register Telegram commands: `uv run python -m llm_rpg.scripts.set_bot_commands`
- set Telegram webhook: `uv run python -m llm_rpg.scripts.set_telegram_webhook`
- delete Telegram webhook for polling: `uv run python -m llm_rpg.scripts.delete_telegram_webhook`

当前已具备健康检查、Redis 队列 worker、领域 schema、持久化模型、LLM provider、世界创建 vertical slice、回合管线、moderation、恢复清理和主要 Telegram 命令。

## Telegram 命令

- `/start`、`/help`：查看入口和帮助。
- `/new`、`/worlds`：创建新游戏或查看世界预设；`/new` 需要玩家仍有回合额度。
- `/world`、`/people`、`/status`、`/inventory`：查看当前游戏状态。
- `/quota`：查看剩余回合额度。
- `/recharge <code>`：兑换一次性充值码。
- `/reset`、`/archive`、`/restore [编号]`：归档当前游戏、查看归档或恢复最近/指定存档。
- `/admin_stats`、`/admin_code <10|50|100|unlimited> [数量]`：管理员命令。

## 真实 Telegram 上线

1. 通过 BotFather 创建 bot，把 `TELEGRAM_BOT_TOKEN` 写入本机 `.env`。
2. 设置 `TELEGRAM_WEBHOOK_URL` 为公网 webhook 地址，并设置 `TELEGRAM_WEBHOOK_SECRET`。
3. 运行 `uv run python -m llm_rpg.scripts.set_bot_commands` 注册命令菜单。
4. 运行 `uv run python -m llm_rpg.scripts.set_telegram_webhook` 设置 webhook。
5. 部署或本地启动 `api`、`worker`、Postgres、Redis；回滚时切回上一镜像并重新执行 webhook 指向。

也可以不使用 webhook：把 `.env` 里的 `TELEGRAM_MODE=polling`，运行
`uv run python -m llm_rpg.scripts.delete_telegram_webhook`，然后只启动 `worker`。
Polling 模式不需要公网地址；worker 会通过 Telegram `getUpdates` 拉取消息并复用同一套 inbox/回合处理。
如果 Telegram 长轮询偶发断开，worker 会按 `TELEGRAM_POLLING_RETRY_INITIAL_SECONDS` 到
`TELEGRAM_POLLING_RETRY_MAX_SECONDS` 之间指数退避后继续拉取。
