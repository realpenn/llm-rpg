# 实施计划 (Implementation Plan)

本文件是 `SPEC.md` 落地的分阶段执行计划。`SPEC.md` 是唯一事实来源;本计划只规定**做的顺序、每阶段的产物与完成标准**,不重复协议细节。按阶段择机实施即可,每个阶段尽量独立可交付、可测。

## 如何使用本文档

- 每个 Phase 含:**目标 / 产出 / 完成标准(Done when)/ 覆盖的 Test Standard / 依赖**。
- "完成标准"尽量是可执行命令或可观察结果,便于验收。
- 标识符、命令、表名、环境变量保持英文;其余中文。
- 实施中若发现与 `SPEC.md` 冲突,先改 SPEC 再改代码,不要在代码里"就地发明"。

## 已锁定的技术决定

- 包管理/运行:**uv**(`pyproject.toml` + `uv.lock`)。
- Provider:Phase 5 起接入**真实 OpenAI-compatible provider**,并实跑一条 world-creation 真实路径;测试一律用 `FakeProvider`,绝不打真实模型。
- 运行形态:`api`(FastAPI webhook)+ `worker`(异步处理)+ `postgres` + `redis`,Docker Compose 编排。
- 代码风格:async-first、类型标注;`ruff check` + `ruff format` 作为门禁;密钥只从环境变量读。

## 里程碑与依赖

```
Milestone A  完整地基 + 真实世界创建路径
  P0 bootstrap → P1 schemas → P2 persistence → P3 provider → P4 reducers → P5 world-creation 真实路径

Milestone B  Telegram 闭环 + 回合管线
  P6 ingress/worker plumbing → P7 turn pipeline + context/memory → P8 moderation

Milestone C  健壮性 + 完整命令 + 上线
  P9 recovery/reconciler + 保留清理 → P10 commands/archive/admin → P11 全链 E2E + Compose 收尾 → P12 真实 Telegram 上线与部署
```

依赖关系:P1 是几乎所有后续阶段的契约前提;P2/P3/P4 可在 P1 后并行;P5 汇聚 P1–P4;P7 依赖 P3/P4/P6;P9 依赖 P6;P11 依赖全部;P12 依赖 P6 的真实 transport 与 P7/P8/P10 的可玩闭环。Telegram transport 自 P6 起即按真实 `api.telegram.org` 实现,`FakeTelegramServer` 仅为测试替身;provider 自 P3 起即按真实 OpenAI-compatible 实现,P5 做真实 provider 实跑验收,`FakeProvider` 仅为测试替身。

---

## Milestone A — 完整地基 + 真实世界创建路径

### P0 — 工程引导 (bootstrap)

- **目标**:可运行的空骨架与工具链。
- **产出**:
  - `pyproject.toml`(PEP 621 + uv)、`uv.lock`;依赖:`fastapi`、`uvicorn`、`aiogram`、`sqlalchemy[asyncio]`、`asyncpg`、`alembic`、`redis`、`pydantic`、`pydantic-settings`、`httpx`(或 `openai`)、`pytest`、`pytest-asyncio`、`ruff`。
  - 目录骨架(见末尾"目标目录结构"),各包 `__init__.py` 占位。
  - `src/llm_rpg/config.py`:`Settings`(pydantic-settings)读取全部 env;`src/llm_rpg/logging.py`:结构化日志。
  - `.env.example`(全部 env vars)、`.gitignore`(含 `.env`、`__pycache__`、`.DS_Store`、`*.pyc`)、`README.md`(quickstart)。
  - `docker-compose.yml`(api/worker/postgres/redis)、`Dockerfile`(python:3.11-slim + uv)。
  - `ruff` 配置、`pytest` 配置。
- **完成标准**:`uv sync` 成功;`uv run ruff check .` 通过;`uv run pytest`(空)通过;`docker compose up -d postgres redis` 起得来。
- **Test Standard**:—(基础设施)。
- **依赖**:无。

### P1 — 领域契约 (Pydantic schemas + enums)

- **目标**:把 SPEC 的数据契约固化为类型,作为全栈共享契约。
- **产出**(`src/llm_rpg/schemas/`):
  - `enums.py`:`UpdateStatus`、`DropReason`、`EdgeType`、`DeltaOp`、`LlmPurpose`、`ModeUsed`、`LlmOutcome`、`ModerationStage`、`ModerationAction`、`MemoryScope`、`TimeAdvance`。
  - `player.py`:`PlayerState`(vitals/currency/conditions/flags/inventory + cap)、`InventoryItem`。
  - `world.py`:`WorldBible`(含 player stat schema/bounds、factions、taboos 等)、`WorldBuildOutput`。
  - `turn.py`:`TurnOutput`(narration/state_delta/npc_updates/relationship_updates/events/suggested_actions/memory_update/time_advance)、`StateDeltaEntry`(path/op/value)、`MemoryUpdateEntry`(scope)。
  - `moderation.py`:worker 侧 `SafetyFlagRecord`(stage/flag/action/rewrites)。
  - 注意:`safety_flags` **不在** LLM `TurnOutput` 内(见 SPEC「Turn Output」)。
