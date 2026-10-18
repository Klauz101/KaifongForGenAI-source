"""Phase 6: privacy hardening (export, consent withdrawal, purge, retention, audit, corrections)."""

import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

from app import main as app_main
from app.config import get_settings
from app.jobs import retention
from app.models import (
    ActionLog,
    Alert,
    AuditLog,
    Call,
    CallSummary,
    Consent,
    Elder,
    FamilyDigest,
    MemoryFact,
    RiskAssessment,
    Turn,
    User,
    utcnow,
)
from app.security.pseudonymize import Pseudonymizer
from scripts import cantonese_review

pytestmark = pytest.mark.usefixtures("reseed_after")


def person(client, name: str) -> int:
    return next(
        c["id"] for c in client.get("/api/today").json()["cards"] if c["display_name_en"] == name
    )


def counts(db: Session, eid: int) -> dict:
    ids = [c.id for c in db.exec(select(Call).where(Call.elder_id == eid))] or [0]
    return {
        "calls": len(ids) if ids != [0] else 0,
        "turns": len(db.exec(select(Turn).where(Turn.call_id.in_(ids))).all()),
        "summaries": len(db.exec(select(CallSummary).where(CallSummary.call_id.in_(ids))).all()),
        "facts": len(db.exec(select(MemoryFact).where(MemoryFact.elder_id == eid)).all()),
        "assessments": len(
            db.exec(select(RiskAssessment).where(RiskAssessment.elder_id == eid)).all()
        ),
    }


# ---------- data-access export ---------------------------------------------------------------------


def test_export_contains_everything_decrypted_and_is_admin_only_and_audited(
    login, db: Session
) -> None:
    admin, worker = login("admin@demo.kaifong"), login("worker@demo.kaifong")
    chan = person(admin, "Mrs Chan")
    assert worker.get(f"/api/elders/{chan}/export").status_code == 403
    assert login("family@demo.kaifong").get(f"/api/elders/{chan}/export").status_code == 403
    data = admin.get(f"/api/elders/{chan}/export").json()
    assert data["elder"]["full_name"] == "Chan Demo Mei" and data["elder"]["phone"] == "0000 0001"
    assert len(data["calls"]) == 21 and any(c["turns"] for c in data["calls"])
    sample = next(c for c in data["calls"] if c["turns"])
    assert sample["turns"][0]["text"] and sample["summary"]["extraction"]["appetite"]
    assert (
        data["consent"]
        and data["memory_facts"]
        and data["risk_assessments"]
        and "audit_trail" in data
    )
    assert db.exec(
        select(AuditLog).where(AuditLog.action == "export_data", AuditLog.entity_id == chan)
    ).first()
    assert admin.get("/api/elders/99999/export").status_code == 404


# ---------- consent withdrawal -----------------------------------------------------------------------


