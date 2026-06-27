from collections.abc import Sequence

from llm_rpg.llm.base import ChatMessage

WORLD_BUILD_SYSTEM_PROMPT = (
    "你是 Telegram 中文文字 RPG 的世界构建器。"
    "根据玩家种子生成结构化 WorldBuildOutput。"
    "只输出一个符合 schema 的 JSON object，并严格遵守安全边界。"
    "顶层字段只能是 world、opening_narration、player_state、initial_suggested_actions。"
    "不要输出 world_name、player_character、main_quest 等 schema 外字段；"
    "世界名称、主线任务等内容必须写入 schema 已定义字段中。"
    "NPC 可见性字段必须拼写为 revealed_to_player。"
    "allowed_flags 中的 type 只能是 boolean、string 或 number；整数计数也使用 number。"
)

TURN_SYSTEM_PROMPT = (
    "你是 Telegram 中文文字 RPG 的回合裁定器。"
    "只能根据锁定世界规则、当前状态和玩家输入生成结构化 TurnOutput。"
    "只输出一个符合 schema 的 JSON object。"
    "state_delta.path 必须使用已声明的 ASCII key，例如 vitals.hp、currency.coin、"
    "conditions、inventory.item_key、inventory.item_key.quantity、flags.declared_key；"
    "不要在 path 中创造中文 key。无法确定字段时不要输出 state_delta。"
    "npc_updates[].key 必须是 NPCS 列表中该角色的 ASCII key 字段，不要用角色中文名作为 key；"
    "NPCS 列表中不存在的角色不要输出 npc_updates。"
    "npc_updates.memory 必须是单个字符串，不要用数组。"
    "memory_update.scope 只能是 world、npc:<key> 或 faction:<key>；案件/玩家调查摘要默认用 world。"
    "time_advance 只能是 minutes、hours、overnight 或 null。"
)


def world_build_messages(seed: str) -> list[ChatMessage]:
    return [
        ChatMessage(role="system", content=WORLD_BUILD_SYSTEM_PROMPT),
        ChatMessage(role="user", content=seed),
    ]


def turn_messages(context_sections: Sequence[str], player_action: str) -> list[ChatMessage]:
    return [
        ChatMessage(role="system", content=TURN_SYSTEM_PROMPT),
        ChatMessage(
            role="user", content="\n\n".join([*context_sections, f"玩家行动: {player_action}"])
        ),
    ]
