"""Weekly family digest (SPEC 10.1): warm, short, EN + 中文. Built only from structured summaries.

It never contains transcripts, diagnoses or clinical detail (pain, medication, confusion): a worried
or lonely week is described gently and the family is encouraged to call. Only consented people get one.
"""

from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import yaml
from sqlmodel import Session, select

from app.analysis import memory, risk
from app.models import Call, CallSummary, Consent, Elder, FamilyDigest, Nudge

RES = Path(__file__).resolve().parents[1] / "resources"
# Only light, positive things to talk about: never family tensions or health matters.
WARM_FACTS = ("event", "interest")


@lru_cache
def lines() -> dict:
    return yaml.safe_load((RES / "digest.yaml").read_text(encoding="utf-8"))


def digest_consented(db: Session, elder_id: int) -> bool:
    c = db.exec(
        select(Consent).where(Consent.elder_id == elder_id).order_by(Consent.consented_at.desc())
    ).first()
    return bool(c and c.scope_family_digest and c.withdrawn_at is None)


def _week(db: Session, elder_id: int, end_day) -> tuple[list[dict], list[Nudge], int]:
    start = end_day - timedelta(days=6)
    calls = [
        c
        for c in db.exec(select(Call).where(Call.elder_id == elder_id))
        if c.status != "no_answer" and start <= risk.hk_day(c.started_at) <= end_day
    ]
    ids = {c.id for c in calls}
    xs = [s.extraction for s in db.exec(select(CallSummary)) if s.call_id in ids]
    nudges = [
        n for n in db.exec(select(Nudge).where(Nudge.elder_id == elder_id)) if n.call_id in ids
    ]
    chat_days = len(
        {risk.hk_day(c.started_at) for c in calls}
    )  # days, not calls: two calls = one day
    return xs, nudges, chat_days


def compose(
    lang: str,
    name: str,
    start: str,
    end: str,
    xs: list[dict],
    nudges: list[Nudge],
    lonely: bool,
    watched: bool,
    facts: list[str],
    preferred_time: str,
    chat_days: int | None = None,
) -> str:
    t = lines()[lang]
    parts = [
        t["intro"].format(name=name, start=start, end=end),
        t["chats"].format(name=name, n=len(xs) if chat_days is None else chat_days),
    ]
    moods = [x["mood"]["value"] for x in xs if isinstance(x["mood"]["value"], int)]
    if moods:
        avg = sum(moods) / len(moods)
        parts.append(
            t["mood_high" if avg >= 4 else "mood_mid" if avg >= 3 else "mood_low"].format(name=name)
        )
    outs = [
        x["went_out_today"]["value"] for x in xs if x["went_out_today"]["value"] in ("yes", "no")
    ]
    if outs:
        yes = outs.count("yes")
        parts.append(
            t["out_some"].format(name=name, k=yes)
            if yes >= len(outs) / 2
            else t["out_less"].format(name=name)
        )
    meals = [x["appetite"]["value"] for x in xs if x["appetite"]["value"] != "unknown"]
    if meals and meals.count("normal") >= len(meals) * 0.7:
        parts.append(t["meals_ok"])
    for n in nudges:
        if n.elder_response == "interested":
            key = "nudge_done" if n.followed_up else "nudge_accepted"
            parts.append(t[key].format(name=name, what=t["nudge_kinds"][n.type]))
            break
    if lonely:
        parts.append(t["lonely"].format(name=name))
    if watched:
        parts.append(t["care_team"])
    if facts:
        parts.append(t["ask_about"].format(facts=("; " if lang == "en" else "；").join(facts)))
    morning = int(preferred_time.split(":")[0]) < 12
    parts.append(
        t["call_time"].format(when=t["when_morning_call" if morning else "when_afternoon_call"])
    )
    parts.append(t["closing"])
    return "\n\n".join(parts)


def generate(db: Session, elder: Elder, now: datetime | None = None) -> FamilyDigest:
    """Create (or replace this week's) digest. Caller must have checked consent."""
    now = now or datetime.now(UTC)
    end = risk.hk_day(now)
    start = end - timedelta(days=6)
    xs, nudges, chat_days = _week(db, elder.id, end)
    lonely = any(x["loneliness_expressed"]["value"] for x in xs)
    result = risk.assess(db, elder.id, as_of=end, persist=False, now=now)
    watched = result.level in ("amber", "red")
    warm = [
        f
        for f in memory.retrieve_facts(db, elder.id, limit=5, now=now)
        if f["category"] in WARM_FACTS
    ][:2]
    texts = {}
    for lang, name in (("en", elder.display_name_en), ("yue", elder.display_name)):
        facts = [f["en" if lang == "en" else "yue"] for f in warm]
        texts[lang] = compose(
            lang,
            name,
            start.isoformat(),
            end.isoformat(),
            xs,
            nudges,
            lonely,
            watched,
            facts,
            elder.preferred_call_time,
            chat_days,
        )
    row = db.exec(
        select(FamilyDigest).where(
            FamilyDigest.elder_id == elder.id, FamilyDigest.week_start == start.isoformat()
        )
    ).first()
    row = row or FamilyDigest(
        elder_id=elder.id, week_start=start.isoformat(), text_en="", text_zh=""
    )
    row.text_en, row.text_zh, row.generated_at, row.sent_simulated_at = (
        texts["en"],
        texts["yue"],
        now,
        None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