- **完成标准**:`uv run pytest tests/unit/test_schemas.py` 通过,覆盖合法/非法样例与边界。
- **Test Standard**:World generation schema validation(schema 部分)。
- **依赖**:P0。

### P2 — 持久化 (SQLAlchemy models + Alembic)

- **目标**:durable 表与迁移就位。
- **产出**:
  - `src/llm_rpg/db.py`:async engine/session。
  - `src/llm_rpg/models/`:`players`、`games`、`factions`、`npcs`、`relationships`、`turns`、`events`、`suggested_actions`、`llm_calls`、`telegram_updates`(字段对齐 SPEC「Data Model」,JSONB 承载 WorldBible/PlayerState/rolling summary/memory_log/reply_payload/delta_audit 等)。
  - 索引:`events`(location/entity/recency)、`telegram_updates`(`update_id` 唯一、`status`、`lease_expires_at`、`next_retry_at`)、`suggested_actions`(`game_id,turn_id`)、`turns`(`game_id,sequence`)。
  - `alembic/` + 初始迁移 `0001_initial`。
- **完成标准**:`uv run alembic upgrade head` 在本地 Postgres 建表成功;模型 round-trip 测试通过;`alembic downgrade base` 干净回滚。
- **Test Standard**:为后续 persistence 类测试提供基座。
- **依赖**:P0(P1 并行,JSONB 列引用 P1 schema)。

### P3 — LLM provider 层

- **目标**:真实 + 假两套 provider,统一接口。
- **产出**(`src/llm_rpg/llm/`):
  - `base.py`:`Provider` 协议(`generate_structured(messages, schema, purpose) -> parsed`)。
  - `provider.py`:OpenAI-compatible 实现;`LLM_STRUCTURED_MODE` 的 `strict`/`json_object`/`auto`;本地 Pydantic 校验先于任何 reducer;一次 repair 重试;硬超时;每次调用写 `llm_calls`(purpose/mode_used/outcome/token/latency)。
  - `fake.py`:`FakeProvider`,确定性、可编排返回,供全部测试使用。
  - `prompts.py`:world-build 与 turn 的 prompt 模板骨架。
- **完成标准**:`uv run pytest tests/unit/test_provider.py` 通过,覆盖 auto 降级、repair 成功/失败、超时→可恢复结果。
- **Test Standard**:Provider fallback and repair behavior;deterministic fake LLM provider。
- **依赖**:P1。

### P4 — Reducers + State Delta Protocol

- **目标**:把模型输出安全地落到 `PlayerState`。
- **产出**(`src/llm_rpg/game/reducers.py`):
  - typed delta 应用:`set`/`add`/`remove`;数值 clamp 记 `adjusted`;conditions/flags/inventory 超 cap 记 `dropped`(绝不隐式淘汰);未知 path/未知 tag/类型错/locked-world/越界 `PlayerState` 一律 `dropped`。
  - 产出 `turns.delta_audit`(accepted/adjusted/dropped)与 `llm_calls.delta_dropped` 摘要。
  - locked world bible 只读强制。
- **完成标准**:`uv run pytest tests/unit/test_reducers.py` 通过,逐条覆盖 reducer 规则与审计。
- **Test Standard**:State reducer bounds;State Delta Protocol accepted/adjusted/dropped auditing。
- **依赖**:P1(运行期与 P2 协作写 audit)。

### P5 — 真实路径:世界创建 (vertical slice)

- **目标**:用**真实 provider** 跑通 `/new` 的核心:seed → `WorldBuildOutput` → 落库,并完成真实 provider 实跑验收。
- **产出**(`src/llm_rpg/game/worldbuilder.py` + 可执行入口):
  - worldbuilder service:调用 provider 生成 `WorldBuildOutput`;本地校验;落库为 `games`(locked WorldBible + 初始 `PlayerState` 快照 + rolling summary 起点 + `turn_number=0`)、`factions`(物化)、`npcs`(初始)、opening `turns`(sequence `0`)、`initial_suggested_actions`(挂 opening turn)。
  - 入口:`uv run python -m llm_rpg.scripts.run_worldbuild "<seed>"`,打印 opening narration + suggested actions。
