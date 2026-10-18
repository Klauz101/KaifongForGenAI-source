import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.main import app
from app.models import AuditLog, CallSummary, Elder, MemoryFact, Turn
from app.schemas import Extraction
from app.security.crypto import decrypt, encrypt


def by_name(cards: list[dict]) -> dict:
    return {c["display_name_en"]: c for c in cards}


def test_encryption_roundtrip_and_ciphertext_at_rest(db: Session) -> None:
    assert decrypt(encrypt("陳大文")) == "陳大文"
    elder = db.exec(select(Elder)).first()
    assert elder.full_name.startswith(("Chan", "Lee"))  # decrypted through the ORM
    raw = sqlite3.connect(engine.url.database)
    names = [r[0] for r in raw.execute("select full_name from elder")]
    texts = [r[0] for r in raw.execute("select text from turn")]
    assert names and all("Demo" not in n for n in names)
    assert texts and all(t.startswith("gAAAA") for t in texts)


def test_login_rules() -> None:
    client = TestClient(app)
    assert client.get("/api/today").status_code == 401
    bad = client.post("/api/auth/login", json={"email": "worker@demo.kaifong", "password": "nope"})
    assert bad.status_code == 401


def test_today_triage_matches_spec_section_12(login) -> None:
    data = login("worker@demo.kaifong").get("/api/today").json()
    cards = by_name(data["cards"])
    assert len(cards) == 8
    levels = {k: v["level"] for k, v in cards.items()}
    assert levels == {
        "Mrs Chan": "amber",
        "Mr Lee": "green",
        "Mrs Wong": "amber",
        "Mr Cheung": "red",
        "Mrs Ho": "amber",
        "Mr Lau": "green",
        "Mrs Fung": "amber",
        "Mrs Patel": "green",
    }
    assert data["cards"][0]["bucket"] == "red"  # red first
    ambers = [c["reason_count"] for c in data["cards"] if c["bucket"] == "amber"]
    assert ambers == sorted(ambers, reverse=True)  # most reasons first
    assert data["summary"]["urgent"] == 1 and data["summary"]["need_attention"] == 5
    assert data["summary"]["calls_completed"] == 7  # Mr Cheung did not answer
    chan = cards["Mrs Chan"]
    assert chan["reason_count"] == 3
    codes = {
        r["code"]
        for r in login("worker@demo.kaifong")
        .get(f"/api/elders/{chan['id']}")
        .json()["risk"]["reasons"]
    }
    assert codes == {"HOMEBOUND", "SKIPPED_MEALS", "PAIN_SEVERE"}
    assert cards["Mr Cheung"]["top_reasons"][0]["code"] == "UNREACHABLE"


def test_chan_trends_show_the_decline(login) -> None:
    client = login("worker@demo.kaifong")
    cid = by_name(client.get("/api/today").json()["cards"])["Mrs Chan"]["id"]
    trends = client.get(f"/api/elders/{cid}").json()["trends"]
    assert len(trends) == 21
    assert [t["went_out"] for t in trends[-5:]] == ["no"] * 5
    assert trends[0]["went_out"] == "yes" and trends[0]["mood"] == 4 and trends[-1]["mood"] == 3
    assert sum(t["appetite"] == "skipped_meals" for t in trends[-7:]) >= 2


def test_family_cannot_see_transcripts_or_triage(login, db: Session) -> None:
    family = login("family@demo.kaifong")
    call_id = db.exec(select(Turn.call_id)).first()
    assert family.get(f"/api/calls/{call_id}").status_code == 403
    assert family.get("/api/today").status_code == 403
    assert family.get("/api/alerts").status_code == 403
    listing = family.get("/api/elders").json()
    assert [e["display_name_en"] for e in listing] == ["Mrs Chan"]
    assert set(listing[0]) == {"id", "display_name", "display_name_en"}
    assert family.get(f"/api/elders/{listing[0]['id']}").status_code == 403


def test_social_worker_sees_only_own_caseload(login) -> None:
    other = login("worker2@demo.kaifong")
    assert other.get("/api/today").json()["cards"] == []
    elder_id = by_name(login("worker@demo.kaifong").get("/api/today").json()["cards"])["Mrs Chan"][
        "id"
    ]
    assert other.get(f"/api/elders/{elder_id}").status_code == 403
    assert len(login("admin@demo.kaifong").get("/api/today").json()["cards"]) == 8


def test_transcript_read_is_audited(login, db: Session) -> None:
    worker = login("worker@demo.kaifong")
    call_id = db.exec(select(Turn.call_id)).first()
    before = len(db.exec(select(AuditLog).where(AuditLog.action == "read_transcript")).all())
    res = worker.get(f"/api/calls/{call_id}")
    assert res.status_code == 200 and res.json()["turns"] and res.json()["summary"]
    db.expire_all()
    after = len(db.exec(select(AuditLog).where(AuditLog.action == "read_transcript")).all())
    assert after == before + 1


def test_alerts_list_and_filter(login) -> None:
    worker = login("worker@demo.kaifong")
    open_alerts = worker.get("/api/alerts?status=open").json()
    assert [a["type"] for a in open_alerts] == ["unreachable"]
    resolved = worker.get("/api/alerts?status=resolved").json()
    assert [a["elder"]["display_name_en"] for a in resolved] == ["Mr Lau"]


def test_memory_fact_can_be_deactivated_and_is_audited(login, db: Session) -> None:
    worker = login("worker@demo.kaifong")
    fact = db.exec(select(MemoryFact)).first()
    res = worker.patch(f"/api/elders/{fact.elder_id}/memory/{fact.id}", json={"active": False})
    assert res.status_code == 200 and res.json()["active"] is False
    assert (
        login("family@demo.kaifong")
        .patch(f"/api/elders/{fact.elder_id}/memory/{fact.id}", json={"active": True})
        .status_code
        == 403
    )
    worker.patch(f"/api/elders/{fact.elder_id}/memory/{fact.id}", json={"active": True})


def test_mrs_ho_nudges_accepted_then_acted_on(login) -> None:
    worker = login("worker@demo.kaifong")
    ho = by_name(worker.get("/api/today").json()["cards"])["Mrs Ho"]["id"]
    detail = worker.get(f"/api/elders/{ho}").json()
    accepted = [
        n for n in detail["nudges"] if n["elder_response"] == "interested" and n["followed_up"]
    ]
    assert len(accepted) == 2
    assert {a["action"] for a in detail["actions"]} >= {
        "family_contacted",
        "centre_activity_arranged",
    }


@pytest.mark.parametrize("model", [CallSummary])
def test_seeded_extractions_match_schema_and_cite_evidence(db: Session, model) -> None:
    transcribed = 0
    for s in db.exec(select(model)):
        ex = Extraction.model_validate(s.extraction)
        n_turns = len(db.exec(select(Turn).where(Turn.call_id == s.call_id)).all())
        if n_turns:
            transcribed += 1
            if ex.overall_confidence != "low":
                for field in (ex.sleep, ex.appetite, ex.meds_taken, ex.pain, ex.went_out_today):
                    if field.value not in ("unknown", "not_applicable"):
                        assert field.evidence_turns and max(field.evidence_turns) < n_turns
    assert transcribed == 8 * 3 - 2  # last 3 calls per elder; Cheung's 2 missed calls have none
