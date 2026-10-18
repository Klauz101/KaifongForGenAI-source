"""Create synthetic demo data: 5 users, 8 elders, 21 days of calls, summaries, risk history.

Offline and deterministic. Every elder is_synthetic=True. Run with `make seed`.
"""

import logging
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml
from sqlmodel import Session, SQLModel

from app import models as m
from app.analysis import digest, risk
from app.config import get_settings
from app.db import engine, init_db
from app.nudges import nudges
from app.schemas import Extraction
from app.security.auth import hash_password
from app.seed.scenarios import PERSONAS, DaySpec, Persona

logger = logging.getLogger(__name__)
RES = Path(__file__).resolve().parents[1] / "resources"
HKT = timedelta(hours=8)
DAYS = 21
DEMO_PASSWORD = "demo1234"
LOC_YUE = {"knee": "膝頭哥", "back": "背脊", "leg": "腳"}

# Nudge scenario: persona key -> {days_ago: (type, response, followed_up)}
NUDGES = {
    "ho": {
        11: ("call_family", "interested", True),
        5: ("centre_activity", "interested", True),
        1: ("go_outside", "declined", None),
    },
    "chan": {8: ("go_outside", "unclear", None)},
}
NUDGE_TEXT = {
    "call_family": ("不如打俾個仔傾下偈？", "Maybe give your son a call for a chat?"),
    "centre_activity": (
        "你鍾意織冷，不如我叫你嘅社工幫你問下附近長者中心有冇相關活動呀？",
        "You enjoy knitting. Shall I ask your social worker to find out whether a nearby centre has something like it?",
    ),
    "go_outside": (
        "今日天氣好，落樓下行下好唔好？",
        "The weather is nice. Shall we go for a short walk downstairs?",
    ),
}


def _yaml(name: str) -> dict:
    return yaml.safe_load((RES / name).read_text(encoding="utf-8"))


def _fmt(item: dict, lang: str, **kw: str) -> tuple[str, str]:
    safe = {"loc": "knee", "loc_yue": "膝頭哥", **kw}
    safe_en = {**safe, "name": kw.get("name_en", kw.get("name", ""))}
    return item[lang].format_map(safe), item["en"].format_map(safe_en)


def build_transcript(
    p: Persona, spec: DaySpec, rng: random.Random, tpl: dict
) -> tuple[list[dict], dict[str, list[int]], str | None]:
    """Return (turns, evidence by topic, neighbour-contact topic or None)."""
    lang = p.language
    kw = {
        "name": p.display_name if lang == "yue" else p.display_name_en,
        "ngo": get_settings().ngo_name,
        "time": p.call_time,
    }
    if spec.pain_loc:
        kw["loc"] = spec.pain_loc
        kw["loc_yue"] = LOC_YUE.get(spec.pain_loc, spec.pain_loc)
    turns: list[dict] = []
    evidence: dict[str, list[int]] = {}

    def add(speaker: str, item: dict, topic: str | None = None, conf: float | None = None) -> int:
        text, text_en = _fmt(item, lang, **kw)
        turns.append({"speaker": speaker, "text": text, "text_en": text_en, "stt_confidence": conf})
        if topic:
            evidence.setdefault(topic, []).append(len(turns) - 1)
        return len(turns) - 1

    low = 0.35 if spec.hard_to_hear else None

    def elder(topic: str, items: list[dict]) -> int:
        if spec.hard_to_hear:
            return add("elder", rng.choice(tpl["unclear"]["a"]), None, low)
        return add("elder", rng.choice(items), topic, round(rng.uniform(0.82, 0.96), 2))

    add("kaifong", tpl["greeting"]["kaifong"][0])
    elder("greeting", tpl["greeting"]["elder"])
    mood_key = (
        "lonely"
        if spec.lonely
        else "high" if (spec.mood or 0) >= 4 else "mid" if spec.mood == 3 else "low"
    )
    topics = [
        ("appetite", tpl["appetite"].get(spec.appetite)),
        ("sleep", tpl["sleep"].get(spec.sleep)),
        ("meds", tpl["meds"].get(spec.meds)),
        ("pain", tpl["pain"].get(spec.pain)),
        ("went_out", tpl["went_out"].get(spec.went_out)),
        ("mood", tpl["mood"][mood_key]),
    ]
    neighbour = None
    for topic, block in topics:
        if block is None or (spec.hard_to_hear and topic not in ("appetite", "sleep", "mood")):
            continue
        add("kaifong", rng.choice(block["q"]))
        answer = rng.choice(block["a"])
        idx = elder(topic, [answer])
        if (
            topic == "went_out"
            and spec.went_out == "yes"
            and answer is block["a"][0]
            and not spec.hard_to_hear
        ):
            neighbour = "went_out"
        if topic == "pain" and spec.pain == "severe":
            add("kaifong", tpl["pain"]["severe_followup"]["q"][0])
        del idx
    if "wrong_day_or_date" in spec.confusion or "repeated_question" in spec.confusion:
        add("elder", tpl["confusion"][spec.confusion[0]]["a"][0], "confusion", 0.9)
    add("kaifong", tpl["close"]["kaifong"][0])
    elder("close", tpl["close"]["elder"])
    return turns, evidence, neighbour


