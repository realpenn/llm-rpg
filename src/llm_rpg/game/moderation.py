import json
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from llm_rpg.llm.base import ChatMessage, Provider
from llm_rpg.schemas.enums import LlmPurpose, ModerationAction, ModerationStage
from llm_rpg.schemas.moderation import SafetyFlagRecord
from llm_rpg.schemas.turn import TurnOutput


class ModerationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["allow", "warn", "refuse", "soften"] = "allow"
    flag: str = Field(default="none", max_length=120)
    message: str = Field(default="", max_length=1000)


@dataclass(slots=True)
class InputModerationResult:
    refused: bool
    safety_flags: list[SafetyFlagRecord]
    refusal_text: str | None = None


@dataclass(slots=True)
class OutputModerationResult:
    output: TurnOutput | None
    refused: bool
    safety_flags: list[SafetyFlagRecord]
    refusal_narration: str | None = None


class ModerationService:
    def __init__(self, provider: Provider) -> None:
        self.provider = provider

    async def moderate_input(self, text: str) -> InputModerationResult:
        try:
            decision = await self.provider.generate_structured(
                [
                    ChatMessage(
                        role="system",
                        content="审核玩家输入，返回 moderation decision JSON object。",
                    ),
                    ChatMessage(role="user", content=text),
                ],
                ModerationDecision,
                LlmPurpose.MODERATION,
            )
        except Exception:
            return InputModerationResult(
                refused=False,
                safety_flags=[
                    SafetyFlagRecord(
                        stage=ModerationStage.INPUT,
                        flag="moderation_unavailable",
                        action=ModerationAction.WARN,
                        rewrites=0,
                    )
                ],
            )
        if decision.action == "allow":
            return InputModerationResult(refused=False, safety_flags=[])
        if decision.action == "refuse":
            return InputModerationResult(
                refused=True,
                safety_flags=[_flag(ModerationStage.INPUT, decision, ModerationAction.REFUSE)],
                refusal_text=decision.message or "这个行动暂时不能继续。请换一种做法。",
            )
        return InputModerationResult(
            refused=False,
            safety_flags=[_flag(ModerationStage.INPUT, decision, ModerationAction.WARN)],
        )

    async def moderate_output(self, output: TurnOutput) -> OutputModerationResult:
        try:
            decision = await self._moderate_output_payload(output)
        except Exception:
            return OutputModerationResult(
                output=None,
                refused=True,
                safety_flags=[
                    SafetyFlagRecord(
                        stage=ModerationStage.OUTPUT,
                        flag="moderation_unavailable",
                        action=ModerationAction.REFUSE,
                        rewrites=0,
                    )
                ],
                refusal_narration="局势变得难以判断，你决定暂时收手，重新观察眼前的状况。",
            )
        if decision.action == "allow":
            return OutputModerationResult(output=output, refused=False, safety_flags=[])
        if decision.action == "warn":
            return OutputModerationResult(
                output=output,
                refused=False,
                safety_flags=[_flag(ModerationStage.OUTPUT, decision, ModerationAction.WARN)],
            )
        if decision.action == "refuse":
            return _refused_output(decision, rewrites=0)

        softened_flag = _flag(ModerationStage.OUTPUT, decision, ModerationAction.SOFTEN, rewrites=1)
        try:
            rewritten = await self.provider.generate_structured(
                [
                    ChatMessage(
                        role="system", content="重写 TurnOutput 以降低被标记内容，只返回 JSON。"
                    ),
                    ChatMessage(role="user", content=output.model_dump_json()),
                ],
                TurnOutput,
                LlmPurpose.SOFTEN_REWRITE,
            )
            second = await self._moderate_output_payload(rewritten)
        except Exception:
            return OutputModerationResult(
                output=None,
                refused=True,
                safety_flags=[
                    SafetyFlagRecord(
                        stage=ModerationStage.OUTPUT,
                        flag="soften_failed",
                        action=ModerationAction.REFUSE,
                        rewrites=1,
                    )
                ],
                refusal_narration="局势变得难以判断，你决定暂时收手，重新观察眼前的状况。",
            )
        if second.action == "allow":
            return OutputModerationResult(
                output=rewritten,
                refused=False,
                safety_flags=[softened_flag],
            )
        if second.action == "warn":
            return OutputModerationResult(
                output=rewritten,
                refused=False,
                safety_flags=[
                    softened_flag,
                    _flag(ModerationStage.OUTPUT, second, ModerationAction.WARN, rewrites=1),
                ],
            )
        return _refused_output(second, rewrites=1)

    async def _moderate_output_payload(self, output: TurnOutput) -> ModerationDecision:
        return await self.provider.generate_structured(
            [
                ChatMessage(
                    role="system",
                    content="审核 LLM 回合输出，返回 moderation decision JSON object。",
                ),
                ChatMessage(
                    role="user",
                    content=json.dumps(output.model_dump(mode="json"), ensure_ascii=False),
                ),
            ],
            ModerationDecision,
            LlmPurpose.MODERATION,
        )


def _flag(
    stage: ModerationStage,
    decision: ModerationDecision,
    action: ModerationAction,
    *,
    rewrites: int = 0,
) -> SafetyFlagRecord:
    return SafetyFlagRecord(
        stage=stage,
        flag=decision.flag if decision.flag and decision.flag != "none" else "moderation",
        action=action,
        rewrites=rewrites,
    )


def _refused_output(decision: ModerationDecision, *, rewrites: int) -> OutputModerationResult:
    return OutputModerationResult(
        output=None,
        refused=True,
        safety_flags=[
            _flag(ModerationStage.OUTPUT, decision, ModerationAction.REFUSE, rewrites=rewrites)
        ],
        refusal_narration=decision.message
        or "局势变得难以判断，你决定暂时收手，重新观察眼前的状况。",
    )
