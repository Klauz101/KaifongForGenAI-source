"""Phase 8: demo control tools, and a replay of the README's 3-minute demo script."""

from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.analysis import risk
from app.config import get_settings
from app.conversation import service
from app.models import Alert, Call, CallSummary, RiskAssessment

pytestmark = pytest.mark.usefixtures("reseed_after")
README = Path(__file__).resolve().parents[2] / "README.md"


def person(client, name: str) -> int:
    return next(
        c["id"] for c in client.get("/api/today").json()["cards"] if c["display_name_en"] == name
    )


# ---------- access -----------------------------------------------------------------------------------


def test_demo_tools_are_admin_only_and_dev_only(clean_db, login, monkeypatch) -> None:
    worker, admin = login("worker@demo.kaifong"), login("admin@demo.kaifong")
    chan = person(admin, "Mrs Chan")
    for method, path in (
        ("get", "/api/demo/status"),
        ("post", "/api/demo/reset"),
        ("post", f"/api/demo/decline/{chan}"),
        ("post", f"/api/demo/inject/{chan}"),
        ("post", f"/api/demo/missed-call/{chan}"),
    ):
        assert getattr(worker, method)(path).status_code == 403, path
        assert getattr(login("family@demo.kaifong"), method)(path).status_code == 403, path
    monkeypatch.setattr(
        get_settings(), "app_env", "production"
    )  # never allowed to rewrite data in production
    for method, path in (
        ("get", "/api/demo/status"),
        ("post", "/api/demo/reset"),
        ("post", f"/api/demo/decline/{chan}"),
    ):
        res = getattr(admin, method)(path)
        assert res.status_code == 403 and "dev/demo" in res.json()["detail"], path


def test_status_lists_everyone_with_live_call_flags(clean_db, login) -> None:
    admin = login("admin@demo.kaifong")
    body = admin.get("/api/demo/status").json()
    assert (
        body["app_env"] == "dev" and body["providers"]["llm"] == "mock" and len(body["people"]) == 8
    )
    assert {p["display_name_en"]: p["level"] for p in body["people"]}["Mr Cheung"] == "red"
    assert body["calls_in_progress"] == 0 and body["open_alerts"] == 1
    cid = admin.post("/api/calls", json={"elder_id": person(admin, "Mr Lee")}).json()["call_id"]
    after = admin.get("/api/demo/status").json()
    assert (
        after["calls_in_progress"] == 1
        and next(p for p in after["people"] if p["display_name_en"] == "Mr Lee")["call_in_progress"]
    )
    admin.post(f"/api/calls/{cid}/end")


# ---------- reset -----------------------------------------------------------------------------------


def test_reset_restores_the_pristine_demo_and_keeps_you_signed_in(
    clean_db, login, db: Session
) -> None:
    admin = login("admin@demo.kaifong")
    chan, lee = person(admin, "Mrs Chan"), person(admin, "Mr Lee")
    admin.post(f"/api/demo/decline/{chan}")
    cid = admin.post("/api/calls", json={"elder_id": lee}).json()["call_id"]  # leave a call running
    assert cid in service.runtimes and len(admin.get("/api/alerts?status=open").json()) == 2
    with admin.websocket_connect("/ws/dashboard") as dash:
        res = admin.post("/api/demo/reset")
        assert res.status_code == 200 and "admin@demo.kaifong" in res.json()["logins"]["admin"]
        assert dash.receive_json() == {"type": "demo.reset"}
    assert not service.runtimes  # live calls are dropped
    assert admin.get("/api/auth/me").status_code == 200  # same ids: the session survives the reset
    today = admin.get("/api/today").json()
    assert today["summary"]["need_attention"] == 5 and today["summary"]["urgent"] == 1
    assert len(admin.get("/api/alerts?status=open").json()) == 1
    assert next(c for c in today["cards"] if c["display_name_en"] == "Mrs Chan")["level"] == "amber"


# ---------- fast-forward decline -------------------------------------------------------------------