- **真实 provider 实跑(手动验收)**:
  - 由你提供 provider 凭据,填入 `.env`:`LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL`/`LLM_STRUCTURED_MODE`。
  - 对本地 Postgres(`docker compose up -d postgres`)实跑上面的入口脚本一次。
  - 逐项确认:结构化输出本地 Pydantic 校验通过;`LLM_STRUCTURED_MODE=auto` 在该 endpoint 的 strict 探测/降级行为符合预期;非法 JSON 时一次 repair 生效;`llm_calls` 写入 purpose/mode_used/outcome/token/latency。
- **完成标准**:脚本对真实 provider 实跑成功,DB 中可见一局游戏 + opening turn(sequence `0`)+ initial suggested actions;`uv run pytest`(用 `FakeProvider` 的 worldbuild 测试)通过。
- **Test Standard**:World generation schema validation;Locked-world behavior(初始锁定);真实 provider 实跑为**手动验收**(类比 P12 真实 Telegram)。
- **依赖**:P1–P4;需要 provider 凭据。

---

## Milestone B — Telegram 闭环 + 回合管线

### P6 — Ingress + worker 管道(尚不含完整回合 LLM)

- **目标**:打通 webhook→队列→worker→出站,以及 update 生命周期与并发控制。
- **产出**:
  - `src/llm_rpg/api/main.py`:webhook ingress(secret 校验、按 `update_id` 落 inbox 行、仅新建行入队、重试幂等)、health/readiness。
  - `src/llm_rpg/worker/`:`lock.py`(per-user Redis 锁 + TTL lease + 心跳)、`lifecycle.py`(状态机 + `lease_owner/lease_token` CAS + `drop_reason` + `blocked_by_update_id`)、`main.py`(pop→取该用户最小 pending turn-producing→锁→CAS 提交→出站)、`reconciler.py`(占位,P9 实现)。
  - 非 turn-producing 路径:只读命令绕过锁/排序/限流,读已提交快照作答。
  - `src/llm_rpg/telegram/`:`router.py`(命令/回调路由)、`sender.py`(**真实出站**:`sendMessage`/`answerCallbackQuery`/typing action 直连 `api.telegram.org`,`aiogram` 实现);transport 由 `TELEGRAM_MODE` 选 `webhook`(默认)或 `polling`(开发免公网);`FakeTelegramServer` 仅作测试替身经配置注入。真实 bot 上线联调见 P12。
  - `tests/e2e/conftest.py`:`FakeTelegramServer` fixture。
- **完成标准**:集成测试(FakeTelegram + FakeProvider)覆盖 `/new` 起步与 in-flight 丢弃;`uv run pytest tests/e2e` 起步用例通过。(可选)用真实 bot token 做一次最小 live smoke:`/help`、`/new` 起步可达。
- **Test Standard**:Idempotent update handling;In-flight rejection(含 `blocked_by_update_id`);Telegram message and callback routing;Per-user ordering;Rate-limit drops。
- **依赖**:P2、P5。

### P7 — 回合管线 + context assembly + memory

- **目标**:玩家自由行动的核心闭环。
- **产出**:
  - `src/llm_rpg/game/context.py`:预算受限的 prompt 组装(world essentials / rolling summary / recent window / recalled NPCs / recalled events / player state / `game_clock`);召回查询。
  - `src/llm_rpg/game/turn.py`:provider 调用 → (P8 的 output moderation) → reducers → 落 `turns`/`npcs`/`relationships`/`events`/`suggested_actions` → scoped `memory_update` 写入 → 出站回复。
  - memory:append-only;`world`/`npc:<key>`/`faction:<key>` 分 store;独立 cap;单 store 超限用专门 LLM 调用 re-summarize;失败保留旧 store。
  - `game_clock`:`turn_number` 递增、`time_advance` 仅更新 `games` 上结构化 time-of-day 字段(渲染时单独拼,不写进 summary 文本)。
  - NPC reveal:仅由 `npc_updates.revealed_to_player` 显式置位。
- **完成标准**:`/new` → 三回合(FakeProvider)集成测试通过;context 在状态增长时不超预算的测试通过。
- **Test Standard**:Context assembly within token budget;Scoped memory + re-summarization 失败保留;NPC persistence and memory updates;NPC reveal;Game clock / `time_advance`;Relationship `edge_type` + faction membership via `npcs.faction`。
- **依赖**:P3、P4、P6。

### P8 — Moderation pipeline