def build_extraction(
    spec: DaySpec, ev: dict[str, list[int]], neighbour: str | None, nudge: str
) -> Extraction:
    conf = spec.confidence
    e = lambda k: ev.get(k, [])
    contact = ["neighbour"] if neighbour else spec.contact if not ev else ["none_mentioned"]
    return Extraction.model_validate(
        {
            "mood": {"value": spec.mood, "confidence": conf, "evidence_turns": e("mood")},
            "sleep": {"value": spec.sleep, "confidence": conf, "evidence_turns": e("sleep")},
            "appetite": {
                "value": spec.appetite,
                "confidence": conf,
                "evidence_turns": e("appetite"),
            },
            "meds_taken": {"value": spec.meds, "confidence": conf, "evidence_turns": e("meds")},
            "pain": {
                "value": spec.pain,
                "location": spec.pain_loc if spec.pain not in ("none", "unknown") else None,
                "evidence_turns": e("pain"),
            },
            "went_out_today": {"value": spec.went_out, "evidence_turns": e("went_out")},
            "social_contact": {
                "value": contact,
                "evidence_turns": e("went_out") if neighbour else [],
            },
            "loneliness_expressed": {
                "value": spec.lonely,
                "evidence_turns": e("mood") if spec.lonely else [],
            },
            "confusion_cues": {"value": spec.confusion, "evidence_turns": e("confusion")},
            "concerns": _concerns(spec),
            "nudge_response": nudge,
            "overall_confidence": conf,
        }
    )


def _concerns(spec: DaySpec) -> list[str]:
    out = []
    if spec.appetite == "skipped_meals":
        out.append("Skipped a meal")
    if spec.pain in ("moderate", "severe"):
        out.append(f"{spec.pain.capitalize()} pain ({spec.pain_loc})")
    if spec.lonely:
        out.append("Feels lonely")
    if "none" not in spec.confusion:
        out.append("Signs of confusion")
    return out


def build_summary(spec: DaySpec, snippets: dict) -> tuple[str, str]:
    flags: list[str] = []
    if spec.hard_to_hear:
        flags.append("hard_to_hear")
    else:
        if spec.appetite == "skipped_meals":
            flags.append("skipped_meals")
        if spec.went_out == "no":
            flags.append("homebound")
        if spec.pain in ("moderate", "severe"):
            flags.append("pain")
        if spec.sleep == "poor":
            flags.append("poor_sleep")
        if spec.lonely:
            flags.append("lonely")
        if "none" not in spec.confusion:
            flags.append("confusion")
        if (spec.mood or 5) <= 2:
            flags.append("low_mood")
        if spec.meds in ("no", "partial"):
            flags.append("meds_missed")
    flags = flags or ["stable"]
    loc = spec.pain_loc or "body"
    loc_yue = LOC_YUE.get(loc, loc)
    en = " ".join(snippets[f]["en"].format(loc=loc) for f in flags)
    yue = "".join(snippets[f]["yue"].format(loc_yue=loc_yue) for f in flags)
    return en, yue


