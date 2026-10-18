"""Phase 5: nudges, community resources, follow-through tracking, and the family digest."""

import pytest
from sqlmodel import Session, select

from app.models import (
    AuditLog,
    Call,
    CommunityResource,
    Consent,
    Elder,
    FamilyDigest,
    Nudge,
    Turn,
)
from app.nudges import nudges as nudge_service

pytestmark = pytest.mark.usefixtures("reseed_after")


def person(client, name: str) -> int:
    return next(
        c["id"] for c in client.get("/api/today").json()["cards"] if c["display_name_en"] == name
    )


def elder(db: Session, name: str) -> Elder:
    return db.exec(select(Elder).where(Elder.display_name_en == name)).first()


def run_call(client, eid: int, lines: list[str]) -> int:
    cid = client.post("/api/calls", json={"elder_id": eid}).json()["call_id"]
    for text in lines:
        events = client.post(f"/api/calls/{cid}/turn", json={"text": text}).json()["events"]
        if any(e["type"] == "call.ended" for e in events):
            return cid
    client.post(f"/api/calls/{cid}/end")
    return cid


# ---------- community resources -------------------------------------------------------------------


def test_sample_resources_are_clearly_placeholders() -> None:
    items = nudge_service.load_resource_file()
    assert 6 <= len(items) <= 8
    assert all(r["verified"] is False for r in items)
    assert all("PLACEHOLDER" in r["address"] or r["address"] == "n/a" for r in items)
    assert all("X" in r["phone"] for r in items)  # no real-looking numbers
    raw = (nudge_service.HERE / "community_resources.json").read_text(encoding="utf-8")
    assert "TODO" in raw


def test_resources_are_loaded_once_into_the_database(db: Session) -> None:
    assert len(db.exec(select(CommunityResource)).all()) == len(nudge_service.load_resource_file())
    assert nudge_service.seed_resources(db) == 0  # idempotent


# ---------- choosing a nudge ---------------------------------------------------------------------


def test_homebound_person_gets_a_centre_activity_matching_their_interests(db: Session) -> None:
    chan = nudge_service.choose_nudge(db, elder(db, "Mrs Chan"))  # HOMEBOUND; likes Cantonese opera
    assert chan["kind"] == "centre_activity" and chan["resource_id"]
    res = db.get(CommunityResource, chan["resource_id"])
    assert "Cantonese opera" in res.activities
    # unverified placeholder: offers to ask the social worker, never claims the activity exists
    assert "social worker" in chan["idea"]["en"] and res.name not in chan["idea"]["en"]
    assert "社工" in chan["idea"]["yue"] and res.name_zh not in chan["idea"]["yue"]


def test_verified_resource_is_named(db: Session) -> None:
    res = db.get(
        CommunityResource, nudge_service.choose_nudge(db, elder(db, "Mrs Chan"))["resource_id"]
    )
    res.verified = True
    db.add(res)
    db.commit()
    try:
        idea = nudge_service.choose_nudge(db, elder(db, "Mrs Chan"))["idea"]
        assert res.name in idea["en"] and res.name_zh in idea["yue"]
    finally:
        res.verified = False
        db.add(res)
        db.commit()


def test_lonely_person_is_nudged_toward_family_and_kinds_rotate(db: Session) -> None:
    ho = nudge_service.choose_nudge(db, elder(db, "Mrs Ho"))  # LONELINESS + MOOD_DROP
    assert ho["kind"] == "call_family" and ho["resource_id"] is None
    lee = elder(db, "Mr Lee")  # no flags: rotate, never repeat the last two kinds
    first = nudge_service.choose_nudge(db, lee)["kind"]
    db.add(
        Nudge(
            call_id=db.exec(select(Call).where(Call.elder_id == lee.id)).first().id,
            elder_id=lee.id,
            type=first,
            text_yue="x",
            text_en="x",
        )
    )
    db.commit()
    assert nudge_service.choose_nudge(db, lee)["kind"] != first


def test_no_resources_falls_back_to_a_generic_offer(db: Session) -> None:
    saved = [r.model_dump() for r in db.exec(select(CommunityResource))]
    for r in db.exec(select(CommunityResource)):
        db.delete(r)
    db.commit()
    try:
        idea = nudge_service.choose_nudge(db, elder(db, "Mrs Chan"))["idea"]
        assert "social worker" in idea["en"]
    finally:
        for r in saved:
            db.add(CommunityResource(**r))
        db.commit()


# ---------- in a real call ---------------------------------------------------------------------


