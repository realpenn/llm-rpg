from dataclasses import dataclass
from typing import Any

from llm_rpg.schemas.enums import DeltaOp
from llm_rpg.schemas.player import InventoryItem, PlayerState
from llm_rpg.schemas.turn import StateDeltaEntry
from llm_rpg.schemas.world import FlagSpec, NumericBound, WorldBible


@dataclass(slots=True)
class ReducerResult:
    player_state: PlayerState
    delta_audit: dict[str, list[dict[str, Any]]]
    delta_dropped_summary: dict[str, Any]


def apply_state_delta(
    player_state: PlayerState,
    world: WorldBible,
    deltas: list[StateDeltaEntry],
) -> ReducerResult:
    state = player_state.model_copy(deep=True)
    state.condition_cap = world.player_stat_schema.condition_cap
    state.flag_cap = world.player_stat_schema.flag_cap
    state.inventory_cap = world.player_stat_schema.inventory_cap
    audit: dict[str, list[dict[str, Any]]] = {"accepted": [], "adjusted": [], "dropped": []}

    for delta in deltas:
        _apply_one(state, world, delta, audit)

    dropped = audit["dropped"]
    return ReducerResult(
        player_state=state,
        delta_audit=audit,
        delta_dropped_summary={
            "count": len(dropped),
            "items": [
                {"path": item["path"], "op": item["op"], "reason": item["reason"]}
                for item in dropped
            ],
        },
    )


def _apply_one(
    state: PlayerState,
    world: WorldBible,
    delta: StateDeltaEntry,
    audit: dict[str, list[dict[str, Any]]],
) -> None:
    parts = delta.path.split(".")
    root = parts[0]
    if _targets_locked_world(root):
        _drop(audit, delta, "locked_world_target")
        return
    if root == "vitals" and len(parts) == 2:
        _apply_numeric(state.vitals, world.player_stat_schema.vitals, parts[1], delta, audit)
        return
    if root == "currency" and len(parts) == 2:
        _apply_numeric(state.currency, world.player_stat_schema.currency, parts[1], delta, audit)
        return
    if root == "conditions" and len(parts) == 1:
        _apply_condition(state, world, delta, audit)
        return
    if root == "inventory":
        _apply_inventory(state, delta, parts, audit)
        return
    if root == "flags" and len(parts) == 2:
        _apply_flag(state, world.player_stat_schema.allowed_flags, parts[1], delta, audit)
        return
    _drop(audit, delta, "unknown_path")


def _apply_numeric(
    values: dict[str, float],
    bounds: dict[str, NumericBound],
    key: str,
    delta: StateDeltaEntry,
    audit: dict[str, list[dict[str, Any]]],
) -> None:
    if key not in bounds:
        _drop(audit, delta, "unknown_numeric_field")
        return
    if delta.op not in {DeltaOp.SET, DeltaOp.ADD}:
        _drop(audit, delta, "unsupported_numeric_op")
        return
    if not _is_number(delta.value):
        _drop(audit, delta, "wrong_value_type")
        return

    bound = bounds[key]
    before = float(values.get(key, bound.default if bound.default is not None else bound.min))
    proposed = float(delta.value) if delta.op == DeltaOp.SET else before + float(delta.value)
    after = min(max(proposed, bound.min), bound.max)
    values[key] = after
    if after != proposed:
        _adjust(audit, delta, "clamped_to_bounds", before, after)
    else:
        _accept(audit, delta, before, after)


def _apply_condition(
    state: PlayerState,
    world: WorldBible,
    delta: StateDeltaEntry,
    audit: dict[str, list[dict[str, Any]]],
) -> None:
    if delta.op not in {DeltaOp.SET, DeltaOp.ADD, DeltaOp.REMOVE}:
        _drop(audit, delta, "unsupported_condition_op")
        return
    if not isinstance(delta.value, str):
        _drop(audit, delta, "wrong_value_type")
        return
    if delta.value not in world.player_stat_schema.allowed_conditions:
        _drop(audit, delta, "unknown_condition")
        return

    before = list(state.conditions)
    if delta.op == DeltaOp.REMOVE:
        state.conditions = [item for item in state.conditions if item != delta.value]
        _accept(audit, delta, before, list(state.conditions))
        return
    if delta.value in state.conditions:
        _accept(audit, delta, before, list(state.conditions))
        return
    if len(state.conditions) >= world.player_stat_schema.condition_cap:
        _drop(audit, delta, "condition_cap_exceeded")
        return
    state.conditions.append(delta.value)
    _accept(audit, delta, before, list(state.conditions))


