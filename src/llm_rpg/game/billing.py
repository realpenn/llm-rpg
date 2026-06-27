from dataclasses import dataclass
from datetime import UTC, datetime
from secrets import choice
from typing import Literal

from sqlalchemy import case, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from llm_rpg.models import Player, RechargeCode

DEFAULT_FREE_TURNS = 10
MAX_ADMIN_CODE_BATCH = 20

RechargePackage = Literal["10", "50", "100", "unlimited"]
RECHARGE_PACKAGES: dict[RechargePackage, int | None] = {
    "10": 10,
    "50": 50,
    "100": 100,
    "unlimited": None,
}
_PACKAGE_ALIASES: dict[str, RechargePackage] = {
    "10": "10",
    "10回合": "10",
    "50": "50",
    "50回合": "50",
    "100": "100",
    "100回合": "100",
    "unlimited": "unlimited",
    "infinite": "unlimited",
    "∞": "unlimited",
    "无限": "unlimited",
    "无限回合": "unlimited",
}
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


@dataclass(slots=True)
class RedeemResult:
    status: Literal["ok", "invalid", "used"]
    turn_amount: int | None = None
    unlimited: bool = False


async def has_turn_credit(session: AsyncSession, player: Player) -> bool:
    player_id = await session.scalar(
        select(Player.id).where(
            Player.id == player.id,
            or_(Player.has_unlimited_turns.is_(True), Player.remaining_turns > 0),
        )
    )
    return player_id is not None


async def consume_turn_credit(session: AsyncSession, player: Player) -> bool:
    result = await session.execute(
        update(Player)
        .where(
            Player.id == player.id,
            or_(Player.has_unlimited_turns.is_(True), Player.remaining_turns > 0),
        )
        .values(
            remaining_turns=case(
                (Player.has_unlimited_turns.is_(True), Player.remaining_turns),
                else_=Player.remaining_turns - 1,
            )
        )
    )
    if result.rowcount != 1:
        return False
    await session.refresh(player, attribute_names=["remaining_turns", "has_unlimited_turns"])
    return True


def format_player_quota(player: Player) -> str:
    if player.has_unlimited_turns:
        return "无限回合"
    return f"{player.remaining_turns} 回合"


def normalize_recharge_package(value: str) -> RechargePackage | None:
    return _PACKAGE_ALIASES.get(value.strip().lower())


def recharge_package_label(package: RechargePackage) -> str:
    amount = RECHARGE_PACKAGES[package]
    return "无限回合" if amount is None else f"{amount} 回合"


async def create_recharge_codes(
    session: AsyncSession,
    *,
    package: RechargePackage,
    count: int,
    created_by_telegram_user_id: int,
) -> list[RechargeCode]:
    amount = RECHARGE_PACKAGES[package]
    codes: list[RechargeCode] = []
    seen: set[str] = set()
    while len(codes) < count:
        code = _generate_code()
        if code in seen:
            continue
        exists = await session.scalar(select(RechargeCode.id).where(RechargeCode.code == code))
        if exists is not None:
            continue
        seen.add(code)
        recharge_code = RechargeCode(
            code=code,
            turn_amount=amount,
            unlimited=amount is None,
            created_by_telegram_user_id=created_by_telegram_user_id,
        )
        session.add(recharge_code)
        codes.append(recharge_code)
    await session.flush()
    return codes


async def redeem_recharge_code(
    session: AsyncSession,
    *,
    player: Player,
    raw_code: str,
) -> RedeemResult:
    code = normalize_recharge_code(raw_code)
    if not code:
        return RedeemResult(status="invalid")

    recharge_code = await session.scalar(select(RechargeCode).where(RechargeCode.code == code))
    if recharge_code is None:
        return RedeemResult(status="invalid")
    if recharge_code.used_at is not None:
        return RedeemResult(status="used")

    now = datetime.now(UTC)
    result = await session.execute(
        update(RechargeCode)
        .where(RechargeCode.id == recharge_code.id, RechargeCode.used_at.is_(None))
        .values(used_by_player_id=player.id, used_at=now)
    )
    if result.rowcount != 1:
        return RedeemResult(status="used")

    if recharge_code.unlimited:
        await session.execute(
            update(Player).where(Player.id == player.id).values(has_unlimited_turns=True)
        )
    else:
        await session.execute(
            update(Player)
            .where(Player.id == player.id)
            .values(remaining_turns=Player.remaining_turns + (recharge_code.turn_amount or 0))
        )

    await session.refresh(player, attribute_names=["remaining_turns", "has_unlimited_turns"])
    return RedeemResult(
        status="ok",
        turn_amount=recharge_code.turn_amount,
        unlimited=recharge_code.unlimited,
    )


def normalize_recharge_code(raw_code: str) -> str:
    return raw_code.strip().upper()


def _generate_code() -> str:
    groups = ["".join(choice(_CODE_ALPHABET) for _ in range(4)) for _ in range(3)]
    return "RPG-" + "-".join(groups)