def test_three_more_days_of_decline_push_mrs_chan_to_red_sharp_change(
    clean_db, login, db: Session
) -> None:
    admin = login("admin@demo.kaifong")
    chan = person(admin, "Mrs Chan")
    before = next(c for c in admin.get("/api/today").json()["cards"] if c["id"] == chan)
    assert before["level"] == "amber" and before["reason_count"] == 3
    with admin.websocket_connect("/ws/dashboard") as dash:
        out = admin.post(f"/api/demo/decline/{chan}", json={"days": 3}).json()
        events = [dash.receive_json() for _ in range(3)]
    assert out["level"] == "red" and "SHARP_CHANGE" in out["reasons"] and out["alert_id"]
    assert {"HOMEBOUND", "SKIPPED_MEALS", "PAIN_SEVERE", "MEDS_MISSED", "MOOD_DROP"} <= set(
        out["reasons"]
    )  # the real rules did this
    assert [e["type"] for e in events] == ["alert.created", "risk.updated", "risk.updated"]
    assert (
        events[0]["alert"]["type"] == "trend" and events[0]["alert"]["level"] == "red"
    )  # a live toast for the demo

    after = next(c for c in admin.get("/api/today").json()["cards"] if c["id"] == chan)
    assert (
        after["level"] == "red"
        and after["top_reasons"][0]["code"] == "SHARP_CHANGE"
        and after["open_alert"]["type"] == "trend"
    )
    trends = admin.get(f"/api/elders/{chan}").json()["trends"]
    assert (
        len(trends) == 24 and [t["went_out"] for t in trends[-8:]] == ["no"] * 8
    )  # 21 days shifted + 3 new
    assert trends[-1]["mood"] == 1 and trends[0]["went_out"] == "yes"
    db.expire_all()
    newest = db.exec(
        select(Call).where(Call.elder_id == chan).order_by(Call.started_at.desc())
    ).first()
    assert (
        newest.mode == "seed" and risk.hk_day(newest.started_at).isoformat() == trends[-1]["date"]
    )  # synthetic, and marked so
    assert len(db.exec(select(RiskAssessment).where(RiskAssessment.elder_id == chan)).all()) >= 14


def test_decline_is_validated_and_can_be_repeated(clean_db, login) -> None:
    admin = login("admin@demo.kaifong")
    lee = person(admin, "Mr Lee")  # a stable person
    assert admin.post(f"/api/demo/decline/{lee}", json={"days": 0}).status_code == 422
    assert admin.post(f"/api/demo/decline/{lee}", json={"days": 8}).status_code == 422
    assert admin.post("/api/demo/decline/99999").status_code == 404
    first = admin.post(f"/api/demo/decline/{lee}").json()  # default 3 days
    assert first["days"] == 3 and first["level"] == "red"
    again = admin.post(f"/api/demo/decline/{lee}").json()
    assert again["alert_id"] is None  # one open trend alert is enough
    assert len(admin.get(f"/api/elders/{lee}").json()["trends"]) == 27


# ---------- inject a red flag -----------------------------------------------------------------------


def test_inject_needs_a_live_call_then_triggers_the_real_safety_path(
    clean_db, login, db: Session
) -> None:
    admin = login("admin@demo.kaifong")
    wong = person(admin, "Mrs Wong")
    res = admin.post(f"/api/demo/inject/{wong}")
    assert res.status_code == 409 and "simulator" in res.json()["detail"]
    cid = admin.post("/api/calls", json={"elder_id": wong}).json()["call_id"]
    rt = service.runtimes[cid]
    out = admin.post(f"/api/demo/inject/{wong}").json()
    assert out["call_id"] == cid and "跌親" in out["injected"]
    kinds = [e["type"] for e in rt.drain()]  # what the simulator's socket would receive
    assert "call.safety_triggered" in kinds and "turn.reply_text" in kinds
    admin.post(f"/api/calls/{cid}/end")
    db.expire_all()
    assert db.exec(
        select(Alert).where(Alert.call_id == cid, Alert.type == "red_flag_phrase")
    ).first()


def test_inject_uses_the_calls_language(clean_db, login) -> None:
    admin = login("admin@demo.kaifong")
    patel = person(admin, "Mrs Patel")
    cid = admin.post("/api/calls", json={"elder_id": patel, "language": "en"}).json()["call_id"]
    assert "fell yesterday" in admin.post(f"/api/demo/inject/{patel}").json()["injected"]
    admin.post(f"/api/calls/{cid}/end")


# ---------- the README demo script still works ----------------------------------------------------------


def test_readme_demo_script_has_the_timed_steps_and_the_honest_numbers() -> None:
    text = README.read_text(encoding="utf-8")
    assert "3-minute demo script" in text.lower()
    for stamp in ("0:00", "0:30", "1:00", "1:45", "2:15", "2:45"):
        assert stamp in text, stamp
    assert "three need attention" not in text.lower()  # the stale claim the SPEC script made
    for must in (
        "make demo-reset",
        "Mrs Chan",
        "Mr Cheung",
        "Mrs Wong",
        "Mrs Ho",
        "worker@demo.kaifong",
    ):
        assert must in text, must
    assert "Honest limitations" in text or "honest limitations" in text.lower()


