# Telegram LLM Text RPG Specification

## Product Goal

Build a production-ready Telegram Bot text RPG where players can type any action in natural language or tap LLM-generated action buttons. The default experience is Chinese, with a focus on open-ended narrative play, persistent NPCs, and world simulation through text.

The game is inspired by AI text RPG design patterns such as:

- Text as the primary interface, not a fallback for missing graphics.
- A computable world state beneath the prose.
- Open-ended player actions adjudicated by an LLM.
- NPCs with goals, memory, relationships, and faction loyalties.
- Dynamic consequences that survive across turns.

## Core Experience

### New Game Flow

1. The player sends `/new`.
2. The bot offers built-in presets and a custom world option.
3. For a custom world, the player writes one paragraph as a world seed.
4. The LLM expands that seed into a structured `WorldBible`.
5. The player confirms the world preview.
6. After confirmation, the world bible is locked and the first scene begins.

### Worldbuilding Rules

- Players control key direction through the seed text and preset selection.
- The LLM builds the detailed world: laws, factions, locations, dangers, roles, NPC seeds, and narrative tone.
- Once confirmed, the world bible is immutable.
- Later turns may reveal new details, locations, NPCs, and consequences.
- Later turns must not rewrite locked world laws, the core conflict, or content boundaries.

### Persistent NPCs

NPCs are persistent entities, not disposable dialogue text.

Each NPC stores:

- Key, name, title, role, faction, and location.
- Personality, desire, fear, secret, goal, and status.
- Attitude and trust toward the player.
- A memory log that accumulates important interactions.
- Relationship edges to the player, other NPCs, and factions.

NPCs can:

- Investigate, track, betray, trade, negotiate, spread rumors, request help, misread evidence, form alliances, and remember the player.
- Serve as companions who can be assigned tasks such as scouting, negotiation, medicine, guard duty, or delaying enemies.

## Technical Architecture

### Stack

- Python 3.11+
- FastAPI for webhook ingress and health endpoints
- aiogram for Telegram API types and message helpers
- Postgres for durable game state
- Redis for update queue, locks, and temporary worldbuilding state
- SQLAlchemy 2.x async ORM
- Alembic migrations
- OpenAI-compatible Chat Completions provider
- pytest for unit and integration tests
- Docker Compose for local production-like execution

### Runtime Services

- `api`: FastAPI service that receives Telegram webhooks, verifies the secret, and quickly enqueues updates.
- `worker`: async worker that pops Telegram updates, serializes work per Telegram user, calls the LLM, writes state, and sends Telegram replies.
- `postgres`: durable data store.
- `redis`: queue, locks, and temporary state.

### Bot Commands

- `/start`: welcome and short onboarding.
- `/new`: start world creation.
- `/worlds`: list presets and custom world option.
- `/world`: show the current locked world summary.
- `/people`: show known persistent NPCs.
- `/status`: show current player state.
- `/inventory`: show inventory.
- `/reset`: archive current game and start over.
- `/help`: show commands.
- `/admin_stats`: admin-only operational stats.

## LLM Contract

### Provider

The app uses an OpenAI-compatible Chat Completions client with:

- `LLM_BASE_URL`
- `LLM_API_KEY`
- `LLM_MODEL`
- `LLM_STRUCTURED_MODE`

The provider prefers JSON schema structured output. If the upstream model does not support strict schemas, it falls back to JSON object mode and Pydantic validation with one repair retry.

### WorldBuilder Output

`WorldBuildOutput` includes:

- `world`: a locked `WorldBible`
- `opening_narration`
- `player_state`
- `initial_suggested_actions`

`WorldBible` includes:

- Summary, language, genre, tone, era/geography
- Locked laws
- Factions (materialized into the `factions` table on confirmation)
- Player stat schema: the `vitals` and `currency` keys with their min/max bounds and the allowed `conditions` tags, used by reducers to clamp `state_delta`
- Initial location
- Initial NPCs
- Dangers
- Available professions or identities
- Narrative style
- Taboos
- Core conflict

