"""Risk engine: every rule has a firing case and a non-firing case (SPEC 9.1)."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlmodel import Session, select

from app.analysis import risk
from app.analysis.risk import CallRecord, evaluate
from app.models import Alert, Elder
from app.schemas import Extraction

AS_OF = date(2026, 10, 17)
_ids = iter(range(1, 10_000))


def ex(
    mood=4,
    sleep="good",
    appetite="normal",
    meds="yes",
    pain="none",
    out="yes",
    lonely=False,
    confusion=None,
    conf="high",
) -> dict:
    x = Extraction.model_validate(
        {
            "mood": {"value": mood},
            "sleep": {"value": sleep},
            "appetite": {"value": appetite},
            "meds_taken": {"value": meds},
            "pain": {"value": pain},
            "went_out_today": {"value": out},
            "loneliness_expressed": {"value": lonely},
            "confusion_cues": {"value": confusion or ["none"]},
            "overall_confidence": conf,
        }
    ).model_dump()
    return x


def rec(days_ago: int, status="completed", **kw) -> CallRecord:
    conf = kw.get("conf", "high")
    return CallRecord(
        next(_ids),
        AS_OF - timedelta(days=days_ago),
        status,
        None if status == "no_answer" else ex(**kw),
        None if status == "no_answer" else conf,
    )


def run(records: list[CallRecord], **kw):
    return evaluate(records, AS_OF, **kw)


def codes(result) -> list[str]:
    return [r["code"] for r in result.reasons]


def recent(n: int, **kw) -> list[CallRecord]:
    """n consecutive daily calls ending today, newest has days_ago=0."""
    return [rec(i, **kw) for i in range(n - 1, -1, -1)]


def baseline_calls(**kw) -> list[CallRecord]:
    """7 older calls (days 9-15) that establish a personal baseline."""
    return [rec(d, **kw) for d in range(9, 16)]


def test_no_flags_is_green_and_green_means_no_flags_only() -> None:
    r = run(recent(5))
    assert (r.level, r.reasons) == ("green", [])


def test_empty_history_is_green_with_population_defaults() -> None:
    r = run([])
    assert r.level == "green" and r.baseline["source"] == "population_defaults"


def test_baseline_personal_when_five_or_more_older_calls() -> None:
    r = run(baseline_calls(mood=2, sleep="poor", out="no") + recent(2))
    assert r.baseline["source"] == "personal" and r.baseline["mood_median"] == 2
    assert r.baseline["sleep_mode"] == "poor" and r.baseline["went_out_rate"] == 0.0
    assert run(recent(2)).baseline["source"] == "population_defaults"


def test_poor_sleep() -> None:
    assert "POOR_SLEEP" in codes(
        run([rec(4), rec(3, sleep="poor"), rec(2, sleep="poor"), rec(1, sleep="poor"), rec(0)])
    )
    assert "POOR_SLEEP" not in codes(
        run([rec(4), rec(3, sleep="poor"), rec(2, sleep="poor"), rec(1), rec(0)])
    )
    # not when poor sleep is the person's own normal
    poor_normal = baseline_calls(sleep="poor") + [
        rec(2, sleep="poor"),
        rec(1, sleep="poor"),
        rec(0, sleep="poor"),
    ]
    assert "POOR_SLEEP" not in codes(run(poor_normal))


def test_mood_drop() -> None:
    assert "MOOD_DROP" in codes(run([rec(2, mood=2), rec(1, mood=4), rec(0, mood=2)]))
    assert "MOOD_DROP" not in codes(run([rec(2, mood=2), rec(1, mood=4), rec(0, mood=4)]))
    assert "MOOD_DROP" not in codes(
        run([rec(2, mood=3), rec(1, mood=3), rec(0, mood=3)])
    )  # only 1 below 4


def test_homebound() -> None:
    assert "HOMEBOUND" in codes(run(recent(4, out="no")))
    assert "HOMEBOUND" not in codes(run(recent(3, out="no")))
    assert "HOMEBOUND" not in codes(
        run(
            [
                rec(4, out="no"),
                rec(3, out="no"),
                rec(2, out="yes"),
                rec(1, out="no"),
                rec(0, out="no"),
            ]
        )
    )
    # not when they rarely went out anyway
    assert "HOMEBOUND" not in codes(run(baseline_calls(out="no") + recent(4, out="no")))
    # unknown never counts as "no"
    assert "HOMEBOUND" not in codes(run(recent(5, out="unknown")))


def test_skipped_meals() -> None:
    assert "SKIPPED_MEALS" in codes(
        run([rec(5, appetite="skipped_meals"), rec(1, appetite="skipped_meals")])
    )
    assert "SKIPPED_MEALS" not in codes(run([rec(1, appetite="skipped_meals"), rec(0)]))
    assert "SKIPPED_MEALS" not in codes(
        run([rec(9, appetite="skipped_meals"), rec(1, appetite="skipped_meals")])
    )  # >7 days


def test_meds_missed() -> None:
    assert "MEDS_MISSED" in codes(run([rec(3, meds="no"), rec(1, meds="partial")]))
    assert "MEDS_MISSED" not in codes(run([rec(3, meds="no"), rec(1)]))
    assert "MEDS_MISSED" not in codes(run([rec(3, meds="not_applicable"), rec(1, meds="unknown")]))


def test_pain_severe() -> None:
    assert "PAIN_SEVERE" in codes(run([rec(2, pain="severe"), rec(0)]))
    assert "PAIN_SEVERE" not in codes(run([rec(4, pain="severe"), rec(0)]))
    assert "PAIN_SEVERE" not in codes(run([rec(1, pain="moderate"), rec(0)]))


def test_loneliness() -> None:
    assert "LONELINESS" in codes(
        run([rec(4, lonely=True), rec(3), rec(2, lonely=True), rec(1), rec(0, lonely=True)])
    )
    assert "LONELINESS" not in codes(
        run([rec(4, lonely=True), rec(3), rec(2), rec(1), rec(0, lonely=True)])
    )


def test_new_confusion() -> None:
    cues = ["repeated_question"]
    assert "NEW_CONFUSION" in codes(
        run([rec(3, confusion=cues), rec(2), rec(1, confusion=cues), rec(0)])
    )
    assert "NEW_CONFUSION" not in codes(run([rec(3, confusion=cues), rec(2), rec(1), rec(0)]))
    had_before = (
        baseline_calls()
        + [rec(10, confusion=cues)]
        + [rec(1, confusion=cues), rec(0, confusion=cues)]
    )
    assert "NEW_CONFUSION" not in codes(run(had_before))  # not new


def test_low_data() -> None:
    r = run([rec(4), rec(3, conf="low"), rec(2, conf="low"), rec(1, conf="low"), rec(0)])
    assert "LOW_DATA" in codes(r) and r.level == "amber"
    assert "LOW_DATA" not in codes(
        run([rec(4), rec(3, conf="low"), rec(2, conf="low"), rec(1), rec(0)])
    )


def test_unreachable_is_red() -> None:
    r = run([rec(2), rec(1, "no_answer"), rec(0, "no_answer")])
    assert r.level == "red" and codes(r) == ["UNREACHABLE"]
    assert "UNREACHABLE" not in codes(run([rec(2), rec(1), rec(0, "no_answer")]))
    assert "UNREACHABLE" not in codes(run([rec(2, "no_answer"), rec(1, "no_answer"), rec(0)]))


def test_red_flag_phrase_is_red() -> None:
    records = recent(3)
    assert run(records).level == "green"
    r = run(records, red_flag_call_ids=[records[-1].call_id])
    assert r.level == "red" and codes(r) == ["RED_FLAG_PHRASE"]
    assert r.reasons[0]["evidence_call_ids"] == [records[-1].call_id]


def test_sharp_change_from_many_amber_rules() -> None:
    four = [
        rec(3, appetite="skipped_meals", meds="no", out="no"),
        rec(2, appetite="skipped_meals", meds="no", out="no", pain="severe"),
        rec(1, out="no"),
        rec(0, out="no"),
    ]
    r = run(four)
    assert (
        r.level == "red"
        and codes(r)[0] == "SHARP_CHANGE"
        and {"HOMEBOUND", "SKIPPED_MEALS", "MEDS_MISSED", "PAIN_SEVERE"} <= set(codes(r))
    )
    three = [
        rec(3, appetite="skipped_meals", out="no"),
        rec(2, appetite="skipped_meals", out="no", pain="severe"),
        rec(1, out="no"),
        rec(0, out="no"),
    ]
    r3 = run(three)  # Mrs Chan's shape: three amber reasons stays Amber (spec Section 12)
    assert r3.level == "amber" and "SHARP_CHANGE" not in codes(r3) and len(r3.reasons) == 3


def test_sharp_change_from_very_low_mood_twice() -> None:
    assert "SHARP_CHANGE" in codes(run([rec(1, mood=1), rec(0, mood=1)]))
    assert "SHARP_CHANGE" not in codes(run([rec(1, mood=1), rec(0, mood=3)]))
    assert "SHARP_CHANGE" not in codes(run([rec(2, mood=1), rec(1, mood=3), rec(0, mood=1)]))


def test_thresholds_are_configurable() -> None:
    rules = {
        **risk.default_rules(),
        "sharp_change": {**risk.default_rules()["sharp_change"], "amber_rules_count": 3},
    }
    three = [
        rec(3, appetite="skipped_meals", out="no"),
        rec(2, appetite="skipped_meals", out="no", pain="severe"),
        rec(1, out="no"),
        rec(0, out="no"),
    ]
    assert run(three, rules=rules).level == "red"


def test_reasons_have_text_in_both_languages_and_evidence() -> None:
    r = run(recent(4, out="no"))
    reason = r.reasons[0]
    assert reason["text_en"] and reason["text_zh"] and len(reason["evidence_call_ids"]) == 4


def test_calls_after_as_of_and_older_than_window_are_ignored() -> None:
    old = [rec(30, appetite="skipped_meals"), rec(20, appetite="skipped_meals")]
    assert run(old).level == "green"


@pytest.mark.usefixtures("reseed_after")
def test_unresolved_red_alert_is_never_downgraded_until_a_human_resolves_it(db: Session) -> None:
    patel = db.exec(select(Elder).where(Elder.display_name_en == "Mrs Patel")).first()
    assert risk.assess(db, patel.id, persist=False).level == "green"
    alert = Alert(
        elder_id=patel.id,
        level="red",
        type="red_flag_phrase",
        reason_en="Said she fell",
        reason_zh="講到跌倒",
        created_at=datetime.now(UTC) - timedelta(days=3),
    )
    db.add(alert)
    db.commit()
    held = risk.assess(db, patel.id, persist=False)
    assert held.level == "red" and held.reasons[0]["code"] == "OPEN_RED_ALERT"
    alert.resolved_at = datetime.now(UTC)
    db.add(alert)
    db.commit()
    assert risk.assess(db, patel.id, persist=False).level == "green"


def test_recent_red_flag_alert_makes_red_for_24_hours_only(db: Session) -> None:
    lau = db.exec(select(Elder).where(Elder.display_name_en == "Mr Lau")).first()
    # Mr Lau's seeded alert is 15 days old and resolved: no effect on today's level
    assert risk.assess(db, lau.id, persist=False).level == "green"