def test_demo_script_replay(clean_db, login, db: Session) -> None:
    """Walks the README script step by step against the real app so it cannot rot."""
    worker, admin = login("worker@demo.kaifong"), login("admin@demo.kaifong")

    # 0:00 Today: eight called, five need attention, one urgent
    today = worker.get("/api/today").json()
    assert len(today["cards"]) == 8 and today["summary"] == {
        "need_attention": 5,
        "urgent": 1,
        "calls_completed": 7,
        "total": 8,
    }
    cards = {c["display_name_en"]: c for c in today["cards"]}
    assert (
        cards["Mr Cheung"]["level"] == "red"
        and cards["Mr Cheung"]["top_reasons"][0]["code"] == "UNREACHABLE"
    )
    assert cards["Mrs Chan"]["level"] == "amber"
    assert {r["code"] for r in cards["Mrs Chan"]["top_reasons"]} == {
        "PAIN_SEVERE",
        "HOMEBOUND",
    }  # top two on the card
    chan_detail = worker.get(f"/api/elders/{cards['Mrs Chan']['id']}").json()["risk"]["reasons"]
    assert {r["code"] for r in chan_detail} == {
        "PAIN_SEVERE",
        "HOMEBOUND",
        "SKIPPED_MEALS",
    }  # three reasons in all

    # 0:30 Mrs Chan's slide that monthly visits would miss
    chan = cards["Mrs Chan"]["id"]
    trends = worker.get(f"/api/elders/{chan}").json()["trends"]
    assert [t["went_out"] for t in trends[-5:]] == ["no"] * 5 and trends[0]["went_out"] == "yes"
    assert sum(t["appetite"] == "skipped_meals" for t in trends[-7:]) >= 2

    # 1:00 live call as Mrs Wong: greeted by name, remembers something, English subtitles available
    wong = cards["Mrs Wong"]["id"]
    start = worker.post("/api/calls", json={"elder_id": wong}).json()
    assert (
        start["greeting"]["text"].startswith("黃太")
        and "孫女下星期考試" in start["greeting"]["text"]
    )
    assert "AI helper" in start["greeting"]["text_en"] and "exam" in start["greeting"]["text_en"]
    cid = start["call_id"]
    worker.post(f"/api/calls/{cid}/turn", json={"text": "早晨，我食咗早餐"})

    # 1:45 she says she fell: fixed reply + a live red alert on the dashboard
    with worker.websocket_connect("/ws/dashboard") as dash:
        events = worker.post(
            f"/api/calls/{cid}/turn", json={"text": "我尋日跌親，起唔到身"}
        ).json()["events"]
        reply = next(e for e in events if e["type"] == "turn.reply_text")
        assert (
            reply["scripted_safety"]
            and "999" in reply["text"]
            and "Are you hurt" in reply["text_en"]
        )
        worker.post(f"/api/calls/{cid}/end")
        seen = []
        while True:
            ev = dash.receive_json()
            seen.append(ev)
            if ev["type"] == "call.ended":
                break
    alert = next(e for e in seen if e["type"] == "alert.created")
    assert alert["alert"]["level"] == "red" and alert["elder"]["display_name_en"] == "Mrs Wong"
    assert worker.get("/api/today").json()["summary"]["urgent"] == 2

    # 2:15 acknowledge, log a home visit; Mrs Ho shows nudge -> accepted -> action
    aid = alert["alert"]["id"]
    assert worker.post(f"/api/alerts/{aid}/acknowledge").json()["status"] == "acknowledged"
    done = worker.post(
        f"/api/alerts/{aid}/resolve",
        json={"outcome": "home_visit", "notes": "Visited, bruised knee."},
    )
    assert done.json()["status"] == "resolved"
    ho = worker.get(f"/api/elders/{cards['Mrs Ho']['id']}").json()
    assert ho["connection"] == {"suggested": 3, "accepted": 2, "followed_up": 2}
    assert {a["action"] for a in ho["actions"]} >= {"family_contacted", "centre_activity_arranged"}
    assert (
        worker.get("/api/today").json()["summary"]["urgent"] == 1
    )  # Wong is dealt with; Cheung still needs a person

    # extra for the presenter: decline fast-forward makes a fresh live toast
    assert admin.post(f"/api/demo/decline/{chan}").json()["level"] == "red"
    assert db.exec(select(CallSummary)).first() is not None
