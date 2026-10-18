"""SQLModel tables (SPEC Section 5). Timestamps are timezone-aware UTC. Sensitive text uses EncryptedStr."""

from datetime import UTC, datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from app.security.crypto import EncryptedStr


def utcnow() -> datetime:
    return datetime.now(UTC)


def enc() -> Column:
    return Column(EncryptedStr)


def js() -> Column:
    return Column(JSON)


class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    email: str = Field(index=True, unique=True)
    password_hash: str
    role: str  # admin | social_worker | family
    linked_elder_ids: list[int] = Field(default_factory=list, sa_column=js())
    created_at: datetime = Field(default_factory=utcnow)


class Elder(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    display_name: str  # how Kaifong addresses them, e.g. 陳婆婆
    display_name_en: str  # e.g. "Mrs Chan" (added for non-Cantonese team members)
    full_name: str = Field(sa_column=enc())
    phone: str = Field(sa_column=enc())
    age: int
    gender: str
    district: str
    language: str = "yue"  # yue | en
    preferred_call_time: str = "09:30"
    caseworker_id: int | None = Field(default=None, foreign_key="user.id")
    status: str = "active"  # active | paused | withdrawn
    interests: list[str] = Field(default_factory=list, sa_column=js())
    care_notes: str | None = Field(default=None, sa_column=enc())
    is_synthetic: bool = True
    created_at: datetime = Field(default_factory=utcnow)


class Consent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    elder_id: int = Field(foreign_key="elder.id", index=True)
    method: str  # verbal_and_written | guardian
    witnessed_by: str
    consented_at: datetime
    scope_calls: bool = True
    scope_family_digest: bool = False
    scope_data_retention_days: int = 180
    withdrawn_at: datetime | None = None


class Call(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    elder_id: int = Field(foreign_key="elder.id", index=True)
    started_at: datetime
    ended_at: datetime | None = None
    status: str = "in_progress"  # in_progress|completed|no_answer|aborted|safety_escalated
    mode: str = "simulator"  # simulator | phone | seed
    language: str = "yue"
    duration_s: int = 0
    turn_count: int = 0
    safety_triggered: bool = False
    avg_latency_ms: int | None = None
    providers_used: dict = Field(default_factory=dict, sa_column=js())
    usage: dict = Field(default_factory=dict, sa_column=js())  # metered usage for cost estimates


class Turn(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    call_id: int = Field(foreign_key="call.id", index=True)
    index: int
    speaker: str  # elder | kaifong
    text: str = Field(sa_column=enc())
    text_en: str | None = Field(default=None, sa_column=enc())
    stt_confidence: float | None = None
    is_scripted_safety: bool = False
    latency_breakdown: dict = Field(default_factory=dict, sa_column=js())
    created_at: datetime = Field(default_factory=utcnow)


class CallSummary(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    call_id: int = Field(foreign_key="call.id", unique=True)
    extraction: dict = Field(default_factory=dict, sa_column=js())
    summary_yue: str = Field(sa_column=enc())
    summary_en: str = Field(sa_column=enc())
    overall_confidence: str = "medium"
    model_used: str = "seed"
    created_at: datetime = Field(default_factory=utcnow)


class MemoryFact(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    elder_id: int = Field(foreign_key="elder.id", index=True)
    fact: str = Field(sa_column=enc())  # English canonical
    fact_yue: str = Field(sa_column=enc())
    category: str  # health|family|routine|interest|event|concern
    source_call_id: int | None = Field(default=None, foreign_key="call.id")
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime | None = None
    active: bool = True


class RiskAssessment(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    elder_id: int = Field(foreign_key="elder.id", index=True)
    date: str  # ISO date, Hong Kong local day
    level: str  # green | amber | red
    reasons: list[dict] = Field(default_factory=list, sa_column=js())
    baseline_snapshot: dict = Field(default_factory=dict, sa_column=js())
    created_at: datetime = Field(default_factory=utcnow)


class Alert(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    elder_id: int = Field(foreign_key="elder.id", index=True)
    call_id: int | None = Field(default=None, foreign_key="call.id")
    level: str
    type: str  # red_flag_phrase | unreachable | trend | opt_out_request
    reason_en: str
    reason_zh: str
    created_at: datetime = Field(default_factory=utcnow)
    acknowledged_by: int | None = Field(default=None, foreign_key="user.id")
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None
    outcome: str | None = None
    notes: str | None = Field(default=None, sa_column=enc())


class ActionLog(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    elder_id: int = Field(foreign_key="elder.id", index=True)
    alert_id: int | None = Field(default=None, foreign_key="alert.id")
    user_id: int = Field(foreign_key="user.id")
    action: str  # home_visit|phone_call|referral|family_contacted|centre_activity_arranged|no_action_needed
    notes: str | None = Field(default=None, sa_column=enc())
    created_at: datetime = Field(default_factory=utcnow)


class CommunityResource(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    name_zh: str
    type: str  # DECC | NEC | activity | hotline
    district: str
    address: str
    phone: str
    activities: list[str] = Field(default_factory=list, sa_column=js())
    schedule_text: str = ""
    verified: bool = False


class Nudge(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    call_id: int = Field(foreign_key="call.id")
    elder_id: int = Field(foreign_key="elder.id", index=True)
    type: str  # centre_activity | call_family | meet_friend | go_outside
    resource_id: int | None = Field(default=None, foreign_key="communityresource.id")
    text_yue: str
    text_en: str
    elder_response: str = "unclear"  # interested | declined | unclear
    followed_up: bool | None = None


class FamilyDigest(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    elder_id: int = Field(foreign_key="elder.id", index=True)
    week_start: str
    text_en: str
    text_zh: str
    generated_at: datetime = Field(default_factory=utcnow)
    sent_simulated_at: datetime | None = None


class AuditLog(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int | None = Field(default=None, foreign_key="user.id")
    action: str
    entity: str
    entity_id: int | None = None
    created_at: datetime = Field(default_factory=utcnow)
