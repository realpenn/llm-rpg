from llm_rpg.game.reducers import apply_state_delta
from llm_rpg.schemas import DeltaOp, InventoryItem, PlayerState, StateDeltaEntry, WorldBible


def test_numeric_delta_clamps_and_audits_adjusted() -> None:
    result = apply_state_delta(
        _player_state(vitals={"hp": 8}),
        _world(),
        [StateDeltaEntry(path="vitals.hp", op=DeltaOp.ADD, value=10)],
    )

    assert result.player_state.vitals["hp"] == 10
    assert result.delta_audit["adjusted"][0]["reason"] == "clamped_to_bounds"


def test_unknown_and_locked_world_paths_are_dropped() -> None:
    result = apply_state_delta(
        _player_state(),
        _world(),
        [
            StateDeltaEntry(path="world.summary", op=DeltaOp.SET, value="rewrite"),
            StateDeltaEntry(path="vitals.unknown", op=DeltaOp.ADD, value=1),
        ],
    )

    assert result.delta_dropped_summary == {
        "count": 2,
        "items": [
            {"path": "world.summary", "op": "set", "reason": "locked_world_target"},
            {"path": "vitals.unknown", "op": "add", "reason": "unknown_numeric_field"},
        ],
    }


def test_conditions_are_bounded_and_never_evict_implicitly() -> None:
    result = apply_state_delta(
        _player_state(conditions=["watched"]),
        _world(condition_cap=1),
        [
            StateDeltaEntry(path="conditions", op=DeltaOp.SET, value="watched"),
            StateDeltaEntry(path="conditions", op=DeltaOp.SET, value="wounded"),
            StateDeltaEntry(path="conditions", op=DeltaOp.SET, value="unknown"),
        ],
    )

    assert result.player_state.conditions == ["watched"]
    assert len(result.delta_audit["accepted"]) == 1
    assert [item["reason"] for item in result.delta_audit["dropped"]] == [
        "condition_cap_exceeded",
        "unknown_condition",
    ]


def test_inventory_upsert_cap_and_quantity_floor() -> None:
    state = _player_state(
        inventory={
            "lantern": InventoryItem(key="lantern", name="提灯", quantity=2),
        },
        inventory_cap=1,
    )
    result = apply_state_delta(
        state,
        _world(inventory_cap=1),
        [
            StateDeltaEntry(
                path="inventory.lantern",
                op=DeltaOp.SET,
                value={"key": "lantern", "name": "铜制提灯", "quantity": 2},
            ),
            StateDeltaEntry(path="inventory.lantern.quantity", op=DeltaOp.ADD, value=-5),
            StateDeltaEntry(
                path="inventory.key",
                op=DeltaOp.SET,
                value={"key": "key", "name": "钥匙", "quantity": 1},
            ),
            StateDeltaEntry(path="inventory.missing.quantity", op=DeltaOp.ADD, value=1),
        ],
    )

    assert result.player_state.inventory["lantern"].name == "铜制提灯"
    assert result.player_state.inventory["lantern"].quantity == 0
    assert result.delta_audit["adjusted"][0]["reason"] == "floored_at_zero"
    assert [item["reason"] for item in result.delta_audit["dropped"]] == [
        "inventory_cap_exceeded",
        "missing_inventory_item",
    ]


def test_flags_must_be_declared_and_type_checked() -> None:
    result = apply_state_delta(
        _player_state(flags={"knows_clock_secret": False}),
        _world(),
        [
            StateDeltaEntry(path="flags.knows_clock_secret", op=DeltaOp.SET, value=True),
            StateDeltaEntry(path="flags.knows_clock_secret", op=DeltaOp.SET, value="yes"),
            StateDeltaEntry(path="flags.unknown", op=DeltaOp.SET, value=True),
            StateDeltaEntry(path="flags.clock_note", op=DeltaOp.SET, value="雨变大了"),
            StateDeltaEntry(path="flags.clock_note", op=DeltaOp.REMOVE, value=None),
        ],
    )

    assert result.player_state.flags == {"knows_clock_secret": True}
    assert [item["reason"] for item in result.delta_audit["dropped"]] == [
        "wrong_value_type",
        "unknown_flag",
    ]


def test_reducer_does_not_mutate_original_state() -> None:
    state = _player_state(vitals={"hp": 8})

    result = apply_state_delta(
        state,
        _world(),
        [StateDeltaEntry(path="vitals.hp", op=DeltaOp.ADD, value=-2)],
    )

    assert state.vitals["hp"] == 8
    assert result.player_state.vitals["hp"] == 6


def _player_state(
    *,
    vitals: dict[str, float] | None = None,
    conditions: list[str] | None = None,
    flags: dict[str, bool | str | int | float] | None = None,
    inventory: dict[str, InventoryItem] | None = None,
    inventory_cap: int = 64,
) -> PlayerState:
    return PlayerState(
        name="阿岚",
        profession="runner",
        location="old_city",
        vitals=vitals or {"hp": 8, "energy": 5},
        currency={"coin": 2},
        conditions=conditions or [],
        flags=flags or {},
        inventory=inventory or {},
        inventory_cap=inventory_cap,
    )


def _world(
    *,
    condition_cap: int = 16,
    inventory_cap: int = 64,
) -> WorldBible:
    return WorldBible.model_validate(
        {
            "summary": "旧城被一场不会停的雨困住。",
            "language": "zh-CN",
            "genre": "mystery fantasy",
            "tone": "冷静",
            "era_geography": "近代海港旧城",
            "locked_laws": ["雨会记录谎言"],
            "locations": [
                {"key": "old_city", "name": "旧城", "description": "潮湿的街区"},
            ],
            "player_stat_schema": {
                "vitals": {
                    "hp": {"min": 0, "max": 10, "default": 8},
                    "energy": {"min": 0, "max": 10, "default": 5},
                },
                "currency": {"coin": {"min": 0, "max": 999, "default": 2}},
                "allowed_conditions": ["watched", "wounded"],
                "allowed_flags": {
                    "knows_clock_secret": {"type": "boolean"},
                    "clock_note": {"type": "string"},
                },
                "condition_cap": condition_cap,
                "inventory_cap": inventory_cap,
            },
            "initial_location": "old_city",
            "narrative_style": "第二人称",
            "core_conflict": "议会掩盖停钟真相。",
        }
    )