- **目标**:输入/输出双段审核与 `safety_flags` 记录。
- **产出**:
  - input moderation 先行:`refuse` 短路(refusal 入 `reply_payload`、不调 turn LLM、不改状态、写终态 `completed`、消费 `update_id`);`warn` 追加 input-stage flag 后继续。
  - output moderation 在 reducers 前跑原始 `TurnOutput`:`soften` 重提示一次→仍 flag 则降级 `refuse`;`refuse` 丢弃全部提案变更、出 in-world refusal、保持 `completed`;`warn` 保留并追加 warning。
  - worker 侧 `safety_flags` 记录;moderation 失败策略(input 失败放行+warn,output 失败按 refuse)。
  - model-backed 审核写 `llm_calls`(`purpose=moderation`);soften 重写用 `purpose=soften_rewrite`。
- **完成标准**:`uv run pytest tests/unit/test_moderation.py` + 相关集成用例通过。
- **Test Standard**:Moderation input warn/refuse、output soften/refuse/warn、失败策略、refuse 丢弃全部提案。
- **依赖**:P7。

---

## Milestone C — 健壮性 + 完整命令 + E2E

### P9 — Recovery / reconciler + 保留清理

- **目标**:崩溃安全与数据保留。
- **产出**:
  - `reconciler`:补入队孤儿 `pending`;reclaim 过期 `processing`(写新 lease + CAS,旧 worker 迟到提交失败被丢弃);重发 `completed`/`failed` 缺 `telegram_message_ids`;`dropped` 不重发。
  - 重试 backoff:同一 worker sleep 到 `next_retry_at`,心跳维持 lease;超过 cap 写终态 `failed` + 可恢复消息。
  - sweepers:`suggested_actions` 保留最近 K 回合后硬删;归档时删除该局 suggested_actions/llm_calls;`llm_calls` 按局保留上限。
- **完成标准**:恢复类测试通过,含三个崩溃窗口与 CAS 防陈旧提交、retry backoff、sweeper。
- **Test Standard**:Reconciler 三条;Reclaim + CAS;Re-send/`dropped` 不重发;Suggested action retention sweeper + archive deletion;Processing retry backoff。
- **依赖**:P6。

### P10 — Commands / archive / admin / 可观测

- **目标**:完整 bot 命令与归档语义。
- **产出**:
  - 全部命令:`/start`、`/new`、`/worlds`、`/world`、`/people`、`/status`、`/inventory`、`/reset`(有局先确认)、`/archive`、`/help`、`/admin_stats`。
  - archive 语义:归档只读视图(无 callback、无 suggested actions)、单 active 局、归档不可续、硬删仅 admin。
  - admin allowlist 门禁;结构化日志;health/metrics;`/worlds` 预设来自 `prompts/` 或 `assets/`。
- **完成标准**:命令与归档测试通过,含归档视图与对归档局 callback 的拒绝。
- **Test Standard**:Archive read-only views + 拒绝归档局 callback;Suggested action callback length;Stale callback rejection。
- **依赖**:P6、P7。

### P11 — 全链 E2E + Docker Compose 收尾

- **目标**:可演示、可复现的完整闭环。
- **产出**:
  - Docker Compose E2E:`/new` → 三回合,使用 `FakeTelegramServer` + `FakeProvider`;worker 走 fake telegram 而非 `api.telegram.org`。
  - CI(ruff/format/pytest)与 `README`/`AGENTS.md` 命令补全;`docker compose up --build` 文档化。
- **完成标准**:`docker compose` E2E 绿;`uv run pytest` 全绿;`uv run ruff check .` 与 `uv run ruff format --check .` 通过。
- **Test Standard**:Docker Compose E2E path from `/new` to three turns;`FakeTelegramServer` fixture。
- **依赖**:全部。

### P12 — 真实 Telegram bot 上线与部署

- **目标**:把闭环切到真实 Telegram,完成上线联调与部署。
- **产出**:
  - 经 BotFather 申请 bot,`TELEGRAM_BOT_TOKEN` 入 `.env`;`setMyCommands` 注册命令菜单。
  - 上线脚本/入口:`setWebhook` 指向公网地址并带 `TELEGRAM_WEBHOOK_SECRET`(即 `X-Telegram-Bot-Api-Secret-Token`),ingress 校验该头;开发期用隧道(cloudflared/ngrok),生产用部署域名;或切 `TELEGRAM_MODE=polling` 走 long-polling 免公网。
  - 切到真实 transport(P6 已实现),逐项确认:typing action、`sendMessage`、`answerCallbackQuery`、64-byte callback、多消息分条与 `telegram_message_ids` 记录。
  - 部署:容器化运行 api+worker,健康检查、结构化日志、密钥注入,基本回滚说明。
