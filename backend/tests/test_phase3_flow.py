"""Phase 3 acceptance: call -> summary with evidence -> memory -> risk recompute -> live events."""

import time

import pytest
from sqlmodel import Session, select

from app.models import Call, CallSummary, MemoryFact, Nudge
from app.providers.base import LLMProvider
from app.providers.registry import Providers, get_providers, set_providers

pytestmark = pytest.mark.usefixtures("reseed_after")

LINES = ["早晨，冇食午飯", "冇出街，唔想出去", "我孫女星期日嚟探我", "瞓得好", "拜拜"]


def person(client, name: str) -> int:
    return next(
        c["id"] for c in client.get("/api/today").json()["cards"] if c["display_name_en"] == name
    )


def run_call(client, eid: int, lines=LINES) -> int:
    start = client.post("/api/calls", json={"elder_id": eid}).json()
    for text in lines:
        events = client.post(f"/api/calls/{start['call_id']}/turn", json={"text": text}).json()[
            "events"
        ]
        if any(e["type"] == "call.ended" for e in events):
            break
    else:
        client.post(f"/api/calls/{start['call_id']}/end")
    return start["call_id"]


def wait_for_summary(client, call_id: int, timeout=8.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/calls/{call_id}").json()
        if body["summary"]:
            return body
        time.sleep(0.1)
    raise AssertionError("summary never appeared")


def test_acceptance_summary_with_evidence_memory_risk_and_live_update(login, db: Session) -> None:
    worker = login("worker@demo.kaifong")
    lee = person(worker, "Mr Lee")
    assert (
        next(c for c in worker.get("/api/today").json()["cards"] if c["id"] == lee)["level"]
        == "green"
    )

    with worker.websocket_connect("/ws/dashboard") as dash:
        first = run_call(worker, lee)
        body = wait_for_summary(worker, first)
        ex = body["summary"]["extraction"]

        # summary fields are filled, each non-unknown value cites the older person's own turns
        elder_turns = {t["index"] for t in body["turns"] if t["speaker"] == "elder"}
        assert ex["appetite"]["value"] == "skipped_meals" and ex["went_out_today"]["value"] == "no"
        for key in ("appetite", "went_out_today", "sleep"):
            assert ex[key]["evidence_turns"] and set(ex[key]["evidence_turns"]) <= elder_turns
        assert body["summary"]["summary_en"] and body["summary"]["summary_yue"]
        assert ex["pain"]["value"] == "unknown"  # never mentioned: not guessed

        # one skipped meal is not yet a flag...
        assert (
            next(c for c in worker.get("/api/today").json()["cards"] if c["id"] == lee)["level"]
            == "green"
        )

        # ...but a second one within a week is: Today changes, and the dashboard is told live
        second = run_call(worker, lee)
        wait_for_summary(worker, second)
        seen = []
        for _ in range(30):
            ev = dash.receive_json()
            seen.append(ev["type"])
            if ev["type"] == "risk.updated" and ev["level"] == "amber":
                assert ev["elder_id"] == lee and "SKIPPED_MEALS" in ev["reasons"]
                break
        else:
            raise AssertionError(f"no amber risk.updated; saw {seen}")
    card = next(c for c in worker.get("/api/today").json()["cards"] if c["id"] == lee)
    assert card["level"] == "amber" and card["top_reasons"][0]["code"] == "SKIPPED_MEALS"
    assert card["top_reasons"][0]["evidence_call_ids"] == [first, second]

    # memory: the visit was remembered once (deduplicated across two calls)
    db.expire_all()
    facts = db.exec(
        select(MemoryFact).where(
            MemoryFact.elder_id == lee, MemoryFact.source_call_id.in_([first, second])
        )
    ).all()
    assert [f.category for f in facts].count("event") == 1

    # ...and the next call's greeting refers to it
    third = worker.post("/api/calls", json={"elder_id": lee}).json()
    assert (
        "孫女會嚟探佢" in third["greeting"]["text"]
        and "granddaughter" in third["greeting"]["text_en"]
    )
    worker.post(f"/api/calls/{third['call_id']}/end")


def test_nudge_is_recorded_and_its_outcome_read_after_the_call(login, db: Session) -> None:
    worker = login("worker@demo.kaifong")
    wong = person(worker, "Mrs Wong")
    call_id = run_call(
        worker, wong, ["早晨", "好", "冇痛", "開心", "出咗街", "好呀，我試下", "拜拜"]
    )
    wait_for_summary(worker, call_id)
    db.expire_all()
    nudge = db.exec(select(Nudge).where(Nudge.call_id == call_id)).first()
    assert nudge is not None and nudge.elder_response == "interested"


def test_reanalyse_is_idempotent_and_replaces_the_summary(login, db: Session) -> None:
    worker = login("worker@demo.kaifong")
    cheung = person(worker, "Mr Cheung")
    call_id = run_call(worker, cheung, ["冇食早餐", "拜拜"])
    wait_for_summary(worker, call_id)
    before = len(db.exec(select(MemoryFact).where(MemoryFact.elder_id == cheung)).all())
    res = worker.post(f"/api/calls/{call_id}/reanalyse")
    assert res.status_code == 200 and res.json()["call_id"] == call_id
    db.expire_all()
    assert len(db.exec(select(CallSummary).where(CallSummary.call_id == call_id)).all()) == 1
    assert len(db.exec(select(MemoryFact).where(MemoryFact.elder_id == cheung)).all()) == before
    assert login("family@demo.kaifong").post(f"/api/calls/{call_id}/reanalyse").status_code == 403
    seeded = db.exec(select(Call).where(Call.mode == "seed", Call.status == "no_answer")).first()
    assert worker.post(f"/api/calls/{seeded.id}/reanalyse").status_code == 422  # nothing to analyse


class BrokenExtractor(LLMProvider):
    name = "broken"
    streams = False

    async def complete(self, *a, **k):
        raise RuntimeError("down")


def test_extraction_outage_still_stores_a_low_confidence_summary_and_updates_risk(login) -> None:
    worker = login("worker@demo.kaifong")
    patel = person(worker, "Mrs Patel")
    prev = get_providers()
    set_providers(Providers(**{**prev.__dict__, "llm_extract": BrokenExtractor()}))
    try:
        call_id = run_call(worker, patel, ["Good morning", "bye"])
        body = wait_for_summary(worker, call_id)
    finally:
        set_providers(prev)
    assert body["summary"]["overall_confidence"] == "low" and body["summary"]["summary_en"]
    assert body["summary"]["extraction"]["appetite"]["value"] == "unknown"
    assert next(c for c in worker.get("/api/today").json()["cards"] if c["id"] == patel)[
        "level"
    ] in ("green", "amber")
