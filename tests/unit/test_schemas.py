import pytest
from pydantic import ValidationError

from llm_rpg.schemas import (
    DeltaOp,
    EdgeType,
    MemoryUpdateEntry,
    ModerationAction,
    ModerationStage,
    NpcUpdate,
    PlayerState,
    SafetyFlagRecord,
    StateDeltaEntry,
    SuggestedAction,
    TimeAdvance,
    TurnOutput,
    WorldBible,
    WorldBuildOutput,
)


def test_world_build_output_validates_complete_payload() -> None:
    output = WorldBuildOutput(
        world=_world_bible(),
        opening_narration="雨夜里，旧城的钟声敲响，你在档案馆门前醒来。",
        player_state=PlayerState(
            name="阿岚",
            profession="runner",
            location="old_city",
            vitals={"hp": 8, "energy": 5},
            currency={"coin": 2},
            conditions=["watched"],
        ),
        initial_suggested_actions=["查看档案馆", "寻找巡夜人", "检查随身物品"],
    )

    assert output.world.initial_location == "old_city"
    assert output.player_state.conditions == ["watched"]


def test_world_bible_rejects_unknown_initial_location() -> None:
    payload = _world_bible().model_dump()
    payload["initial_location"] = "missing"

    with pytest.raises(ValidationError):
        WorldBible.model_validate(payload)


def test_world_bible_normalizes_flag_type_aliases() -> None:
    payload = _world_bible().model_dump()
    payload["player_stat_schema"]["allowed_flags"]["reputation"] = {
        "type": "integer",
        "description": "声望",
        "default": 0,
    }

    world = WorldBible.model_validate(payload)

    assert world.player_stat_schema.allowed_flags["reputation"].type == "number"


def test_world_bible_normalizes_revealed_to_player_alias() -> None:
    payload = _world_bible().model_dump()
    payload["initial_npcs"][0]["reveled_to_player"] = True

    world = WorldBible.model_validate(payload)

    assert world.initial_npcs[0].revealed_to_player is True


def test_npc_update_normalizes_revealed_to_player_alias() -> None:
    update = NpcUpdate.model_validate({"key": "archivist", "reveled_to_player": True})

    assert update.revealed_to_player is True


def test_npc_update_normalizes_memory_list_to_string() -> None:
    update = NpcUpdate.model_validate({"key": "archivist", "memory": ["第一条", "第二条"]})

    assert update.memory == "第一条\n第二条"


def test_player_state_enforces_caps_and_inventory_keys() -> None:
    with pytest.raises(ValidationError):
        PlayerState(location="old_city", conditions=["a", "b"], condition_cap=1)


def test_state_delta_accepts_protocol_shape() -> None:
    delta = StateDeltaEntry(path="inventory.lantern.quantity", op=DeltaOp.ADD, value=1)

    assert delta.path == "inventory.lantern.quantity"
    assert delta.op == DeltaOp.ADD


def test_state_delta_allows_unknown_unicode_path_for_reducer_audit() -> None:
    delta = StateDeltaEntry(path="flags.发现镜面线索", op=DeltaOp.SET, value=True)

    assert delta.path == "flags.发现镜面线索"


def test_turn_output_rejects_safety_flags_from_llm() -> None:
    payload = _turn_output().model_dump()
    payload["safety_flags"] = [{"stage": "output", "flag": "x", "action": "warn"}]

    with pytest.raises(ValidationError):
        TurnOutput.model_validate(payload)


def test_turn_output_requires_three_to_five_suggested_actions() -> None:
    payload = _turn_output().model_dump()
    payload["suggested_actions"] = [payload["suggested_actions"][0]]

    with pytest.raises(ValidationError):
        TurnOutput.model_validate(payload)


def test_memory_update_scope_is_explicitly_scoped() -> None:
    assert MemoryUpdateEntry(scope="world", content="旧城开始戒严").scope == "world"
    assert (
        MemoryUpdateEntry(scope="npc:archivist", content="记得玩家的问题").scope == "npc:archivist"
    )
    assert (
        MemoryUpdateEntry(scope="faction:council", content="怀疑外来者").scope == "faction:council"
    )

    with pytest.raises(ValidationError):
        MemoryUpdateEntry(scope="npc", content="缺少 key")


def test_memory_update_normalizes_common_unscoped_aliases_to_world() -> None:
    assert MemoryUpdateEntry(scope="player", content="玩家发现线索").scope == "world"
    assert MemoryUpdateEntry(scope="case", content=["线索一", "线索二"]).content == "线索一\n线索二"


