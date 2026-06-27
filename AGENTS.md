# Repository Guidelines

## 交流语言

本仓库协作默认使用中文。提交说明、PR 描述、代码评审意见和代理回复应优先使用简体中文；代码标识符、命令、环境变量和第三方 API 名称保持英文原文。

## 项目结构与模块组织

当前仓库以 `SPEC.md` 作为产品与技术规格的唯一事实来源。新增代码前先阅读该文件，并保持其中定义的架构约束：Python 3.11+、FastAPI webhook 服务、异步 worker、Postgres、Redis、SQLAlchemy、Alembic 和 pytest。

建议后续按以下结构落地：

- `src/llm_rpg/`：应用包，按 API、worker、LLM、游戏状态、持久化、Telegram 路由拆分。
- `tests/`：测试目录，结构尽量镜像 `src/llm_rpg/`。
- `alembic/`：数据库迁移。
- `docker-compose.yml`：本地 API、worker、Postgres、Redis 编排。
- `assets/` 或 `prompts/`：预设、提示词和非敏感静态内容。

## 构建、测试与开发命令

常用工程命令：

- `uv sync`：安装并同步本地依赖。
- `docker compose up --build`：启动本地完整服务。
- `uv run pytest`：运行全部测试。
- `uv run pytest tests/path/test_file.py -q`：运行单个测试文件。
- `uv run alembic upgrade head`：应用数据库迁移。
- `uv run ruff check .`、`uv run ruff format --check .` 与 `uv run ruff format .`：检查和格式化 Python 代码。
- `uv run python -m llm_rpg.scripts.run_worldbuild "<seed>"`：手动运行世界创建 vertical slice。
- `uv run python -m llm_rpg.scripts.set_bot_commands`：注册 Telegram bot 命令菜单。
- `uv run python -m llm_rpg.scripts.set_telegram_webhook`：设置真实 Telegram webhook。
- `uv run python -m llm_rpg.scripts.delete_telegram_webhook`：切换到 polling 前删除 Telegram webhook。
- `docker compose --profile e2e run --rm e2e`：在 Compose 容器中运行 FakeTelegram/FakeProvider E2E。

新增命令时，同步更新本文件和 README。

## 编码风格与命名约定

优先编写带类型标注、async-first 的 Python 代码。使用 4 空格缩进；函数、模块使用 `snake_case`；Pydantic 与 ORM 模型使用 `PascalCase`，例如 `WorldBible`、`TurnOutput`、`NpcMemory`。webhook 入口保持轻量，游戏状态变更和 LLM 调用应放在 worker 侧服务中。密钥只能从环境变量读取。

## 测试规范

使用 pytest 编写单元测试和集成测试。测试文件命名为 `test_*.py`，测试函数命名为 `test_<behavior>()`。重点覆盖 `SPEC.md` 中列出的行为：锁定世界不可变、NPC 持久化、状态 reducer 边界、callback 字节长度、update 幂等、LLM 修复回退，以及从 `/new` 到三轮行动的 Docker Compose E2E 流程。

## 提交与 Pull Request 规范

仓库目前没有提交历史；在形成项目惯例前，使用 Conventional Commits，例如 `feat: add world builder schema`、`test: cover update idempotency`。

所有修改应以 Pull Request 形式提交，PR 标题不加任何前缀。PR 应包含变更摘要、关联 issue 或任务、测试结果、迁移说明，以及 Telegram 交互相关的 transcript 片段。凡涉及环境变量、数据模型、提示词或 LLM provider 假设的变化，都必须明确说明。

## Agent 专用说明

代理修改代码前必须先阅读 `SPEC.md`。LLM 校验失败后不得写入 durable game state；世界确认后不得改写 locked world bible；不要提交密钥、token、`.DS_Store` 或其他本机临时文件。
