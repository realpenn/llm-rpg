from pydantic import BaseModel, ConfigDict, Field

from llm_rpg.schemas.enums import ModerationAction, ModerationStage


class SafetyFlagRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: ModerationStage
    flag: str = Field(min_length=1, max_length=120)
    action: ModerationAction
    rewrites: int = Field(default=0, ge=0, le=1)
