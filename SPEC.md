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
- Profile-only temperament fields. Dynamic attitude and trust toward the player live in `relationships` as the authoritative `player_npc` edge, not on the NPC row.
- A memory log that accumulates important interactions.
- Relationship edges to the player, other NPCs, and factions.
- `revealed_to_player`: whether the player has encountered this NPC. `/people` lists only NPCs revealed in the active game; unrevealed NPCs may still exist in state and be recalled by the LLM when the scene requires them. The LLM reveals an NPC explicitly with `npc_updates.revealed_to_player = true`; reducers never infer reveal state by parsing narration text.

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
- `/quota`: show the player's remaining paid/free turn quota.
- `/recharge <code>`: redeem a one-time recharge code.
- `/reset`: archive current game and start over; asks for confirmation if a game is active.
- `/archive`: list the player's archived games and read-only summaries.
- `/restore [archive_id]`: restore the latest archived game, or a specific archived game by the short id shown in `/archive`.
- `/help`: show commands.
- `/admin_stats`: admin-only operational stats.
- `/admin_code <10|50|100|unlimited> [count]`: admin-only recharge code generation.

### Archive Semantics

- An archived game's locked `WorldBible`, narration history, NPCs, and events are preserved while archived. Archived games are read-only until restored.
- `/world`, `/people`, `/inventory`, and `/status` operate on the active game only. Archived games are inspected through archived views: read-only summary with a short restore id, no callbacks accepted while archived, and no suggested actions retained.
- A player has at most one active game. `/restore` clears `archived_at` on an archived game only when the player has no active game. It does not call the LLM, does not consume quota, does not rewrite the locked world bible, and does not recreate deleted suggested action buttons; after restore, the player continues by typing a natural-language action.
- Hard-deletion of an archived game is admin-only and gated by the admin allowlist.

## LLM Contract

### Provider

The app uses an OpenAI-compatible Chat Completions client with:

- `LLM_BASE_URL`
- `LLM_API_KEY`
- `LLM_MODEL`
- `LLM_STRUCTURED_MODE`: one of `strict`, `json_object`, or `auto`. `strict` requires JSON schema support and fails if unsupported. `json_object` uses JSON object mode plus Pydantic validation with one repair retry. `auto` probes for strict support and downgrades to `json_object` on provider-feature failure. Default: `auto`.

The provider prefers JSON schema structured output according to `LLM_STRUCTURED_MODE`; validation always happens locally before any reducer applies model output.

### WorldBuilder Output

`WorldBuildOutput` includes:

- `world`: a locked `WorldBible`
- `opening_narration`
- `player_state`
- `initial_suggested_actions`

On confirmation, the opening narration is persisted as an opening turn row with sequence `0`; `initial_suggested_actions` attach to that opening turn through normal `(game_id, turn_id)` scoping.

`WorldBible` includes:

- Summary, language, genre, tone, era/geography
- Locked laws
- Factions (materialized into the `factions` table on confirmation)
- Player stat schema: the `vitals` and `currency` keys with their min/max bounds, allowed `conditions` tags, allowed `flags` keys, and optional caps used by reducers to validate `state_delta`
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
- Recalled NPCs: only NPCs relevant to the current turn (present at the location, named or addressed by the player, or recently active), each with relationship-derived attitude/trust, goal, status, and a bounded slice of memory log.
- Recalled events: durable events relevant to the current scene, selected by location, involved entities, and recency.
- The current player state and the player's input action.
- `game_clock`: a game-scoped `turn_number` plus an optional structured in-world time-of-day field stored on the game. The prompt renders this beside the rolling summary; it is not embedded inside the summary text. The LLM receives `turn_number` for pacing; no game logic depends on wall-clock time.

Budget and compaction rules:

- A configurable token budget (`LLM_CONTEXT_TOKEN_BUDGET`) caps total prompt size. Sections are filled in priority order: world essentials, player state and input, recent turn window, recalled NPCs, recalled events, rolling summary.
- When state exceeds the budget, the oldest or least-relevant detail is dropped first and folded into the rolling summary.
- `memory_update` entries are scoped: `world`, `npc:<key>`, or `faction:<key>`.
- `world`-scoped entries fold into the rolling game summary as a compressed digest. `npc` and `faction` entries append higher-fidelity detail to that entity memory log and a one-line digest to the rolling summary.
- The rolling summary and each entity memory log carry independent entry caps and per-entry character caps. When one store breaches its cap, only that store is re-summarized by a dedicated LLM call; cross-store re-summarization never mixes scopes.
- Re-summarization is the only path that rewrites a memory store; turns only append. If re-summarization fails, the previous memory store remains authoritative.
- `game_clock` field updates such as `turn_number` and time-of-day changes are not memory rewrites; they are bounded game state fields updated by the reducer and rendered into prompts separately from memory stores.