def test_the_call_makes_the_chosen_nudge_and_records_its_resource(login, db: Session) -> None:
    worker = login("worker@demo.kaifong")
    chan = person(worker, "Mrs Chan")
    cid = run_call(worker, chan, ["早晨", "好", "冇痛", "開心", "出咗街", "好呀，我想試", "拜拜"])
    db.expire_all()
    n = db.exec(select(Nudge).where(Nudge.call_id == cid)).first()
    assert n.type == "centre_activity" and n.resource_id is not None
    assert "社工" in n.text_yue and "social worker" in n.text_en
    turns = db.exec(select(Turn).where(Turn.call_id == cid, Turn.speaker == "kaifong")).all()
    assert any("社工" in t.text for t in turns)  # the person actually heard it


# ---------- follow-through ---------------------------------------------------------------------


def seeded_call(db: Session, e: Elder, days_ago: int) -> Call:
    return db.exec(
        select(Call)
        .where(Call.elder_id == e.id, Call.mode == "seed")
        .order_by(Call.started_at.desc())
        .offset(days_ago)
    ).first()


def test_logging_a_family_call_completes_a_call_family_nudge(login, db: Session) -> None:
    worker = login("worker@demo.kaifong")
    lee = elder(db, "Mr Lee")
    n = Nudge(
        call_id=seeded_call(db, lee, 0).id,
        elder_id=lee.id,
        type="call_family",
        text_yue="x",
        text_en="x",
        elder_response="interested",
        followed_up=None,
    )
    db.add(n)
    db.commit()
    assert (
        worker.post(f"/api/elders/{lee.id}/actions", json={"action": "home_visit"}).status_code
        == 200
    )
    db.refresh(n)
    assert n.followed_up is None  # a different kind of action is not follow-through
    assert (
        worker.post(
            f"/api/elders/{lee.id}/actions",
            json={"action": "family_contacted", "notes": "Son called"},
        ).status_code
        == 200
    )
    db.refresh(n)
    assert n.followed_up is True


def test_later_calls_showing_contact_complete_a_nudge_and_silence_marks_it_not_followed(
    db: Session,
) -> None:
    lee = elder(db, "Mr Lee")
    walk = Nudge(
        call_id=seeded_call(db, lee, 10).id,
        elder_id=lee.id,
        type="go_outside",
        text_yue="x",
        text_en="x",
        elder_response="interested",
        followed_up=None,
    )  # later seeded calls: went out
    old_friend = Nudge(
        call_id=seeded_call(db, lee, 20).id,
        elder_id=lee.id,
        type="centre_activity",
        text_yue="x",
        text_en="x",
        elder_response="interested",
        followed_up=None,
    )  # >14 days, no centre contact
    db.add(walk)
    db.add(old_friend)
    db.commit()
    nudge_service.update_followups(db, lee.id)
    db.refresh(walk)
    db.refresh(old_friend)
    assert walk.followed_up is True and old_friend.followed_up is False


def test_declined_nudges_are_never_marked_followed_up(db: Session) -> None:
    lee = elder(db, "Mr Lee")
    n = Nudge(
        call_id=seeded_call(db, lee, 10).id,
        elder_id=lee.id,
        type="go_outside",
        text_yue="x",
        text_en="x",
        elder_response="declined",
        followed_up=None,
    )
    db.add(n)
    db.commit()
    nudge_service.update_followups(db, lee.id)
    db.refresh(n)
    assert n.followed_up is None


def test_acceptance_mrs_ho_page_shows_nudge_accepted_and_action_logged(login) -> None:
    worker = login("worker@demo.kaifong")
    detail = worker.get(f"/api/elders/{person(worker, 'Mrs Ho')}").json()
    accepted = [n for n in detail["nudges"] if n["elder_response"] == "interested"]
    assert len(accepted) == 2 and all(n["followed_up"] for n in accepted)
    assert {a["action"] for a in detail["actions"]} >= {
        "family_contacted",
        "centre_activity_arranged",
    }
    assert detail["connection"] == {"suggested": 3, "accepted": 2, "followed_up": 2}


# ---------- family digest ----------------------------------------------------------------------