def test_withdrawing_consent_stops_calls_digests_and_schedules_deletion(
    clean_db, login, db: Session
) -> None:
    worker, family = login("worker@demo.kaifong"), login("family2@demo.kaifong")
    ho = person(worker, "Mrs Ho")
    assert len(family.get("/api/family/digest").json()) == 1
    cid = worker.post("/api/calls", json={"elder_id": ho}).json()["call_id"]  # a call in progress
    worker.post(
        f"/api/calls/{cid}/turn", json={"text": "唔使再打嚟喇"}
    )  # opt-out asked: alert raised on confirmation
    worker.post(f"/api/calls/{cid}/turn", json={"text": "係呀"})

    assert (
        worker.post(f"/api/elders/{ho}/consent/withdraw", json={"notes": "  "}).status_code == 422
    )  # note is mandatory
    assert (
        login("worker2@demo.kaifong")
        .post(f"/api/elders/{ho}/consent/withdraw", json={"notes": "x"})
        .status_code
        == 403
    )
    assert (
        login("family2@demo.kaifong")
        .post(f"/api/elders/{ho}/consent/withdraw", json={"notes": "x"})
        .status_code
        == 403
    )

    with worker.websocket_connect("/ws/dashboard") as dash:
        status = worker.post(
            f"/api/elders/{ho}/consent/withdraw",
            json={"notes": "Visited with her son; she confirmed."},
        ).json()
        assert dash.receive_json() == {
            "type": "elder.updated",
            "elder_id": ho,
            "status": "withdrawn",
        }
    assert (
        status["active"] is False
        and status["elder_status"] == "withdrawn"
        and status["withdrawn_at"]
    )
    due = status["deletion_due_at"]
    assert due.startswith(
        (utcnow() + timedelta(days=get_settings().withdrawal_grace_days)).strftime("%Y-%m-%d")
    )

    assert worker.post("/api/calls", json={"elder_id": ho}).status_code == 403  # calls stop
    assert family.get("/api/family/digest").json() == []  # family digest stops
    assert all(
        c["id"] != ho for c in worker.get("/api/today").json()["cards"]
    )  # off the triage list
    db.expire_all()
    opt_out = db.exec(
        select(Alert).where(Alert.elder_id == ho, Alert.type == "opt_out_request")
    ).first()
    assert (
        opt_out.resolved_at and "Consent withdrawn" in opt_out.notes
    )  # the paused-calls task is closed
    assert db.exec(
        select(AuditLog).where(AuditLog.action == "consent_withdrawn", AuditLog.entity_id == ho)
    ).first()
    assert (
        worker.post(f"/api/elders/{ho}/consent/withdraw", json={"notes": "again"}).status_code
        == 409
    )
    assert (
        worker.patch(f"/api/elders/{ho}/status", json={"status": "active"}).status_code == 409
    )  # cannot resume


def test_withdrawing_hangs_up_a_call_in_progress(clean_db, login, db: Session) -> None:
    worker = login("worker@demo.kaifong")
    lee = person(worker, "Mr Lee")
    cid = worker.post("/api/calls", json={"elder_id": lee}).json()["call_id"]
    worker.post(f"/api/elders/{lee}/consent/withdraw", json={"notes": "She asked in person."})
    db.expire_all()
    assert db.get(Call, cid).status == "aborted" and db.get(Call, cid).ended_at


def test_consent_status_endpoint(login) -> None:
    worker = login("worker@demo.kaifong")
    s = worker.get(f"/api/elders/{person(worker, 'Mrs Chan')}/consent").json()
    assert (
        s["active"]
        and s["witnessed_by"] == "Demo Staff"
        and s["scope_family_digest"]
        and s["deletion_due_at"] is None
    )
    assert (
        login("worker2@demo.kaifong")
        .get(f"/api/elders/{person(worker, 'Mrs Chan')}/consent")
        .status_code
        == 403
    )


# ---------- purge ---------------------------------------------------------------------------------------


def test_purge_deletes_all_personal_data_but_keeps_consent_proof_and_audit(
    clean_db, login, db: Session
) -> None:
    admin = login("admin@demo.kaifong")
    ho = person(admin, "Mrs Ho")
    assert (
        admin.post(f"/api/elders/{ho}/purge").status_code == 409
    )  # only after consent is withdrawn
    admin.post(f"/api/elders/{ho}/consent/withdraw", json={"notes": "Confirmed in person."})
    assert (
        login("worker@demo.kaifong").post(f"/api/elders/{ho}/purge").status_code == 403
    )  # admin only
    before = counts(db, ho)
    assert before["turns"] and before["summaries"] and before["facts"]
    done = admin.post(f"/api/elders/{ho}/purge").json()
    assert (
        done["calls"] == 21
        and done["turns"] == before["turns"]
        and done["summaries"] == before["summaries"]
    )
    db.expire_all()
    assert counts(db, ho) == {"calls": 0, "turns": 0, "summaries": 0, "facts": 0, "assessments": 0}
    elder = db.get(Elder, ho)
    assert (
        elder.full_name == "[deleted]"
        and elder.phone == "[deleted]"
        and elder.display_name == "(removed)"
    )
    assert (
        db.exec(select(Consent).where(Consent.elder_id == ho)).first().withdrawn_at
    )  # proof of withdrawal kept
    assert db.exec(
        select(AuditLog).where(AuditLog.action == "elder_purged", AuditLog.entity_id == ho)
    ).first()
    assert db.exec(select(FamilyDigest).where(FamilyDigest.elder_id == ho)).first() is None
    assert db.exec(select(ActionLog).where(ActionLog.elder_id == ho)).first() is None
    family = db.exec(select(User).where(User.email == "family2@demo.kaifong")).first()
    assert ho not in family.linked_elder_ids
    exported = admin.get(f"/api/elders/{ho}/export").json()
    assert exported["calls"] == [] and exported["elder"]["full_name"] == "[deleted]"