### Turn Input and Context Assembly

The worker assembles each turn's prompt from durable state under a fixed token budget. The full history is never resent verbatim; long games stay in-window through compaction and selective recall.

Each turn prompt contains:

- A system prompt with the locked world bible essentials: summary, language, tone, locked laws, taboos, and core conflict. Bulky or rarely-relevant world detail is omitted unless recalled.
- A rolling game summary: a compact, periodically-refreshed digest of prior events. Older turns are folded into this summary instead of being sent in full.
- The recent turn window: the last N turns of narration and player actions, verbatim.
- Recalled NPCs: only NPCs relevant to the current turn (present at the location, named or addressed by the player, or recently active), each with attitude, trust, goal, status, and a bounded slice of memory log.
- Recalled events: durable events relevant to the current scene, selected by location, involved entities, and recency.
- The current player state and the player's input action.

Budget and compaction rules:

- A configurable token budget (`LLM_CONTEXT_TOKEN_BUDGET`) caps total prompt size. Sections are filled in priority order: world essentials, player state and input, recent turn window, recalled NPCs, recalled events, rolling summary.
- When state exceeds the budget, the oldest or least-relevant detail is dropped first and folded into the rolling summary.
- `memory_update` from each turn output appends to the rolling summary and, when scoped to an NPC, to that NPC's memory log.
- NPC memory logs and the rolling summary are themselves bounded; when they grow past their cap they are re-summarized.

### Turn Output

Each game turn returns:

- `narration`: prose shown to the player
- `state_delta`: bounded player state changes
- `npc_updates`: persistent NPC changes
- `relationship_updates`: relationship edge changes
- `events`: durable event log entries
- `suggested_actions`: 3-5 suggested next actions
- `memory_update`: compact memory summary addition
- `safety_flags`: optional moderation or safety notes

## Game State Schemas

### PlayerState

`PlayerState` is the bounded, reducer-managed view of the player. It is persisted as the current snapshot on the active `games` row and carried in each turn prompt. Fields:

- `name`, and `profession` or identity chosen from the world bible's available roles
- `location`: current location key
- `vitals`: bounded numeric gauges (for example `hp`, `energy`, `morale`), each with a min/max fixed at world creation
- `currency`: bounded numeric balances
- `conditions`: a bounded set of status tags (for example `wounded`, `hunted`)
- `flags`: a small key/value map for narrative state the world relies on
- `inventory`: a bounded list of `InventoryItem`

### InventoryItem

- `key`, `name`, `description`
- `quantity`: non-negative integer
- `tags`: optional classification (for example `weapon`, `quest`)

Inventory has no separate table; it lives inside `PlayerState` and is mutated only through `state_delta`. The `/inventory` command renders inventory from the active game's player state.

NPC fields are defined canonically in Persistent NPCs and are persisted in the `npcs` table.

## State Rules

