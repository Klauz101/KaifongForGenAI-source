"""Risk engine (SPEC 9.1): deterministic rules on an older person's own recent history.

`evaluate` is a pure function over `CallRecord`s so each rule is unit-testable; `assess` loads records
from the database, applies the unresolved-Red guard and persists a RiskAssessment.
Green means "no flags", never "safe".
"""

import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import yaml
from sqlmodel import Session, select

from app.models import Alert, Call, CallSummary, RiskAssessment

RES = Path(__file__).resolve().parents[1] / "resources"
HKT = timedelta(hours=8)
RED_RULES = ("RED_FLAG_PHRASE", "SHARP_CHANGE", "UNREACHABLE")
RULE_ORDER = [
    "RED_FLAG_PHRASE",
    "SHARP_CHANGE",
    "UNREACHABLE",
    "PAIN_SEVERE",
    "MOOD_DROP",
    "HOMEBOUND",
    "SKIPPED_MEALS",
    "MEDS_MISSED",
    "POOR_SLEEP",
    "LONELINESS",
    "NEW_CONFUSION",
    "LOW_DATA",
]


@lru_cache
def default_rules() -> dict:
    return yaml.safe_load((RES / "risk_rules.yaml").read_text(encoding="utf-8"))


@lru_cache
def reason_texts() -> dict:
    return yaml.safe_load((RES / "reasons.yaml").read_text(encoding="utf-8"))


@dataclass
class CallRecord:
    """One call as the rules see it. `x` is the extraction dict; None for no-answer/unanalysed calls."""

    call_id: int
    day: date  # Hong Kong local date
    status: str  # completed | safety_escalated | no_answer | ...
    x: dict | None = None
    confidence: str | None = None

    @property
    def answered(self) -> bool:
        return self.status != "no_answer"


@dataclass
class RiskResult:
    level: str
    reasons: list[dict] = field(default_factory=list)
    baseline: dict = field(default_factory=dict)


def _val(rec: CallRecord, key: str) -> object:
    return rec.x[key]["value"] if rec.x and key in rec.x else None


def compute_baseline(records: list[CallRecord], as_of: date, rules: dict) -> dict:
    cfg, d = rules["baseline"], rules["defaults"]
    lo, hi = as_of - timedelta(days=cfg["to_days_ago"]), as_of - timedelta(
        days=cfg["from_days_ago"]
    )
    older = [r for r in records if lo <= r.day <= hi and r.answered and r.x]
    if len(older) < cfg["min_calls"]:
        return {"source": "population_defaults", "calls": len(older), **d}
    moods = [m for r in older if isinstance(m := _val(r, "mood"), int)]
    sleeps = [s for r in older if (s := _val(r, "sleep")) in ("good", "poor")]
    outs = [o for r in older if (o := _val(r, "went_out_today")) in ("yes", "no")]
    return {
        "source": "personal",
        "calls": len(older),
        "mood_median": statistics.median(moods) if moods else d["mood_median"],
        "sleep_mode": Counter(sleeps).most_common(1)[0][0] if sleeps else d["sleep_mode"],
        "went_out_rate": (
            round(sum(o == "yes" for o in outs) / len(outs), 2) if outs else d["went_out_rate"]
        ),
        "confusion_calls": sum(_confused(r) for r in older),
    }


def _confused(rec: CallRecord) -> bool:
    cues = _val(rec, "confusion_cues")
    return bool(cues) and cues != ["none"]


def _reason(code: str, evidence: list[CallRecord], n: int | None = None) -> dict:
    n = len(evidence) if n is None else n
    tpl = reason_texts()[code]
    return {
        "code": code,
        "text_en": tpl["en"].format(n=n),
        "text_zh": tpl["zh"].format(n=n),
        "evidence_call_ids": [r.call_id for r in evidence][-5:],
    }


