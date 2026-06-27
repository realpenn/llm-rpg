from enum import StrEnum


class UpdateStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    DROPPED = "dropped"
    FAILED = "failed"


class DropReason(StrEnum):
    IN_FLIGHT = "in_flight"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXHAUSTED = "quota_exhausted"
    STALE_CALLBACK = "stale_callback"
    ARCHIVED_GAME = "archived_game"
    DUPLICATE = "duplicate"
    OTHER = "other"


class EdgeType(StrEnum):
    PLAYER_NPC = "player_npc"
    NPC_NPC = "npc_npc"
    PLAYER_FACTION = "player_faction"
    NPC_FACTION = "npc_faction"
    FACTION_FACTION = "faction_faction"


class DeltaOp(StrEnum):
    SET = "set"
    ADD = "add"
    REMOVE = "remove"


class LlmPurpose(StrEnum):
    WORLD_BUILD = "world_build"
    TURN = "turn"
    REPAIR = "repair"
    RESUMMARIZE = "resummarize"
    MODERATION = "moderation"
    SOFTEN_REWRITE = "soften_rewrite"


class ModeUsed(StrEnum):
    STRICT = "strict"
    JSON_OBJECT = "json_object"
    REPAIR = "repair"


class LlmOutcome(StrEnum):
    OK = "ok"
    SCHEMA_INVALID = "schema_invalid"
    PROVIDER_TIMEOUT = "provider_timeout"
    REPAIR_FAILED = "repair_failed"
    MODERATION_BLOCKED = "moderation_blocked"
    PROVIDER_ERROR = "provider_error"


class ModerationStage(StrEnum):
    INPUT = "input"
    OUTPUT = "output"


class ModerationAction(StrEnum):
    SOFTEN = "soften"
    REFUSE = "refuse"
    WARN = "warn"


class MemoryScope(StrEnum):
    WORLD = "world"
    NPC = "npc"
    FACTION = "faction"


class TimeAdvance(StrEnum):
    MINUTES = "minutes"
    HOURS = "hours"
    OVERNIGHT = "overnight"