- Webhook ingress must not call the LLM directly. It verifies the secret and records the update in a durable inbox row keyed by `update_id`; a newly-created row is enqueued, and a retry of an existing row follows the idempotency rules below rather than creating a duplicate.
- The worker processes at most one turn per Telegram user at a time via a per-user Redis lock. The lock carries a TTL lease renewed by a heartbeat task independent of the LLM await path, and every LLM/provider call has a hard timeout. This same lease is mirrored onto the `processing` row as `lease_owner`, `lease_token`, and `lease_expires_at`; terminal writes use compare-and-set on those fields, so a stale worker whose lease was reclaimed cannot commit late output.
- On acquiring a user's lock, the worker handles that user's lowest pending turn-producing `update_id` — not necessarily the update it popped — so a user's actions are processed in Telegram order even across multiple workers.
- A worker never blocks on a held lock. Turn-producing updates are not buffered or stacked: when update N is claimed for processing, the same transaction marks any other pending turn-producing updates for that user with a higher `update_id` as `dropped` with `blocked_by_update_id=N`; any turn-producing update that arrives while a user's row is already `processing` is inserted directly as `dropped` with the same field. The user gets a brief in-game "still acting" notice. A re-enqueued duplicate of the update already in flight (same `update_id`) is recognized by its status and skipped as a no-op, never re-dropped or re-notified.
- Telegram `update_id` values are idempotency keys stored in `telegram_updates`. The inbox row, not the Redis queue, is the source of truth. A webhook retry never creates a duplicate row: for a `pending` or `processing` update it ensures the update is enqueued (re-enqueue is idempotent — the worker dedupes by status and the per-user lock) and returns; for a terminal update with a `reply_payload`, it re-sends the stored reply and never triggers a second LLM call. The time-sensitive `dropped` notice is re-sent only by such near-immediate webhook retries, never by the later reconciler (see Update Lifecycle).
- The worker commits game mutations and the outbound reply payload in one transaction (`processing` → `completed`) only if its `lease_owner` and `lease_token` still match the row. It then sends the reply to Telegram and records the delivered `telegram_message_ids`. Sending is always driven from the stored reply payload, never by re-running the LLM, so any retry — in-process, from webhook retry, or by the reconciler — reuses it.
- Callback data must stay within Telegram's 64-byte limit; it carries an opaque id referencing a `suggested_actions` row scoped to its game and turn. Callbacks from an earlier turn or game are rejected with a gentle notice.
- LLM failures must not mutate durable game state.
- Reducers clamp numeric values to their world-defined bounds and reject or ignore invalid deltas.
- The locked world bible is read-only after confirmation. No turn-output field can mutate it, and reducers drop any delta targeting locked world content.

## Data Model

Durable tables:

- `players`
- `games`: per-player game holding the locked `WorldBible`, the current `PlayerState` snapshot, the rolling game summary, and active/archived status (at most one active per player).
- `factions`: faction entities materialized from the locked `WorldBible` at confirmation, keyed and scoped to the game. Holds each faction's static identity (key, name, description, ideology); identity is read-only after confirmation, while dynamic standing lives in `relationships`. Referenced by `npcs.faction` and by relationship edges via faction key.
- `npcs`
- `relationships`: edges among the player, NPCs, and factions, referenced by key.
- `turns`
- `events`: durable event log entries produced by turns, scoped to the game and indexed by location, involved entities, and recency so context assembly can recall the relevant ones.
- `suggested_actions`
- `llm_calls`
- `telegram_updates`: durable inbox/outbox keyed by `update_id`, with `telegram_user_id`, `telegram_chat_id`, update kind, turn-producing flag, `status` (`pending`, `processing`, `completed`, `dropped`, `failed`), `blocked_by_update_id` for in-flight drops, `lease_owner`, `lease_token`, `lease_expires_at`, raw update payload, optional `game_id`, optional `turn_id`, `retry_count`, error text, an ordered `reply_payload` (one or more outbound messages), the delivered `telegram_message_ids`, and timestamps.

Transient Redis keys:

- `llm_rpg:updates`: update queue
- `llm_rpg:lock:user:{telegram_user_id}`: per-player lock
- `llm_rpg:tg_state:{telegram_user_id}`: worldbuilding flow state
- `llm_rpg:pending_world:{telegram_user_id}`: generated world awaiting confirmation

## Update Lifecycle and Recovery

Each `telegram_updates` row moves through a status machine:

- `pending`: durably recorded at ingress and queued for processing, or awaiting (re-)enqueue by the reconciler if the original enqueue was lost. Not yet picked up.
- `processing`: claimed by a worker that holds the user's lock. The same lock lease bounds the row through `lease_owner`, `lease_token`, and `lease_expires_at`; heartbeat renewal keeps it alive while the worker is healthy, after which the row can be reclaimed.
- `completed`: game mutations and the normal reply payload are committed. The reply may not have reached Telegram yet; `telegram_message_ids` is filled in as messages are delivered.
- `dropped`: rejected without LLM processing or game mutation — for example a second action that arrived while the user's turn was already in flight (see State Rules). The brief notice to the player is stored in `reply_payload` and delivered through the same outbox path.
- `failed`: the turn could not be produced (provider timeout, or validation failure after the repair retry). Bounded by `retry_count`: the worker retries with backoff up to a cap, then leaves the row `failed` with a recoverable in-game failure message in `reply_payload`. A `failed` row never leaves durable game state mutated.