# ---------- retention ----------------------------------------------------------------------------------


def test_retention_dry_run_counts_and_changes_nothing(clean_db, login, db: Session) -> None:
    admin = login("admin@demo.kaifong")
    before = {n: counts(db, p) for n, p in ((n, person(admin, n)) for n in ("Mrs Chan", "Mr Lee"))}
    far_future = utcnow() + timedelta(days=200)
    dry = retention.run_retention(db, now=far_future, dry_run=True)
    assert dry["turns"] > 0 and dry["summaries"] > 0
    assert {n: counts(db, person(admin, n)) for n in before} == before
    assert admin.post("/api/admin/retention/run", json={"dry_run": True}).json()["dry_run"] is True
    assert (
        login("worker@demo.kaifong")
        .post("/api/admin/retention/run", json={"dry_run": True})
        .status_code
        == 403
    )


def test_retention_deletes_old_turns_summaries_facts_and_assessments_but_keeps_call_metadata(
    clean_db, db: Session
) -> None:
    elder = db.exec(select(Elder).where(Elder.display_name_en == "Mrs Chan")).first()
    calls_before = counts(db, elder.id)["calls"]
    result = retention.run_retention(db, now=utcnow() + timedelta(days=200))
    assert (
        result["turns"] > 0
        and result["summaries"] > 0
        and result["facts"] > 0
        and result["assessments"] > 0
    )
    after = counts(db, elder.id)
    assert after["turns"] == after["summaries"] == after["facts"] == after["assessments"] == 0
    assert after["calls"] == calls_before  # who/when is kept for accountability; content is not
    assert db.exec(select(AuditLog).where(AuditLog.action.startswith("retention_run"))).first()


def test_retention_keeps_recent_data_and_respects_a_shorter_consented_period(
    clean_db, db: Session
) -> None:
    lee = db.exec(select(Elder).where(Elder.display_name_en == "Mr Lee")).first()
    other = db.exec(select(Elder).where(Elder.display_name_en == "Mr Lau")).first()
    before_other = counts(db, other.id)
    assert retention.run_retention(db)["summaries"] == 0  # nothing is older than 180 days yet
    consent = db.exec(select(Consent).where(Consent.elder_id == lee.id)).first()
    consent.scope_data_retention_days = 10  # Mr Lee only agreed to 10 days
    db.add(consent)
    db.commit()
    retention.run_retention(db)
    summaries = db.exec(
        select(CallSummary).where(
            CallSummary.call_id.in_(
                [c.id for c in db.exec(select(Call).where(Call.elder_id == lee.id))]
            )
        )
    ).all()
    assert 0 < len(summaries) < 21  # the last ~10 days survive
    assert counts(db, other.id) == before_other  # a person with the default period is untouched


def test_withdrawn_people_are_purged_only_after_the_grace_period(
    clean_db, login, db: Session
) -> None:
    worker = login("worker@demo.kaifong")
    early, late = person(worker, "Mr Lau"), person(worker, "Mrs Fung")
    for eid in (early, late):
        worker.post(f"/api/elders/{eid}/consent/withdraw", json={"notes": "In person."})
    db.expire_all()
    grace = get_settings().withdrawal_grace_days
    early_c = db.exec(select(Consent).where(Consent.elder_id == early)).first()
    early_c.withdrawn_at = utcnow() - timedelta(days=grace + 1)
    db.add(early_c)
    db.commit()
    result = retention.run_retention(db)
    assert result["purged"] == 1
    db.expire_all()
    assert db.get(Elder, early).full_name == "[deleted]" and counts(db, early)["turns"] == 0
    assert db.get(Elder, late).full_name != "[deleted]" and counts(db, late)["summaries"] > 0
    assert retention.run_retention(db)["purged"] == 0  # idempotent