def test_acceptance_family_sees_a_warm_digest_without_transcripts(login, db: Session) -> None:
    family = login("family2@demo.kaifong")
    [d] = family.get("/api/family/digest").json()
    assert d["elder"]["display_name_en"] == "Mrs Ho" and d["sent_simulated_at"]
    assert set(d) == {
        "elder",
        "week_start",
        "text_en",
        "text_zh",
        "sent_simulated_at",
    }  # no call ids, no data
    en, zh = d["text_en"], d["text_zh"]
    assert (
        "on 7 of the last 7 days" in en
        and "lovely to hear" in en
        and "would mean a lot" in en
        and "A good time to call" in en
    )
    assert "何婆婆" in zh and "好時間打俾佢" in zh
    assert "emigrated" not in en and "移民" not in zh  # family tensions are never surfaced
    # no transcript line is ever quoted
    ho = elder(db, "Mrs Ho")
    calls = [c.id for c in db.exec(select(Call).where(Call.elder_id == ho.id))]
    for t in db.exec(select(Turn).where(Turn.call_id.in_(calls))):
        assert t.text not in en and t.text not in zh
    # and the family account still cannot reach calls, transcripts or the person's record
    assert family.get(f"/api/calls/{calls[-1]}").status_code == 403
    assert family.get(f"/api/elders/{ho.id}").status_code == 403


@pytest.fixture
def clean_db():
    from app.seed.seed import reset_and_seed

    reset_and_seed()


def test_digest_never_contains_clinical_detail(clean_db, login, db: Session) -> None:
    worker = login("worker@demo.kaifong")
    chan = person(worker, "Mrs Chan")  # severe knee pain, skipped meals, homebound
    text = worker.post(f"/api/elders/{chan}/digest").json()
    blob = (text["text_en"] + text["text_zh"]).lower()
    for word in (
        "pain",
        "knee",
        "severe",
        "skipped",
        "medication",
        "pills",
        "confus",
        "dementia",
        "depress",
        "膝",
        "痛",
        "藥",
        "混亂",
    ):
        assert word not in blob, word
    assert (
        "care team is keeping a gentle eye" in text["text_en"]
    )  # a watched week is flagged gently, no specifics


def test_digest_is_hidden_from_family_until_staff_mark_it_sent(
    clean_db, login, db: Session
) -> None:
    worker, family = login("worker@demo.kaifong"), login("family@demo.kaifong")
    chan = person(worker, "Mrs Chan")
    assert len(family.get("/api/family/digest").json()) == 1  # seeded, already sent
    fresh = worker.post(f"/api/elders/{chan}/digest").json()
    assert fresh["sent_simulated_at"] is None
    assert family.get("/api/family/digest").json() == []  # regenerated: staff preview first
    assert worker.get(f"/api/elders/{chan}/digest").json()["id"] == fresh["id"]
    sent = worker.post(f"/api/digests/{fresh['id']}/send").json()
    assert sent["sent_simulated_at"]
    assert len(family.get("/api/family/digest").json()) == 1
    assert (
        len(db.exec(select(FamilyDigest).where(FamilyDigest.elder_id == chan)).all()) == 1
    )  # one per week
    assert db.exec(select(AuditLog).where(AuditLog.action == "digest_send_simulated")).first()


def test_digest_permissions_and_consent(clean_db, login, db: Session) -> None:
    worker = login("worker@demo.kaifong")
    ho = person(worker, "Mrs Ho")
    assert login("family2@demo.kaifong").post(f"/api/elders/{ho}/digest").status_code == 403
    assert login("worker2@demo.kaifong").post(f"/api/elders/{ho}/digest").status_code == 403
    assert (
        login("worker@demo.kaifong").get("/api/family/digest").status_code == 403
    )  # family-only endpoint
    assert (
        login("family@demo.kaifong").get("/api/family/digest").json()[0]["elder"]["display_name_en"]
        == "Mrs Chan"
    )
    consent = db.exec(select(Consent).where(Consent.elder_id == ho)).first()
    consent.scope_family_digest = False
    db.add(consent)
    db.commit()
    try:
        assert (
            worker.post(f"/api/elders/{ho}/digest").status_code == 409
        )  # no consent to share with family
        assert (
            login("family2@demo.kaifong").get("/api/family/digest").json() == []
        )  # and existing ones hidden
    finally:
        consent.scope_family_digest = True
        db.add(consent)
        db.commit()


def test_digest_tone_depends_on_the_week(db: Session) -> None:
    from app.analysis.digest import compose

    base = {
        "start": "a",
        "end": "b",
        "nudges": [],
        "lonely": False,
        "watched": False,
        "facts": [],
        "preferred_time": "09:30",
    }
    steady = compose("en", "Mrs X", xs=[], **base)
    assert "lonely" not in steady and "care team is keeping" not in steady and "afternoon" in steady
    evening = compose("en", "Mrs X", xs=[], **{**base, "preferred_time": "15:00"})
    assert "in the morning" in evening
    assert "孤單" in compose("yue", "X", xs=[], **{**base, "lonely": True})