`completed`, `dropped`, and `failed` are terminal; `pending` and `processing` are not.

A periodic reconciler makes the inbox/outbox crash-safe by scanning durable rows instead of trusting the Redis queue:

- Re-enqueues `pending` rows that are not in flight — covers a crash between the DB insert and the Redis enqueue, and a flushed Redis queue.
- Reclaims `processing` rows whose `lease_expires_at` has expired by re-enqueueing them; the next worker re-acquires the user lock, writes a fresh `lease_owner`/`lease_token`, and re-checks the status before acting. If the old worker later returns from the LLM call, its compare-and-set terminal write fails and its output is discarded — covers a worker that crashed or lost its lease mid-turn.
- Re-sends `completed` and `failed` rows still missing one or more `telegram_message_ids` — covers a crash after commit but before the Telegram send, which Telegram itself will not retry because ingress already returned 200. `dropped` notices are time-sensitive ("still acting") and are sent best-effort once, never re-sent, so a stale notice never surprises the player later.

A turn may emit more than one outbound message. The `reply_payload` stores them in order, and recovery sends the tail not yet present in `telegram_message_ids`. This is best-effort rather than perfect send idempotency: if Telegram accepts a message but the service crashes before recording its `telegram_message_id`, the reconciler may send a visible duplicate. Each outbound message should carry a stable update or turn marker so duplicate deliveries are recognizable and operationally reconcilable.

## Production Behavior

- The webhook endpoint returns quickly after enqueueing updates.
- The worker sends Telegram typing actions during LLM calls.
- Per-player Redis locks prevent concurrent state corruption.
- Invalid model JSON triggers a repair attempt.
- Provider timeouts and validation failures return a recoverable in-game failure message.
- Secrets are read from environment variables only.
- Logs are structured enough to trace update, game, and LLM call failures.
- Health endpoints expose readiness and basic metrics.
- Per-user rate limiting caps turn frequency to control cost and abuse. A player has at most one active game at a time; `/new` while a game is active prompts before archiving.
- Player input and generated narration both pass moderation. A raised safety flag drives a defined response (soften, refuse, or warn), not an advisory note only.
- Redis holds the queue, locks, and worldbuilding flow state, and is treated as ephemeral. Transient keys carry TTLs, and loss of in-progress worldbuilding state is recoverable by restarting `/new`. Durable game state never lives only in Redis.
- Admin commands are gated by an allowlist of Telegram user ids from configuration.

## Test Standard

Required coverage:

- World generation schema validation
- Locked-world behavior
- NPC persistence and memory updates
- State reducer bounds
- Suggested action callback length
- Telegram message and callback routing
- Idempotent update handling
- Provider fallback and repair behavior
- Docker Compose E2E path from `/new` to three turns
- A deterministic fake LLM provider used across all tests, including the E2E path, so tests never call a live model
- In-flight rejection when a user sends an action during an active turn, including `blocked_by_update_id` on rows dropped because another turn is processing
- Stale callback rejection across turns and games
- Context assembly staying within the token budget as game state grows
- Reconciler re-enqueues orphaned `pending` rows after a lost enqueue or flushed queue
- Reclaim of an expired `processing` lease without double-processing a slow-but-alive worker, including `lease_owner`/`lease_token` compare-and-set preventing stale commits
- Re-send of `completed`/`failed` rows missing `telegram_message_ids`, and `dropped` notices not re-sent by the reconciler
- Per-user updates processed in `update_id` order across workers