def test_old_audio_files_are_deleted(tmp_path, monkeypatch) -> None:
    import os
    import time

    monkeypatch.setattr(retention, "AUDIO_DIR", tmp_path)
    old, new = tmp_path / "old.webm", tmp_path / "new.webm"
    old.write_bytes(b"x")
    new.write_bytes(b"y")
    os.utime(old, (time.time() - 3600 * 48, time.time() - 3600 * 48))
    assert retention.delete_old_audio(dry_run=True) == 1 and old.exists()
    assert retention.delete_old_audio() == 1 and not old.exists() and new.exists()


# ---------- audit viewer --------------------------------------------------------------------------------


def test_audit_viewer_is_admin_only_filterable_and_holds_no_content(login) -> None:
    admin, worker = login("admin@demo.kaifong"), login("worker@demo.kaifong")
    call_id = worker.get("/api/today").json()["cards"][0]["id"]
    seeded_call = next(
        c["id"] for c in worker.get(f"/api/elders/{call_id}").json()["calls"] if c["summary_en"]
    )
    worker.get(f"/api/calls/{seeded_call}")  # reading a transcript is audited
    assert (
        worker.get("/api/audit").status_code == 403
        and login("family@demo.kaifong").get("/api/audit").status_code == 403
    )
    body = admin.get("/api/audit?action=read_transcript").json()
    assert body["total"] >= 1 and all(
        i["action"] == "read_transcript" and i["entity"] == "call" for i in body["items"]
    )
    assert body["items"][0]["user"] == "Demo Social Worker A"
    assert set(body["items"][0]) == {
        "id",
        "at",
        "user_id",
        "user",
        "action",
        "entity",
        "entity_id",
    }  # ids, never content
    everything = admin.get("/api/audit?limit=5").json()
    assert len(everything["items"]) == 5 and everything["total"] > 5
    page2 = admin.get("/api/audit?limit=5&offset=5").json()
    assert not {i["id"] for i in everything["items"]} & {i["id"] for i in page2["items"]}
    assert all(i["entity"] == "alert" for i in admin.get("/api/audit?entity=alert").json()["items"])


# ---------- correcting extracted fields ------------------------------------------------------------------


def test_social_worker_can_correct_a_field_with_audit_and_risk_recompute(
    clean_db, login, db: Session
) -> None:
    worker = login("worker@demo.kaifong")
    lee = person(worker, "Mr Lee")
    detail = worker.get(f"/api/elders/{lee}").json()
    cid = next(c["id"] for c in detail["calls"] if c["summary_en"])
    res = worker.patch(f"/api/calls/{cid}/extraction", json={"field": "meds_taken", "value": "no"})
    assert res.status_code == 200 and res.json()["value"] == "no"
    db.expire_all()
    data = db.exec(select(CallSummary).where(CallSummary.call_id == cid)).first().extraction
    assert data["meds_taken"]["value"] == "no" and data["corrections"][-1]["field"] == "meds_taken"
    assert (
        data["corrections"][-1]["by"]
        == db.exec(select(User).where(User.email == "worker@demo.kaifong")).first().id
    )
    assert db.exec(
        select(AuditLog).where(
            AuditLog.action == "extraction_edit:meds_taken", AuditLog.entity_id == cid
        )
    ).first()
    shown = worker.get(f"/api/calls/{cid}").json()["summary"]["extraction"]
    assert shown["meds_taken"]["value"] == "no"