def evaluate(
    records: list[CallRecord],
    as_of: date,
    rules: dict | None = None,
    red_flag_call_ids: list[int] | None = None,
) -> RiskResult:
    """Apply every rule to `records` (any order) as of the Hong Kong date `as_of`."""
    rules = rules or default_rules()
    window_start = as_of - timedelta(days=rules["window_days"] - 1)
    recs = sorted(
        (r for r in records if window_start <= r.day <= as_of), key=lambda r: (r.day, r.call_id)
    )
    answered = [r for r in recs if r.answered and r.x]  # calls with usable data
    base = compute_baseline(records, as_of, rules)
    hits: dict[str, list[CallRecord]] = {}

    def last(n: int) -> list[CallRecord]:
        return answered[-n:]

    def within(days: int) -> list[CallRecord]:
        return [r for r in answered if r.day > as_of - timedelta(days=days)]

    def consecutive_tail(pred, pool: list[CallRecord]) -> list[CallRecord]:
        tail: list[CallRecord] = []
        for r in reversed(pool):
            if not pred(r):
                break
            tail.append(r)
        return list(reversed(tail))

    # --- Red -------------------------------------------------------------------------------
    cfg = rules["unreachable"]
    tail = consecutive_tail(lambda r: r.status == "no_answer", recs)
    if len(tail) >= cfg["consecutive_calls"]:
        hits["UNREACHABLE"] = tail

    # --- Amber -----------------------------------------------------------------------------
    cfg = rules["pain_severe"]
    ev = [r for r in within(cfg["days"]) if _val(r, "pain") == "severe"]
    if len(ev) >= cfg["min"]:
        hits["PAIN_SEVERE"] = ev

    cfg = rules["mood_drop"]
    ev = [
        r
        for r in last(cfg["of_last"])
        if isinstance(m := _val(r, "mood"), int) and base["mood_median"] - m >= cfg["points"]
    ]
    if len(ev) >= cfg["min"]:
        hits["MOOD_DROP"] = ev

    cfg = rules["homebound"]
    tail = consecutive_tail(lambda r: _val(r, "went_out_today") == "no", answered)
    if (
        len(tail) >= cfg["consecutive_calls"]
        and base["went_out_rate"] >= cfg["baseline_going_out_rate_min"]
    ):
        hits["HOMEBOUND"] = tail

    for code, key, bad in (
        ("SKIPPED_MEALS", "appetite", ("skipped_meals",)),
        ("MEDS_MISSED", "meds_taken", ("no", "partial")),
    ):
        cfg = rules[code.lower()]
        ev = [r for r in within(cfg["days"]) if _val(r, key) in bad]
        if len(ev) >= cfg["min"]:
            hits[code] = ev

    cfg = rules["poor_sleep"]
    ev = [r for r in last(cfg["of_last"]) if _val(r, "sleep") == "poor"]
    if len(ev) >= cfg["min"] and base["sleep_mode"] == "good":
        hits["POOR_SLEEP"] = ev

    cfg = rules["loneliness"]
    ev = [r for r in last(cfg["of_last"]) if _val(r, "loneliness_expressed") is True]
    if len(ev) >= cfg["min"]:
        hits["LONELINESS"] = ev

    cfg = rules["new_confusion"]
    ev = [r for r in last(cfg["of_last"]) if _confused(r)]
    if len(ev) >= cfg["min"] and base["confusion_calls"] == 0:
        hits["NEW_CONFUSION"] = ev

    cfg = rules["low_data"]
    ev = [r for r in last(cfg["of_last"]) if r.confidence == "low"]
    if len(ev) >= cfg["min"]:
        hits["LOW_DATA"] = ev

    # --- Red from combinations / alerts ---------------------------------------------------------
    cfg = rules["sharp_change"]
    low_mood_tail = consecutive_tail(
        lambda r: isinstance(m := _val(r, "mood"), int) and m <= cfg["mood_le"], answered
    )
    if (
        len(hits) - ("UNREACHABLE" in hits) >= cfg["amber_rules_count"]
        or len(low_mood_tail) >= cfg["consecutive_calls"]
    ):
        hits["SHARP_CHANGE"] = (
            low_mood_tail if len(low_mood_tail) >= cfg["consecutive_calls"] else answered[-3:]
        )
    if red_flag_call_ids is not None:  # a red-flag alert exists in the last 24 h (ids may be empty)
        hits["RED_FLAG_PHRASE"] = [r for r in recs if r.call_id in red_flag_call_ids]

    reasons = [_reason(code, hits[code]) for code in RULE_ORDER if code in hits]
    level = "red" if any(c in hits for c in RED_RULES) else "amber" if hits else "green"
    return RiskResult(level, reasons, base)


# ---------------------------------------------------------------------------------------------
# Database layer
# ---------------------------------------------------------------------------------------------


def hk_day(dt: datetime) -> date:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return (dt + HKT).date()


def load_records(db: Session, elder_id: int, as_of: date) -> list[CallRecord]:
    calls = db.exec(
        select(Call)
        .where(Call.elder_id == elder_id, Call.status != "in_progress")
        .order_by(Call.started_at)
    ).all()
    summaries = {
        s.call_id: s
        for s in db.exec(
            select(CallSummary).where(CallSummary.call_id.in_([c.id for c in calls] or [0]))
        )
    }
    out = []
    for c in calls:
        day = hk_day(c.started_at)
        if day > as_of:
            continue
        s = summaries.get(c.id)
        out.append(
            CallRecord(
                c.id,
                day,
                c.status,
                s.extraction if s else None,
                s.overall_confidence if s else None,
            )
        )
    return out


def assess(
    db: Session,
    elder_id: int,
    as_of: date | None = None,
    persist: bool = True,
    now: datetime | None = None,
) -> RiskResult:
    """Compute (and store) the assessment for one person. Never downgrades an unresolved Red alert."""
    now = now or datetime.now(UTC)
    as_of = as_of or hk_day(now)
    rules = default_rules()
    cutoff = now - timedelta(hours=rules["red_flag_phrase"]["hours"])
    alerts = db.exec(select(Alert).where(Alert.elder_id == elder_id)).all()
    flagged = [
        a
        for a in alerts
        if a.type == "red_flag_phrase"
        and (a.resolved_at is None or rules["red_flag_phrase"].get("count_resolved", False))
        and a.created_at.replace(tzinfo=UTC) >= cutoff
        and as_of >= hk_day(a.created_at)
    ]
    result = evaluate(
        load_records(db, elder_id, as_of),
        as_of,
        rules,
        [a.call_id for a in flagged if a.call_id] if flagged else None,
    )
    open_red = next(
        (
            a
            for a in alerts
            if a.level == "red" and a.resolved_at is None and hk_day(a.created_at) <= as_of
        ),
        None,
    )
    if open_red and result.level != "red":
        result.level = "red"
        result.reasons.insert(
            0,
            {
                "code": "OPEN_RED_ALERT",
                "text_en": open_red.reason_en,
                "text_zh": open_red.reason_zh,
                "evidence_call_ids": [open_red.call_id] if open_red.call_id else [],
            },
        )
    if persist:
        row = db.exec(
            select(RiskAssessment).where(
                RiskAssessment.elder_id == elder_id, RiskAssessment.date == as_of.isoformat()
            )
        ).first()
        row = row or RiskAssessment(elder_id=elder_id, date=as_of.isoformat(), level=result.level)
        row.level, row.reasons, row.baseline_snapshot = (
            result.level,
            result.reasons,
            result.baseline,
        )
        db.add(row)
        db.commit()
    return result