### Turn Output

Each game turn returns:

- `narration`: prose shown to the player
- `state_delta`: bounded player state changes
- `npc_updates`: persistent NPC changes
- `relationship_updates`: relationship edge changes
- `events`: durable event log entries
- `suggested_actions`: 3-5 suggested next actions
- `memory_update`: compact memory summary addition
- `time_advance`: optional hint such as `minutes`, `hours`, or `overnight`; it updates only the structured in-world time-of-day field on the game. It has no automatic vitals decay effect.

The LLM `TurnOutput` does not include final `safety_flags`; those are worker-side records attached to the resulting `turns` row when one exists and always to the `telegram_updates` row after moderation.

### State Delta Protocol

`state_delta` is a list of typed operations, not free-form field assignments. Each delta entry carries:

- `path`: dotted path into `PlayerState`, for example `vitals.hp`, `currency.gold`, `conditions`, `inventory.<key>`, `inventory.<key>.quantity`, or `flags.<key>`.
- `op`: one of `set`, `add`, or `remove`. `clamp` is reducer-local behavior and is never emitted by the LLM.
- `value`: numeric for bounded `set`/`add`, a tag string for `conditions`, a boolean/string/number for declared flags, or a full `InventoryItem` for inventory upsert.

Reducer rules:

- `add` on bounded numeric fields clamps to the world-bible min/max. A clamped value is `adjusted`, not `dropped`.
- `set` on `conditions` is idempotent; `remove` drops a tag if present and is a no-op if absent. Adding a condition beyond the cap is `dropped`; reducers never evict old conditions implicitly.
- `set` on `inventory.<key>` upserts only when `value` is a complete `InventoryItem`. Upserting a new item beyond the inventory cap is `dropped`; reducers never evict old inventory implicitly. `add` on `inventory.<key>.quantity` requires an existing item, floors at `0`, and records the floor as `adjusted`; quantity ops against missing items are `dropped`.
- `flags.<key>` accepts only keys declared in the locked world-bible player stat schema. Unknown flags and flags beyond the declared cap are `dropped`; reducers never evict old flags implicitly.
- Unknown paths, unknown condition tags, wrong value types, locked-world targets, and any attempted mutation outside `PlayerState` are `dropped`.
- Moderation `refuse` happens before reducers and is recorded in `safety_flags`, not in `delta_dropped`.
- Reducers persist `turns.delta_audit` with accepted, adjusted, and dropped entries; `llm_calls.delta_dropped` stores only a compact dropped-delta summary for the corresponding turn call.

### Moderation Pipeline

Both player input and LLM-generated narration pass moderation before mutation. Moderation may be implemented by rules or by a separate moderation model; only model-backed checks create `llm_calls` rows with `purpose=moderation`.

- Input moderation runs first. `refuse` short-circuits with a refusal message in `reply_payload`, emits no turn-purpose LLM call, applies no `state_delta`, writes the update as terminal `completed`, and consumes the `update_id`. `warn` appends an input-stage safety flag and continues into the normal turn.
- Output moderation runs on the raw LLM `TurnOutput` before reducers.
- On `soften`, the worker re-prompts the LLM once for a rewrite with the flagged output discarded. If the second pass still flags, the action downgrades to `refuse`.
- On `refuse`, the worker drops the proposed `state_delta`, `npc_updates`, `relationship_updates`, `events`, `memory_update`, and `suggested_actions`, emits in-world refusal narration, and keeps the row `completed`.
- On `warn`, the worker keeps narration and deltas, then appends a `safety_flags.warning` entry.
- Worker-side `safety_flags` is a list of records; a single update may contain both an input-stage warning and an output-stage action. Each record has `{stage: input|output, flag, action: soften|refuse|warn, rewrites: 0|1}`.
- Moderation failure policy is conservative and stage-specific: input moderation failure allows the turn with a warning safety flag; output moderation failure treats the output as `refuse`.
- A refused or softened update is still ordered under the worker lock and consumes its `update_id`.

## Game State Schemas

### PlayerState

`PlayerState` is the bounded, reducer-managed view of the player. It is persisted as the current snapshot on the active `games` row and carried in each turn prompt. Fields:

- `name`, and `profession` or identity chosen from the world bible's available roles
- `location`: current location key
- `vitals`: bounded numeric gauges (for example `hp`, `energy`, `morale`), each with a min/max fixed at world creation
- `currency`: bounded numeric balances
- `conditions`: a bounded set of status tags (for example `wounded`, `hunted`), default cap `16` unless the world bible overrides it
- `flags`: a small key/value map for narrative state the world relies on, default cap `64` declared keys unless the world bible overrides it
- `inventory`: a bounded list of `InventoryItem`, default cap `64` items unless the world bible overrides it

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
- Per-user rate limiting is enforced at the worker lock boundary after the user lock is acquired and before any LLM turn call. A turn in flight drops competing turn-producing updates with `drop_reason=in_flight`; a configurable sliding-window turns-per-minute cap drops fast-typing floods with `drop_reason=rate_limited`. Both paths use the same `reply_payload` delivery mechanism so dropped notices stay consistent.
- Non-turn-producing updates such as status/account commands (`/status`, `/people`, `/world`, `/inventory`, `/archive`, `/quota`, `/recharge`, and admin commands) and archived-view callbacks are still recorded in `telegram_updates`, but bypass the turn lock, turn ordering, and turn rate limit. They read or update only the latest committed non-gameplay account state and must not call the turn LLM. Game-state commands such as `/reset` and `/restore` are serialized like turn-producing updates even though `/restore` does not call the LLM or consume quota.
- Telegram `update_id` values are idempotency keys stored in `telegram_updates`. The inbox row, not the Redis queue, is the source of truth. A webhook retry never creates a duplicate row: for a `pending` or `processing` update it ensures the update is enqueued (re-enqueue is idempotent — the worker dedupes by status and the per-user lock) and returns; for a terminal update with a `reply_payload`, it re-sends the stored reply and never triggers a second LLM call. The time-sensitive `dropped` notice is re-sent only by such near-immediate webhook retries, never by the later reconciler (see Update Lifecycle).
- The worker commits game mutations and the outbound reply payload in one transaction (`processing` → `completed`) only if its `lease_owner` and `lease_token` still match the row. It then sends the reply to Telegram and records the delivered `telegram_message_ids`. Sending is always driven from the stored reply payload, never by re-running the LLM, so any retry — in-process, from webhook retry, or by the reconciler — reuses it.
- Callback data must stay within Telegram's 64-byte limit; it carries an opaque id referencing a `suggested_actions` row scoped to its game and turn. Callbacks from an earlier turn or game are rejected with a gentle notice.
- LLM failures must not mutate durable game state.
- Reducers apply the State Delta Protocol, clamp numeric values to their world-defined bounds, and audit accepted, adjusted, and dropped deltas.
- The locked world bible is read-only after confirmation. No turn-output field can mutate it, and reducers drop any delta targeting locked world content.
- Player quota is checked before any user-triggered LLM gameplay cost. `/new` requires positive remaining quota or unlimited status before worldbuilding, but does not consume a turn itself. Successful natural-language player actions and valid current suggested-action callbacks consume one turn inside the same database transaction as the resulting game mutation; if the provider fails and the transaction rolls back, the quota decrement rolls back too. Quota increments and decrements must use atomic SQL updates on the `players` row rather than Python read-modify-write, because recharge/account commands are not serialized with turn-producing updates. Read-only/status commands, recharge commands, admin commands, stale callbacks, and refused input moderation do not consume quota.
- Recharge codes are single-use. Redeeming a finite code adds its turn amount to `players.remaining_turns`; redeeming an unlimited code sets `players.has_unlimited_turns`. Admin code generation is gated by the same admin allowlist as other admin commands.

## Data Model

Durable tables:

- `players`: Telegram player account records, including remaining finite turn quota and whether the account has unlimited turns. New players receive 10 free turns.
- `recharge_codes`: one-time recharge codes generated by admins. Codes are either 10-turn, 50-turn, 100-turn, or unlimited packages, and store their creator, redemption player, and redemption timestamp.
- `games`: per-player game holding the locked `WorldBible`, the current `PlayerState` snapshot, the rolling game summary, `turn_number`, optional time-of-day tag, and active/archived status (at most one active per player).
- `factions`: faction entities materialized from the locked `WorldBible` at confirmation, keyed and scoped to the game. Holds each faction's static identity (key, name, description, ideology) plus a dynamic `memory_log`; identity is read-only after confirmation, while dynamic standing lives in `relationships`. Referenced by `npcs.faction` and by relationship edges via faction key.
- `npcs`
- `relationships`: edges among the player, NPCs, and factions, referenced by key, with `edge_type` (`player_npc`, `npc_npc`, `player_faction`, `npc_faction`, `faction_faction`) and numeric standing/trust fields.
- `turns`: ordered game history, including opening turn sequence `0`, player input, narration, `delta_audit`, safety flag list, and the game clock snapshot for that turn.
- `events`: durable event log entries produced by turns, scoped to the game and indexed by location, involved entities, and recency so context assembly can recall the relevant ones.
- `suggested_actions`: rows scoped to `(game_id, turn_id)` with an opaque callback id. Retention is bounded: rows older than the last K turns of the active game are hard-deleted by a periodic sweeper. Missing action ids are treated as stale with a gentle notice. On game archive, all suggested actions for that game are deleted; callbacks referencing an archived game are rejected at game-state lookup. Restoring an archived game does not recreate deleted suggested actions.
- `llm_calls`: scoped to `(game_id, turn_id)` where applicable, with purpose (`world_build`, `turn`, `repair`, `resummarize`, `moderation`, `soften_rewrite`), provider, model, `mode_used` (`strict`, `json_object`, `repair`), request messages or hash, raw response text, parsed payload when valid, token counts, latency, outcome (`ok`, `schema_invalid`, `provider_timeout`, `repair_failed`, `moderation_blocked`), `delta_dropped` summary for turn calls, error text, and timestamp. Retention is bounded per game, and rows are deleted on archive.
- `telegram_updates`: durable inbox/outbox keyed by `update_id`, with `telegram_user_id`, `telegram_chat_id`, update kind, turn-producing flag, `status` (`pending`, `processing`, `completed`, `dropped`, `failed`), `drop_reason` (`in_flight`, `rate_limited`, `quota_exhausted`, `stale_callback`, `archived_game`, `duplicate`, or `other`), `blocked_by_update_id` for in-flight drops, `lease_owner`, `lease_token`, `lease_expires_at`, `next_retry_at`, raw update payload, optional `game_id`, optional `turn_id`, `retry_count`, error text, an ordered `reply_payload` (one or more outbound messages), the delivered `telegram_message_ids`, and timestamps.

Faction membership model: an NPC's current allegiance is stored only on `npcs.faction`. `relationships` edges carry standing toward a faction, never authoritative membership.

- A betrayal turn that switches allegiance writes `npc_updates.faction = <new faction key>`; reducers apply it directly to `npcs.faction`.
- Standing edges toward the prior faction and the new faction are updated in the same transaction via `relationship_updates`.
- Faction identity is read-only after world confirmation; only standing edges and `npcs.faction` move.

Transient Redis keys:

- `llm_rpg:updates`: update queue
- `llm_rpg:lock:user:{telegram_user_id}`: per-player lock
- `llm_rpg:tg_state:{telegram_user_id}`: worldbuilding flow state
- `llm_rpg:pending_world:{telegram_user_id}`: generated world awaiting confirmation

## Update Lifecycle and Recovery

Each `telegram_updates` row moves through a status machine:

- `pending`: durably recorded at ingress and queued for processing, or awaiting (re-)enqueue by the reconciler if the original enqueue was lost. Not yet picked up.
- `processing`: claimed by a worker that holds the user's lock. The same lock lease bounds the row through `lease_owner`, `lease_token`, and `lease_expires_at`; heartbeat renewal keeps it alive while the worker is healthy, including provider backoff between retries, after which the row can be reclaimed.
- `completed`: game mutations and the normal reply payload are committed. The reply may not have reached Telegram yet; `telegram_message_ids` is filled in as messages are delivered.
- `dropped`: rejected without LLM processing or game mutation, with `drop_reason` set for cases such as `in_flight`, `rate_limited`, `stale_callback`, or `archived_game`. The brief notice to the player is stored in `reply_payload` and delivered through the same outbox path.
- `failed`: the turn could not be produced (provider timeout, or validation failure after the repair retry). Bounded by `retry_count`: retries remain `processing`, set `next_retry_at`, and keep the lease alive through heartbeat during backoff. The same worker sleeps until `next_retry_at` rather than using delayed Redis requeue. Once retry cap is exhausted, the worker writes terminal `failed` with a recoverable in-game failure message in `reply_payload`. A `failed` row never leaves durable game state mutated.

`completed`, `dropped`, and `failed` are terminal; `pending` and `processing` are not.

A periodic reconciler makes the inbox/outbox crash-safe by scanning durable rows instead of trusting the Redis queue:

- Re-enqueues `pending` rows that are not in flight — covers a crash between the DB insert and the Redis enqueue, and a flushed Redis queue.
- Reclaims `processing` rows whose `lease_expires_at` has expired by re-enqueueing them; the next worker re-acquires the user lock, writes a fresh `lease_owner`/`lease_token`, and re-checks the status before acting. If the old worker later returns from the LLM call, its compare-and-set terminal write fails and its output is discarded — covers a worker that crashed or lost its lease mid-turn.
- Re-sends terminal `completed` and `failed` rows still missing one or more `telegram_message_ids` — covers a crash after commit but before the Telegram send, which Telegram itself will not retry because ingress already returned 200. `dropped` notices are time-sensitive ("still acting") and are sent best-effort once, never re-sent, so a stale notice never surprises the player later.

A turn may emit more than one outbound message. For `completed` and `failed` rows, `reply_payload` stores messages in order, and recovery sends the tail not yet present in `telegram_message_ids`. This is best-effort rather than perfect send idempotency: if Telegram accepts a message but the service crashes before recording its `telegram_message_id`, the reconciler may send a visible duplicate. Each outbound message should carry a stable update or turn marker so duplicate deliveries are recognizable and operationally reconcilable.

## Production Behavior

- The webhook endpoint returns quickly after enqueueing updates.
- The worker sends Telegram typing actions during LLM calls.
- Per-player Redis locks prevent concurrent state corruption.
- Invalid model JSON triggers a repair attempt.
- Provider timeouts and validation failures return a recoverable in-game failure message.
- Secrets are read from environment variables only.
- Logs are structured enough to trace update, game, and LLM call failures.
- Health endpoints expose readiness and basic metrics.
- A player has at most one active game at a time; `/new` while a game is active prompts before archiving.
- Moderation follows the Moderation Pipeline and always records the final action taken.
- Redis holds the queue, locks, and worldbuilding flow state, and is treated as ephemeral. Transient keys carry TTLs, and loss of in-progress worldbuilding state is recoverable by restarting `/new`. Durable game state never lives only in Redis.
- Admin commands are gated by an allowlist of Telegram user ids from configuration.
- New accounts can play 10 turns for free. After the quota is exhausted, `/new`, natural-language actions, and valid action callbacks are rejected with a recharge prompt until the player redeems a recharge code or has unlimited turns.

## Test Standard

Required coverage:

- World generation schema validation
- Locked-world behavior
- NPC persistence and memory updates
- State reducer bounds
- State Delta Protocol accepted/adjusted/dropped auditing, including clamped numeric deltas, unknown paths, unknown flags, and locked-world targets
- Suggested action callback length
- Telegram message and callback routing
- Idempotent update handling
- Provider fallback and repair behavior
- Docker Compose E2E path from `/new` to three turns
- A deterministic fake LLM provider used across all tests, including the E2E path, so tests never call a live model
- E2E Telegram path uses a `FakeTelegramServer` fixture in `tests/e2e/` that emulates webhook registration, accepts outbound `sendMessage` and `answerCallbackQuery`, returns incrementing `message_id`s, and replays queued updates into FastAPI ingress. The worker talks to it instead of `api.telegram.org`.
- In-flight rejection when a user sends an action during an active turn, including `blocked_by_update_id` on rows dropped because another turn is processing
- Stale callback rejection across turns and games
- Context assembly staying within the token budget as game state grows
- Scoped `memory_update` writes, independent memory caps, and failed re-summarization preserving the prior store
- Moderation input warn continuing into a turn, input refuse short-circuit, output soften/refuse/warn handling, moderation failure policy, and refused output dropping all proposed mutations
- Suggested action retention sweeper, missing action id stale handling, and archive deletion
- Archive read-only views and rejection of callbacks against archived games
- Opening turn sequence `0` and `initial_suggested_actions` attached to that turn
- NPC reveal via explicit `npc_updates.revealed_to_player`
- Game clock persistence on games and turn snapshots; `time_advance` updating only the in-world time tag
- Relationship `edge_type` validation and faction membership changes via `npcs.faction`
- Rate-limit drops at the worker lock boundary with `drop_reason=rate_limited`
- Player quota defaults, `/new` quota validation without consuming quota, successful turn quota consumption, exhausted-quota drops, one-time recharge-code redemption, unlimited-code redemption, and admin-only code generation
- Processing retry backoff with `next_retry_at` and heartbeat-held lease
- Reconciler re-enqueues orphaned `pending` rows after a lost enqueue or flushed queue
- Reclaim of an expired `processing` lease without double-processing a slow-but-alive worker, including `lease_owner`/`lease_token` compare-and-set preventing stale commits
- Re-send of `completed`/`failed` rows missing `telegram_message_ids`, and `dropped` notices not re-sent by the reconciler
- Per-user updates processed in `update_id` order across workers