def test_turn_output_normalizes_time_advance_aliases() -> None:
    payload = _turn_output().model_dump()
    payload["time_advance"] = "几分钟"

    output = TurnOutput.model_validate(payload)

    assert output.time_advance == TimeAdvance.MINUTES


def test_turn_output_accepts_common_provider_shape_drift_without_repair() -> None:
    payload = _turn_output().model_dump()
    payload["npc_updates"] = [{"key": "archivist", "memory": ["第一条", "第二条"]}]
    payload["memory_update"] = [{"scope": "investigation", "content": ["线索一", "线索二"]}]
    payload["time_advance"] = "几分钟"

    output = TurnOutput.model_validate(payload)

    assert output.npc_updates[0].memory == "第一条\n第二条"
    assert output.memory_update[0].scope == "world"
    assert output.memory_update[0].content == "线索一\n线索二"
    assert output.time_advance == TimeAdvance.MINUTES


def test_turn_output_wraps_single_memory_update_object() -> None:
    payload = _turn_output().model_dump()
    payload["memory_update"] = {"scope": "world", "content": "单条摘要"}

    output = TurnOutput.model_validate(payload)

    assert len(output.memory_update) == 1
    assert output.memory_update[0].content == "单条摘要"


def test_safety_flag_record_is_worker_side_contract() -> None:
    flag = SafetyFlagRecord(
        stage=ModerationStage.OUTPUT,
        flag="violence",
        action=ModerationAction.SOFTEN,
        rewrites=1,
    )

    assert flag.stage == ModerationStage.OUTPUT


def _turn_output() -> TurnOutput:
    return TurnOutput(
        narration="你把灯举高，墙上的潮痕像一行未写完的字。",
        state_delta=[StateDeltaEntry(path="vitals.energy", op=DeltaOp.ADD, value=-1)],
        npc_updates=[{"key": "archivist", "revealed_to_player": True}],
        relationship_updates=[
            {
                "source_key": "player",
                "target_key": "archivist",
                "edge_type": EdgeType.PLAYER_NPC,
                "trust_delta": 1,
            }
        ],
        events=[{"summary": "玩家进入旧城档案馆", "location": "old_city"}],
        suggested_actions=[
            SuggestedAction(label="询问管理员", action="询问管理员这里发生了什么"),
            SuggestedAction(label="查看墙面", action="查看墙上的潮痕"),
            SuggestedAction(label="保持警惕", action="先观察四周的出口"),
        ],
        memory_update=[MemoryUpdateEntry(scope="world", content="玩家抵达旧城档案馆")],
        time_advance=TimeAdvance.MINUTES,
    )


def _world_bible() -> WorldBible:
    return WorldBible.model_validate(
        {
            "summary": "旧城被一场不会停的雨困住，玩家要找出钟楼失声的原因。",
            "language": "zh-CN",
            "genre": "mystery fantasy",
            "tone": "冷静、克制、带悬疑感",
            "era_geography": "近代海港旧城",
            "locked_laws": ["雨会记录谎言", "钟楼停止时亡者会说话"],
            "factions": [
                {
                    "key": "council",
                    "name": "旧城议会",
                    "description": "管理旧城档案和宵禁的权力机构",
                    "ideology": "秩序高于真相",
                }
            ],
            "locations": [
                {
                    "key": "old_city",
                    "name": "旧城",
                    "description": "雨水、煤烟和档案纸气味混在一起的街区",
                }
            ],
            "player_stat_schema": {
                "vitals": {
                    "hp": {"min": 0, "max": 10, "default": 8},
                    "energy": {"min": 0, "max": 10, "default": 5},
                },
                "currency": {"coin": {"min": 0, "max": 999, "default": 2}},
                "allowed_conditions": ["watched", "wounded"],
                "allowed_flags": {
                    "knows_clock_secret": {
                        "type": "boolean",
                        "description": "是否知道钟楼秘密",
                    }
                },
            },
            "initial_location": "old_city",
            "initial_npcs": [
                {
                    "key": "archivist",
                    "name": "闻鹤",
                    "title": "档案管理员",
                    "role": "keeper",
                    "faction": "council",
                    "location": "old_city",
                    "personality": "谨慎，惜字如金",
                    "desire": "保护档案馆",
                    "fear": "雨水毁掉禁档",
                    "secret": "他听见过停钟后的声音",
                    "goal": "确认玩家是否可信",
                    "status": "active",
                }
            ],
            "dangers": ["宵禁巡逻", "会记录谎言的雨"],
            "available_roles": ["runner", "doctor"],
            "narrative_style": "第二人称、短段落、重视可行动线索",
            "taboos": ["不改写雨与钟楼的核心规则"],
            "core_conflict": "旧城议会想掩盖停钟真相，亡者想让真相重见天日。",
        }
    )