def test_corrections_are_validated_and_permissioned(login) -> None:
    worker = login("worker@demo.kaifong")
    lee = person(worker, "Mr Lee")
    cid = next(c["id"] for c in worker.get(f"/api/elders/{lee}").json()["calls"] if c["summary_en"])
    for bad in (
        {"field": "mood", "value": 9},
        {"field": "appetite", "value": "starving"},
        {"field": "summary_en", "value": "x"},
        {"field": "concerns", "value": "x"},
    ):
        assert worker.patch(f"/api/calls/{cid}/extraction", json=bad).status_code == 422, bad
    ok = {"field": "mood", "value": 3}
    assert (
        login("worker2@demo.kaifong").patch(f"/api/calls/{cid}/extraction", json=ok).status_code
        == 403
    )
    assert (
        login("family@demo.kaifong").patch(f"/api/calls/{cid}/extraction", json=ok).status_code
        == 403
    )
    assert worker.patch("/api/calls/99999/extraction", json=ok).status_code == 404


# ---------- pseudonymiser (addresses) ---------------------------------------------------------------------


def test_pseudonymiser_removes_all_four_identifier_types() -> None:
    p = Pseudonymizer("Chan Demo Mei", "9123 4567", ("Mrs Chan", "陳婆婆"))
    cases = {
        "I live at Flat 12B, 3/F, Block 4": "Flat",
        "我住喺彩虹邨 12樓B室": "彩虹邨",
        "come to 123 Nathan Road": "Nathan",
        "Mrs Chan Demo Mei, HKID A123456(7), phone 9123-4567": "A123456",
    }
    for text, leaked in cases.items():
        out = p.scrub(text)
        assert leaked not in out, (text, out)
    assert (
        "[ADDRESS]" in p.scrub("Flat 12B")
        and p.rehydrate("see [ADDRESS] and [ID]", "Mrs Chan") == "see  and "
    )
    assert (
        p.scrub("今日天氣好，我去咗街市買餸") == "今日天氣好，我去咗街市買餸"
    )  # ordinary talk is untouched
    assert "Mrs Chan" in p.scrub("Mrs Chan says hi")


# ---------- DPP3 provider flag, privacy page, Cantonese review sheet ------------------------------------------------


def test_health_warning_for_real_providers(login, monkeypatch) -> None:
    client = login("worker@demo.kaifong")
    assert client.get("/api/health").json()["warnings"] == []  # mock mode: nothing to warn about
    fake = SimpleNamespace(
        names=lambda: {"stt": "azure", "tts": "azure", "llm": "openai_compat", "translate": "mock"}
    )
    monkeypatch.setattr(app_main, "get_providers", lambda: fake)
    warning = client.get("/api/health").json()["warnings"]
    assert warning and "no training" in warning[0].lower()
    s = get_settings()
    s.provider_no_training_confirmed = True
    try:
        assert client.get("/api/health").json()["warnings"] == []
    finally:
        s.provider_no_training_confirmed = False


def test_privacy_page_content_is_parallel_in_both_languages_and_covers_the_ordinance_basics() -> (
    None
):
    data = json.loads(
        (Path(cantonese_review.FRONT) / "i18n" / "privacy.json").read_text(encoding="utf-8")
    )
    en, zh = data["en"]["sections"], data["zh"]["sections"]
    assert len(en) == len(zh) >= 6 and all(len(a["p"]) == len(b["p"]) for a, b in zip(en, zh))
    text = json.dumps(data["en"]).lower()
    for must in (
        "ai",
        "stop the calls",
        "delete",
        "correct",
        "personal data (privacy) ordinance",
        "do not keep recordings",
    ):
        assert must in text.replace("we do not keep recordings", "do not keep recordings"), must


def test_cantonese_review_sheet_is_complete_and_up_to_date() -> None:
    text, problems = cantonese_review.build()
    assert problems == [], problems  # every Cantonese string has an English meaning
    assert (
        cantonese_review.OUT.read_text(encoding="utf-8") == text
    ), "stale: run `make cantonese-review`"
    assert "Total strings:" in text and int(text.split("Total strings: **")[1].split("**")[0]) > 400
    from app.safety import redflags

    for cat in redflags.config()["categories"].values():
        for phrase in cat["yue"]:
            assert phrase in text, phrase  # every red-flag phrase is reviewable by a native speaker