def seed_all(session: Session, now: datetime | None = None) -> dict[str, str]:
    """Populate an empty DB. Returns demo logins."""
    now = now or m.utcnow()
    today_hk = (now + HKT).date()
    tpl, snippets = _yaml("transcript_templates.yaml"), _yaml("summary_snippets.yaml")

    def user(name: str, email: str, role: str, linked: list[int] | None = None) -> m.User:
        u = m.User(
            name=name,
            email=email,
            password_hash=hash_password(DEMO_PASSWORD),
            role=role,
            linked_elder_ids=linked or [],
        )
        session.add(u)
        session.commit()
        session.refresh(u)
        return u

    admin = user("Demo Admin", "admin@demo.kaifong", "admin")
    worker = user("Demo Social Worker A", "worker@demo.kaifong", "social_worker")
    user("Demo Social Worker B", "worker2@demo.kaifong", "social_worker")  # empty caseload
    elders: dict[str, m.Elder] = {}

    for p in PERSONAS:
        e = m.Elder(
            display_name=p.display_name,
            display_name_en=p.display_name_en,
            full_name=p.full_name,
            phone=p.phone,
            age=p.age,
            gender=p.gender,
            district="Kwun Tong",
            language=p.language,
            preferred_call_time=p.call_time,
            caseworker_id=worker.id,
            interests=p.interests,
            care_notes=p.care_notes,
            is_synthetic=True,
        )
        session.add(e)
        session.commit()
        session.refresh(e)
        elders[p.key] = e
        session.add(
            m.Consent(
                elder_id=e.id,
                method="verbal_and_written",
                witnessed_by="Demo Staff",
                consented_at=now - timedelta(days=DAYS + 5),
                scope_family_digest=p.family_digest,
            )
        )
        for en, yue, cat in p.facts:
            session.add(m.MemoryFact(elder_id=e.id, fact=en, fact_yue=yue, category=cat))

    nudges.seed_resources(session)
    user("Demo Family (Chan)", "family@demo.kaifong", "family", [elders["chan"].id])
    user("Demo Family (Ho)", "family2@demo.kaifong", "family", [elders["ho"].id])

    for p in PERSONAS:
        _seed_elder(session, p, elders[p.key], worker, now, today_hk, tpl, snippets)
    session.commit()
    for key in ("chan", "ho"):  # people with family accounts: a digest, already "sent" (simulated)
        row = digest.generate(session, elders[key], now)
        row.sent_simulated_at = now
        session.add(row)
    session.commit()
    del admin
    return {
        r: f"{r}@demo.kaifong / {DEMO_PASSWORD}"
        for r in ("admin", "worker", "worker2", "family", "family2")
    }