def _apply_inventory(
    state: PlayerState,
    delta: StateDeltaEntry,
    parts: list[str],
    audit: dict[str, list[dict[str, Any]]],
) -> None:
    if len(parts) == 2 and delta.op == DeltaOp.SET:
        _upsert_inventory_item(state, delta, parts[1], audit)
        return
    if len(parts) == 3 and parts[2] == "quantity" and delta.op == DeltaOp.ADD:
        _add_inventory_quantity(state, delta, parts[1], audit)
        return
    _drop(audit, delta, "unsupported_inventory_path_or_op")


def _upsert_inventory_item(
    state: PlayerState,
    delta: StateDeltaEntry,
    key: str,
    audit: dict[str, list[dict[str, Any]]],
) -> None:
    try:
        item = (
            delta.value
            if isinstance(delta.value, InventoryItem)
            else InventoryItem.model_validate(delta.value)
        )
    except (TypeError, ValueError) as exc:
        _drop(audit, delta, "wrong_value_type", str(exc))
        return
    if item.key != key:
        _drop(audit, delta, "inventory_key_mismatch")
        return
    if key not in state.inventory and len(state.inventory) >= state.inventory_cap:
        _drop(audit, delta, "inventory_cap_exceeded")
        return
    before = state.inventory.get(key).model_dump(mode="json") if key in state.inventory else None
    state.inventory[key] = item
    _accept(audit, delta, before, item.model_dump(mode="json"))


def _add_inventory_quantity(
    state: PlayerState,
    delta: StateDeltaEntry,
    key: str,
    audit: dict[str, list[dict[str, Any]]],
) -> None:
    if key not in state.inventory:
        _drop(audit, delta, "missing_inventory_item")
        return
    if not _is_number(delta.value):
        _drop(audit, delta, "wrong_value_type")
        return
    item = state.inventory[key]
    before = item.quantity
    proposed = before + int(delta.value)
    after = max(proposed, 0)
    state.inventory[key] = item.model_copy(update={"quantity": after})
    if after != proposed:
        _adjust(audit, delta, "floored_at_zero", before, after)
    else:
        _accept(audit, delta, before, after)


def _apply_flag(
    state: PlayerState,
    flag_specs: dict[str, FlagSpec],
    key: str,
    delta: StateDeltaEntry,
    audit: dict[str, list[dict[str, Any]]],
) -> None:
    if key not in flag_specs:
        _drop(audit, delta, "unknown_flag")
        return
    if delta.op == DeltaOp.REMOVE:
        before = state.flags.get(key)
        state.flags.pop(key, None)
        _accept(audit, delta, before, None)
        return
    if delta.op != DeltaOp.SET:
        _drop(audit, delta, "unsupported_flag_op")
        return
    if not _flag_value_matches(flag_specs[key], delta.value):
        _drop(audit, delta, "wrong_value_type")
        return
    if key not in state.flags and len(state.flags) >= state.flag_cap:
        _drop(audit, delta, "flag_cap_exceeded")
        return
    before = state.flags.get(key)
    state.flags[key] = delta.value
    _accept(audit, delta, before, delta.value)


def _targets_locked_world(root: str) -> bool:
    return root in {"world", "world_bible", "locked_laws", "factions", "npcs"}


def _flag_value_matches(spec: FlagSpec, value: Any) -> bool:
    if spec.type == "boolean":
        return isinstance(value, bool)
    if spec.type == "string":
        return isinstance(value, str)
    if spec.type == "number":
        return _is_number(value)
    return False


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _accept(
    audit: dict[str, list[dict[str, Any]]],
    delta: StateDeltaEntry,
    before: Any,
    after: Any,
) -> None:
    audit["accepted"].append(_audit_entry(delta, "accepted", before=before, after=after))


def _adjust(
    audit: dict[str, list[dict[str, Any]]],
    delta: StateDeltaEntry,
    reason: str,
    before: Any,
    after: Any,
) -> None:
    audit["adjusted"].append(_audit_entry(delta, reason, before=before, after=after))


def _drop(
    audit: dict[str, list[dict[str, Any]]],
    delta: StateDeltaEntry,
    reason: str,
    detail: str | None = None,
) -> None:
    entry = _audit_entry(delta, reason)
    if detail:
        entry["detail"] = detail
    audit["dropped"].append(entry)


def _audit_entry(
    delta: StateDeltaEntry,
    reason: str,
    *,
    before: Any = None,
    after: Any = None,
) -> dict[str, Any]:
    entry = {
        "path": delta.path,
        "op": delta.op.value,
        "value": _jsonable(delta.value),
        "reason": reason,
    }
    if before is not None:
        entry["before"] = _jsonable(before)
    if after is not None:
        entry["after"] = _jsonable(after)
    return entry


def _jsonable(value: Any) -> Any:
    if isinstance(value, InventoryItem):
        return value.model_dump(mode="json")
    return value