- **完成标准**:真实手机 Telegram 实跑 `/new`→世界创建→至少三回合→`/status`/`/people`/`/inventory`/`/reset` 正常;webhook secret 校验生效;Telegram 重试投递幂等、无重复回复。
- **Test Standard**:真实联调为**手动验收**(自动化仍由 `FakeTelegramServer` 覆盖,见 P6/P11),类比 P5 的真实 provider 实跑。
- **依赖**:P6(真实 transport + ingress)、P7/P8(可玩回合)、P10(完整命令/归档)。

---

## Test Standard → Phase 覆盖映射

| SPEC Test Standard 条目 | Phase |
|---|---|
| World generation schema validation | P1, P5 |
| Locked-world behavior | P4, P5 |
| NPC persistence and memory updates | P7 |
| State reducer bounds | P4 |
| State Delta Protocol 审计 | P4 |
| Suggested action callback length | P10 |
| Telegram message and callback routing | P6 |
| Idempotent update handling | P6 |
| Provider fallback and repair behavior | P3 |
| Docker Compose E2E `/new`→3 turns | P11 |
| Deterministic fake LLM provider | P3(全程使用) |
| FakeTelegramServer fixture | P6, P11 |
| In-flight rejection + `blocked_by_update_id` | P6 |
| Stale callback rejection | P10 |
| Context assembly within token budget | P7 |
| Scoped memory + re-summarization 失败保留 | P7 |
| Moderation pipeline 各情形 | P8 |
| Suggested action retention + archive deletion | P9, P10 |
| Archive views + 拒绝归档 callback | P10 |
| Opening turn `0` + initial_suggested_actions | P5 |
| NPC reveal via `revealed_to_player` | P7 |
| Game clock + `time_advance` | P7 |
| Relationship `edge_type` + faction membership | P7 |
| Rate-limit drops (`drop_reason=rate_limited`) | P6 |
| Processing retry backoff + heartbeat lease | P9 |
| Reconciler / reclaim / CAS / re-send | P9 |
| Per-user `update_id` ordering | P6 |

---

## 跨阶段约定

- **Definition of Done(每阶段)**:该阶段测试绿 + `ruff check`/`format` 通过 + 涉及 env/数据模型/prompt/provider 假设的改动在 PR 描述写明(见 `AGENTS.md`)。
- **测试纪律**:任何阶段的自动化测试都用 `FakeProvider` 与 `FakeTelegramServer`,不打真实模型/真实 Telegram;两处真实验收手动进行:**P5 真实 provider 实跑**(world-creation)与 **P12 真实 Telegram 实跑**(完整对局)。
- **环境变量(已知)**:`LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`、`LLM_STRUCTURED_MODE`、`LLM_CONTEXT_TOKEN_BUDGET`、`DATABASE_URL`、`REDIS_URL`、`TELEGRAM_BOT_TOKEN`、`TELEGRAM_WEBHOOK_SECRET`、`TELEGRAM_MODE`、`TELEGRAM_WEBHOOK_URL`、`ADMIN_USER_IDS`;以及随阶段补充的 `LEASE_TTL_SECONDS`、`RATE_LIMIT_TURNS_PER_MINUTE`、`SUGGESTED_ACTIONS_RETAIN_TURNS`、memory cap 等。新增即更新 `.env.example`。
- **密钥**:只进 `.env`(git 忽略),绝不提交。

## 目标目录结构

```
llm-rpg/
  pyproject.toml  uv.lock  .env.example  .gitignore  README.md
  docker-compose.yml  Dockerfile  alembic.ini
  alembic/{env.py, versions/}
  src/llm_rpg/
    config.py  logging.py  db.py  redis.py
    schemas/{enums,player,world,turn,moderation}.py
    models/{...durable tables...}.py
    llm/{base,provider,fake,prompts}.py
    game/{worldbuilder,reducers,context,turn,memory}.py
    telegram/{router,sender}.py
    worker/{main,lock,lifecycle,reconciler}.py
    api/main.py
    scripts/run_worldbuild.py
  prompts/            # 预设、提示词等非敏感静态内容
  tests/
    unit/  e2e/  conftest.py
```

## 暂不在范围内 / 待定

- 多语言 UI(命令/报错文案)本地化范围:默认中文,随 `WorldBible.language` 走的范围待 P10 时确认。
- 伪流式叙事(消息编辑)与每回合单消息合并的取舍:作为 P7 之后的优化项。
- 指标/dashboard 细化(Prometheus 等):P11 视需要再定。