def _seed_elder(
    session: Session,
    p: Persona,
    e: m.Elder,
    worker: m.User,
    now: datetime,
    today_hk,
    tpl: dict,
    snippets: dict,
) -> None:
    rng = random.Random(p.key)
    specs = {d: p.day(d, rng) for d in range(DAYS)}
    call_ids: dict[int, int] = {}
    nudges = NUDGES.get(p.key, {})
    hh, mm = map(int, p.call_time.split(":"))

    for d in range(DAYS - 1, -1, -1):
        spec = specs[d]
        local = datetime.combine(today_hk - timedelta(days=d), datetime.min.time()) + timedelta(
            hours=hh, minutes=mm
        )
        started = (local - HKT).replace(tzinfo=UTC)
        if started > now:
            started = now - timedelta(minutes=10 + rng.randint(0, 40))
        if d == 0:  # seeding shortly after midnight HKT: keep "today's" calls on today's date
            day_start = (datetime.combine(today_hk, datetime.min.time()) - HKT).replace(tzinfo=UTC)
            started = max(started, day_start + timedelta(minutes=1))
        if spec.status == "no_answer":
            call = m.Call(
                elder_id=e.id,
                started_at=started,
                ended_at=started + timedelta(seconds=30),
                status="no_answer",
                mode="seed",
                language=p.language,
            )
            session.add(call)
            session.commit()
            session.refresh(call)
            call_ids[d] = call.id
            continue
        transcribed = d <= 2
        turns, ev, neighbour = (
            build_transcript(p, spec, rng, tpl) if transcribed else ([], {}, None)
        )
        nudge = nudges.get(d)
        duration = rng.randint(190, 280)
        latency = rng.randint(1700, 2700)
        call = m.Call(
            elder_id=e.id,
            started_at=started,
            ended_at=started + timedelta(seconds=duration),
            status="completed",
            mode="seed",
            language=p.language,
            duration_s=duration,
            turn_count=len(turns) or rng.randint(12, 16),
            avg_latency_ms=latency,
            providers_used={"stt": "seed", "tts": "seed", "llm": "seed"},
        )
        session.add(call)
        session.commit()
        session.refresh(call)
        call_ids[d] = call.id
        for i, t in enumerate(turns):
            lat = (
                {
                    "stt_ms": rng.randint(300, 700),
                    "llm_ms": rng.randint(700, 1300),
                    "tts_ms": rng.randint(300, 600),
                }
                if t["speaker"] == "kaifong"
                else {}
            )
            session.add(
                m.Turn(
                    call_id=call.id,
                    index=i,
                    created_at=started + timedelta(seconds=i * 15),
                    latency_breakdown=lat,
                    **t,
                )
            )
        extraction = build_extraction(spec, ev, neighbour, nudge[1] if nudge else "not_offered")
        en, yue = build_summary(spec, snippets)
        extraction.summary_en, extraction.summary_yue = en, yue
        session.add(
            m.CallSummary(
                call_id=call.id,
                extraction=extraction.model_dump(),
                summary_en=en,
                summary_yue=yue,
                overall_confidence=spec.confidence,
            )
        )
        if nudge:
            ntype, resp, followed = nudge
            yue_t, en_t = NUDGE_TEXT[ntype]
            session.add(
                m.Nudge(
                    call_id=call.id,
                    elder_id=e.id,
                    type=ntype,
                    text_yue=yue_t,
                    text_en=en_t,
                    elder_response=resp,
                    followed_up=followed,
                )
            )
            if followed:
                action = (
                    "family_contacted" if ntype == "call_family" else "centre_activity_arranged"
                )
                session.add(
                    m.ActionLog(
                        elder_id=e.id,
                        user_id=worker.id,
                        action=action,
                        notes="Synthetic: follow-up after Kaifong nudge.",
                        created_at=started + timedelta(days=1),
                    )
                )
    session.commit()

    # Risk history comes from the real rules engine, as of each day, so the dashboard and the
    # engine can never disagree (SPEC 9.1). Alerts are seeded after, so history ignores them.
    for d in range(13, -1, -1):
        risk.assess(session, e.id, as_of=today_hk - timedelta(days=d), now=now - timedelta(days=d))
    _seed_alerts(session, p, e, worker, now, call_ids)
    session.commit()


def _seed_alerts(
    session: Session,
    p: Persona,
    e: m.Elder,
    worker: m.User,
    now: datetime,
    call_ids: dict[int, int],
) -> None:
    if p.key == "cheung":
        session.add(
            m.Alert(
                elder_id=e.id,
                call_id=call_ids[0],
                level="red",
                type="unreachable",
                reason_en="Missed 2 scheduled calls in a row",
                reason_zh="連續 2 次冇人接聽",
                created_at=now - timedelta(minutes=20),
            )
        )
    if p.key == "lau":
        created = now - timedelta(days=15)
        alert = m.Alert(
            elder_id=e.id,
            call_id=call_ids[15],
            level="red",
            type="red_flag_phrase",
            reason_en="Said they slipped in the bathroom (fall)",
            reason_zh="講到喺浴室跣親（跌倒）",
            created_at=created,
            acknowledged_by=worker.id,
            acknowledged_at=created + timedelta(minutes=4),
            resolved_at=created + timedelta(hours=3),
            outcome="home_visit",
            notes="Synthetic: visited, minor bruise, grab rail requested.",
        )
        session.add(alert)
        session.commit()
        session.refresh(alert)
        session.add(
            m.ActionLog(
                elder_id=e.id,
                alert_id=alert.id,
                user_id=worker.id,
                action="home_visit",
                notes="Synthetic: visited same day.",
                created_at=created + timedelta(hours=2),
            )
        )


def reset_and_seed() -> dict[str, str]:
    from app.models import AuditLog  # noqa: F401

    SQLModel.metadata.drop_all(engine)
    init_db()
    with Session(engine) as session:
        return seed_all(session)


if __name__ == "__main__":
    logins = reset_and_seed()
    print("Seeded synthetic demo data. Demo logins:")
    for line in logins.values():
        print(f"  {line}")
