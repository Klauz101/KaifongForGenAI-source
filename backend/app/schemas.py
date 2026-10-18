"""Pydantic schemas. `Extraction` is the post-call JSON contract (SPEC 7.4)."""

from typing import Literal

from pydantic import BaseModel, Field

Conf = Literal["high", "medium", "low"]


class _Ev(BaseModel):
    evidence_turns: list[int] = Field(default_factory=list)


class Mood(_Ev):
    value: int | None = Field(default=None, ge=1, le=5)
    confidence: Conf = "low"


class Sleep(_Ev):
    value: Literal["good", "poor", "unknown"] = "unknown"
    confidence: Conf = "low"


class Appetite(_Ev):
    value: Literal["normal", "reduced", "skipped_meals", "unknown"] = "unknown"
    confidence: Conf = "low"


class MedsTaken(_Ev):
    value: Literal["yes", "partial", "no", "unknown", "not_applicable"] = "unknown"
    confidence: Conf = "low"


class Pain(_Ev):
    value: Literal["none", "mild", "moderate", "severe", "unknown"] = "unknown"
    location: str | None = None


class WentOut(_Ev):
    value: Literal["yes", "no", "unknown"] = "unknown"


class SocialContact(_Ev):
    value: list[Literal["family_call", "friend", "neighbour", "centre", "none_mentioned"]] = Field(
        default_factory=lambda: ["none_mentioned"]
    )


class Loneliness(_Ev):
    value: bool = False


class Confusion(_Ev):
    value: list[Literal["repeated_question", "wrong_day_or_date", "lost_thread", "none"]] = Field(
        default_factory=lambda: ["none"]
    )


class MemoryFactOut(BaseModel):
    fact_en: str
    fact_yue: str
    category: Literal["health", "family", "routine", "interest", "event", "concern"]
    expires_in_days: int | None = None


class Extraction(BaseModel):
    mood: Mood = Field(default_factory=Mood)
    sleep: Sleep = Field(default_factory=Sleep)
    appetite: Appetite = Field(default_factory=Appetite)
    meds_taken: MedsTaken = Field(default_factory=MedsTaken)
    pain: Pain = Field(default_factory=Pain)
    went_out_today: WentOut = Field(default_factory=WentOut)
    social_contact: SocialContact = Field(default_factory=SocialContact)
    loneliness_expressed: Loneliness = Field(default_factory=Loneliness)
    confusion_cues: Confusion = Field(default_factory=Confusion)
    concerns: list[str] = Field(default_factory=list)
    new_memory_facts: list[MemoryFactOut] = Field(default_factory=list)
    nudge_response: Literal["interested", "declined", "unclear", "not_offered"] = "not_offered"
    summary_en: str = ""
    summary_yue: str = ""
    overall_confidence: Conf = "low"


class LoginRequest(BaseModel):
    email: str
    password: str
