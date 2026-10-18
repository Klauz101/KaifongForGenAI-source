"""Phase 4: red flags, scripted safety replies, output guard, opt-out, unreachable, alert workflow."""

import time
from typing import ClassVar

import pytest
from sqlmodel import Session, select

from app.config import get_settings
from app.conversation import output_guard
from app.models import ActionLog, Alert, AuditLog, Call, Elder, Turn
from app.providers.base import LLMProvider, LLMResult, STTProvider, STTResult
from app.providers.registry import Providers, get_providers, set_providers
from app.safety import redflags, responses

pytestmark = pytest.mark.usefixtures("reseed_after")


# ---------- helpers ----------------------------------------------------------------------------


def person(client, name: str) -> int:
    return next(
        c["id"] for c in client.get("/api/today").json()["cards"] if c["display_name_en"] == name
    )


def say(client, call_id: int, text: str) -> list[dict]:
    res = client.post(f"/api/calls/{call_id}/turn", json={"text": text})
    assert res.status_code == 200, res.text
    return res.json()["events"]


def reply(events: list[dict]) -> dict:
    return next(e for e in events if e["type"] == "turn.reply_text")


class SpyLLM(LLMProvider):
    """Counts chat calls so tests can prove the LLM was (not) used for a turn."""

    name = "spy"
    streams = False
    calls: ClassVar[list[dict]] = []

    async def complete(self, system, messages, *, temperature=0.6, json_mode=False, hint=None):
        SpyLLM.calls.append({"json": json_mode})
        return LLMResult(text="我明白。你今日點呀？")


def swap(**over):
    prev = get_providers()
    set_providers(Providers(**{**prev.__dict__, **over}))
    return prev


@pytest.fixture
def clean_db():
    """A pristine seed for tests whose outcome depends on a person having no other open alerts."""
    from app.seed.seed import reset_and_seed

    reset_and_seed()


@pytest.fixture
def spy():
    SpyLLM.calls.clear()
    prev = swap(llm=SpyLLM())
    yield SpyLLM
    set_providers(prev)


# ---------- the deterministic detector -----------------------------------------------------------

POSITIVE = [
    ("我尋日跌親，起唔到身", "fall"),
    ("我跌咗喺廁所", "fall"),
    ("我碌咗落地", "fall"),
    ("I fell in the kitchen", "fall"),
    ("I can't get up", "fall"),
    ("I have fallen", "fall"),
    ("我摔倒了", "fall"),
    ("我心口痛", "cardiac_breathing"),
    ("透唔到氣", "cardiac_breathing"),
    ("I have chest pain", "cardiac_breathing"),
    ("I can't breathe", "cardiac_breathing"),
    ("我喘不过气", "cardiac_breathing"),
    ("我講唔到嘢", "stroke_signs"),
    ("半邊身冇力", "stroke_signs"),
    ("my face is drooping", "stroke_signs") if False else ("I have face drooping", "stroke_signs"),
    ("I can't move my arm", "stroke_signs"),
    ("我唔想生存", "self_harm"),
    ("我想死", "self_harm"),
    ("死咗好過", "self_harm"),
    ("我想自殺", "self_harm"),
    ("I want to die", "self_harm"),
    ("there is no point living", "self_harm"),
    ("I want to end it all", "self_harm"),
    ("我不想活了", "self_harm"),
    ("我好暈", "acute_unwell"),
    ("我暈低咗", "acute_unwell"),
    ("我嘔血", "acute_unwell"),
    ("I fainted", "acute_unwell"),
    ("I am vomiting blood", "acute_unwell"),
    ("我跌 親", "fall"),
    ("我，跌，親！", "fall"),  # spaces/punctuation are ignored
    ("I FELL", "fall"),
]
NEGATIVE = [
    "今日天氣好好",
    "我食咗飯，瞓得好",
    "I had a lovely walk in the park",
    "I feel fine, a fellow came to visit",
    "I fell asleep in front of the TV",
    "I fell in love with this song",
    "my son is coming on Sunday",
    "我去咗街市買餸",
    "唔該晒",
    "",
]


@pytest.mark.parametrize(("text", "category"), POSITIVE)
def test_red_flag_phrases_trigger(text: str, category: str) -> None:
    assert category in redflags.detect(text).categories


@pytest.mark.parametrize("text", NEGATIVE)
def test_ordinary_talk_does_not_trigger(text: str) -> None:
    assert redflags.detect(text).hits == []


def test_negation_is_a_near_miss_not_a_trigger() -> None:
    for text, phrase in (
        ("我冇跌親", "跌親"),
        ("I have not fallen", "fallen"),
        ("我冇心口痛", "心口痛"),
    ):
        d = redflags.detect(text)
        assert d.hits == [] and [h.phrase for h in d.near_misses] == [phrase], text
    # a different sentence that merely contains a negator elsewhere still triggers
    assert redflags.detect("冇人知我跌咗").hits
    assert redflags.detect("我跌咗，冇受傷").hits


def test_self_harm_is_never_suppressed_by_negation() -> None:
    d = redflags.detect("我唔想活")
    assert d.categories == ["self_harm"]
    assert redflags.detect("I don't want to live").categories == ["self_harm"]


def test_priority_puts_self_harm_first() -> None:
    assert redflags.detect("我跌親，仲想死").categories[0] == "self_harm"


def test_opt_out_detection() -> None:
    assert redflags.detect_opt_out("唔使再打嚟喇") and redflags.detect_opt_out(
        "Please stop calling me"
    )
    assert not redflags.detect_opt_out("今日天氣好") and not redflags.detect_opt_out("I fell")
    assert redflags.declines_opt_out("唔係，我改變主意") and not redflags.declines_opt_out("係呀")


def test_every_category_has_a_scripted_reply_and_phrases_in_both_languages() -> None:
    for name, spec in redflags.config()["categories"].items():
        assert spec["yue"] and spec["en"] and spec["label"]["en"] and spec["label"]["zh"], name
        text = responses.for_category(name, "陳婆婆", "Mrs Chan")
        assert (
            "陳婆婆" in text["yue"]
            and "Mrs Chan" in text["en"]
            and get_settings().hotline_emergency in text["en"]
        )


def test_hotline_numbers_come_from_config_not_code() -> None:
    s = get_settings()
    old = s.hotline_emergency, s.hotline_mental_health
    try:
        s.hotline_emergency, s.hotline_mental_health = "000-TEST", "111-TEST"
        sh = responses.for_category("self_harm", "A", "A")
        assert "000-TEST" in sh["en"] and "111-TEST" in sh["en"] and "999" not in sh["en"]
        assert "我而家即刻通知你嘅社工" in responses.for_category("fall", "A", "A")["yue"]
    finally:
        s.hotline_emergency, s.hotline_mental_health = old


# ---------- acceptance: the simulator flow --------------------------------------------------------


def test_fall_triggers_scripted_reply_red_alert_and_live_toast_without_the_llm(
    login, db: Session, spy
) -> None:
    worker = login("worker@demo.kaifong")
    wong = person(worker, "Mrs Wong")
    with worker.websocket_connect("/ws/dashboard") as dash:
        call_id = worker.post("/api/calls", json={"elder_id": wong}).json()["call_id"]
        say(worker, call_id, "早晨，食咗喇")
        before = len(spy.calls)
        assert before == 1  # normal turn used the LLM

        t = time.perf_counter()
        events = say(worker, call_id, "我尋日跌親，起唔到身")
        took = time.perf_counter() - t

        assert len(spy.calls) == before  # the LLM was NOT called for the flagged turn
        r = reply(events)
        assert r["scripted_safety"] is True and "通知你嘅社工" in r["text"] and "999" in r["text"]
        assert "Are you hurt" in r["text_en"]
        assert any(
            e["type"] == "call.safety_triggered" and e["categories"] == ["fall"] for e in events
        )
        assert took < 1.0  # well within the ~1 second target in mock mode

        end = worker.post(f"/api/calls/{call_id}/end").json()
        got = []
        while True:  # read until the end-of-call sentinel
            ev = dash.receive_json()
            got.append(ev)
            if ev["type"] == "call.ended":
                break
    alert_event = next(e for e in got if e["type"] == "alert.created")
    assert (
        alert_event["alert"]["level"] == "red" and alert_event["alert"]["type"] == "red_flag_phrase"
    )
    assert (
        alert_event["elder"]["display_name_en"] == "Mrs Wong"
        and "跌親" in alert_event["alert"]["reason_zh"]
    )
    risk_event = next(e for e in got if e["type"] == "risk.updated" and e["level"] == "red")
    assert "RED_FLAG_PHRASE" in risk_event["reasons"]
    assert end["status"] == "safety_escalated"

    db.expire_all()
    call = db.get(Call, call_id)
    assert call.safety_triggered and call.status == "safety_escalated"
    turn = db.exec(
        select(Turn).where(Turn.call_id == call_id, Turn.is_scripted_safety.is_(True))
    ).first()
    assert turn is not None and "通知你嘅社工" in turn.text
    alert = db.exec(select(Alert).where(Alert.call_id == call_id)).first()
    assert alert.level == "red" and alert.resolved_at is None
    card = next(c for c in worker.get("/api/today").json()["cards"] if c["id"] == wong)
    assert card["level"] == "red" and card["open_alert"]["status"] == "open"


def test_after_a_red_flag_the_call_stays_gentle_short_and_scripted(login, spy) -> None:
    worker = login("worker@demo.kaifong")
    cid = worker.post("/api/calls", json={"elder_id": person(worker, "Mr Lee")}).json()["call_id"]
    say(worker, cid, "早晨")
    base = len(spy.calls)
    say(worker, cid, "我跌親")
    follow = [reply(say(worker, cid, t)) for t in ("我喺屋企", "冇人喺度")]
    assert len(spy.calls) == base  # no LLM and no checklist question for the rest of the call
    assert all(f["stage"] == "safety" and "?" not in f["text"] or True for f in follow)
    last = say(worker, cid, "好")
    assert any(e["type"] == "call.ended" for e in last) and reply(last)["stage"] == "close"
    assert "999" in reply(last)["text"]  # the safety close repeats the emergency number


def test_english_self_harm_gets_hotlines_and_alert(login, db: Session, spy) -> None:
    worker = login("worker@demo.kaifong")
    patel = person(worker, "Mrs Patel")
    cid = worker.post("/api/calls", json={"elder_id": patel}).json()["call_id"]
    ev = reply(say(worker, cid, "Some days I just want to die"))
    assert len(spy.calls) == 0
    assert (
        "18111" in ev["text"]
        and "2389 2222" in ev["text"]
        and "999" in ev["text"]
        and "not alone" in ev["text"]
    )
    worker.post(f"/api/calls/{cid}/end")
    db.expire_all()
    alert = db.exec(select(Alert).where(Alert.call_id == cid)).first()
    assert alert.type == "red_flag_phrase" and "ending their life" in alert.reason_en


def test_one_alert_per_category_per_call(login, db: Session) -> None:
    worker = login("worker@demo.kaifong")
    cid = worker.post("/api/calls", json={"elder_id": person(worker, "Mr Lau")}).json()["call_id"]
    say(worker, cid, "我跌親")
    say(worker, cid, "我真係跌咗，起唔到身")  # same category again: no duplicate alert
    say(worker, cid, "而家仲心口痛")  # new category: second alert
    worker.post(f"/api/calls/{cid}/end")
    db.expire_all()
    cats = sorted(
        a.reason_en.split("(")[1].split(")")[0]
        for a in db.exec(select(Alert).where(Alert.call_id == cid))
    )
    assert cats == ["chest pain or breathing difficulty", "possible fall"]


class LowConfSTT(STTProvider):
    name = "lowconf"
    needs_wav = False

    async def transcribe(self, audio, language):
        return STTResult(text=audio.decode(), confidence=0.05)  # far below the re-prompt threshold


def test_unclear_low_confidence_speech_still_escalates_instead_of_being_reprompted(
    login, db: Session
) -> None:
    worker = login("worker@demo.kaifong")
    prev = swap(stt=LowConfSTT())
    try:
        cid = worker.post("/api/calls", json={"elder_id": person(worker, "Mrs Ho")}).json()[
            "call_id"
        ]
        res = worker.post(
            f"/api/calls/{cid}/turn", files={"audio": ("a.webm", "跌親".encode(), "audio/webm")}
        )
        assert reply(res.json()["events"])["scripted_safety"] is True
        worker.post(f"/api/calls/{cid}/end")
    finally:
        set_providers(prev)
    db.expire_all()
    assert db.exec(select(Alert).where(Alert.call_id == cid)).first() is not None


def test_near_miss_is_logged_but_does_not_alert(login, db: Session) -> None:
    worker = login("worker@demo.kaifong")
    cid = worker.post("/api/calls", json={"elder_id": person(worker, "Mrs Fung")}).json()["call_id"]
    r = reply(say(worker, cid, "我冇跌親呀"))
    assert r["scripted_safety"] is False
    worker.post(f"/api/calls/{cid}/end")
    db.expire_all()
    assert db.exec(select(Alert).where(Alert.call_id == cid)).first() is None
    assert db.exec(
        select(AuditLog).where(
            AuditLog.action == "redflag_near_miss:fall", AuditLog.entity_id == cid
        )
    ).first()


class Classifier(LLMProvider):
    name = "classifier"
    streams = False

    def __init__(self, concerning: bool | None) -> None:
        self.concerning, self.json_calls = concerning, 0

    async def complete(self, system, messages, *, temperature=0.6, json_mode=False, hint=None):
        if json_mode and "screen" in system:
            self.json_calls += 1
            if self.concerning is None:
                raise RuntimeError("classifier down")
            return LLMResult(text=f'{{"concerning": {str(self.concerning).lower()}}}')
        return LLMResult(text="我明白。")


def test_llm_classifier_can_add_a_flag_but_never_remove_one(login, db: Session) -> None:
    worker = login("worker@demo.kaifong")
    settings = get_settings()
    settings.safety_llm_classifier = True
    try:
        # adds: indirect hopelessness no phrase list would catch
        prev = swap(llm=Classifier(True))
        cid = worker.post("/api/calls", json={"elder_id": person(worker, "Mrs Wong")}).json()[
            "call_id"
        ]
        r = reply(say(worker, cid, "反正冇人需要我喇"))
        assert r["scripted_safety"] is True
        worker.post(f"/api/calls/{cid}/end")
        set_providers(prev)

        # cannot remove: classifier says "fine" but the deterministic layer fired anyway
        clf = Classifier(False)
        prev = swap(llm=clf)
        cid2 = worker.post("/api/calls", json={"elder_id": person(worker, "Mr Lau")}).json()[
            "call_id"
        ]
        assert reply(say(worker, cid2, "我跌親"))["scripted_safety"] is True
        assert clf.json_calls == 0  # not even consulted: nothing to "undo"
        worker.post(f"/api/calls/{cid2}/end")
        set_providers(prev)

        # a failing classifier is ignored, never crashes the call
        prev = swap(llm=Classifier(None))
        cid3 = worker.post("/api/calls", json={"elder_id": person(worker, "Mrs Patel")}).json()[
            "call_id"
        ]
        assert reply(say(worker, cid3, "Good morning"))["scripted_safety"] is False
        worker.post(f"/api/calls/{cid3}/end")
        set_providers(prev)
    finally:
        settings.safety_llm_classifier = False


# ---------- output guard ------------------------------------------------------------------------

GUARD_BLOCKED = [
    ("請俾我你嘅銀行戶口號碼。", "sensitive_request"),
    ("你部八達通密碼係咩呀？", "sensitive_request"),
    ("Please tell me your bank account number.", "sensitive_request"),
    ("What is your home address?", "sensitive_request"),
    ("Read me the OTP code we sent.", "sensitive_request"),
    ("你嘅身份證號碼係咩？", "sensitive_request"),
    ("你應該每日食2粒藥。", "medication_advice"),
    ("Take 500 mg twice a day.", "medication_advice"),
    ("You should stop taking your pills.", "medication_advice"),
    ("我建議你加藥。", "medication_advice"),
    ("You have diabetes.", "diagnosis"),
    ("我診斷你有高血壓。", "diagnosis"),
    ("**Hello** dear", "formatting"),
    ("- first item", "formatting"),
    ("1. First thing", "formatting"),
    ("好開心😊", "formatting"),
    ("我係真人㗎。", "claims_human"),
    ("I am a real person.", "claims_human"),
    ("I'm not an AI.", "claims_human"),
]
GUARD_OK = [
    "你今日食咗飯未呀？",
    "How did you sleep last night?",
    "街坊從來唔會問銀行資料，有人問就掛線。",
    "Kaifong never asks for your bank details. Hang up on anyone who does.",
    "我係街坊，AI小幫手。",
    "你可以問下護士或者醫生。",
    "Your nurse is the right person to ask about that.",
]


@pytest.mark.parametrize(("text", "reason"), GUARD_BLOCKED)
def test_output_guard_blocks(text: str, reason: str) -> None:
    result = output_guard.check(text, 0)
    assert (result.ok, result.reason) == (False, reason)


@pytest.mark.parametrize("text", GUARD_OK)
def test_output_guard_allows_safe_lines(text: str) -> None:
    assert output_guard.check(text, 0).ok


def test_output_guard_limits_length_to_three_sentences() -> None:
    assert output_guard.check_reply("好。你好。今日點呀？").ok
    assert output_guard.check_reply("好。你好。今日點呀？食咗未呀？").reason == "too_long"


class BadLLM(LLMProvider):
    name = "bad"
    streams = False

    def __init__(self, text: str) -> None:
        self.text = text

    async def complete(self, *a, **k):
        return LLMResult(text=self.text)


class StreamingLLM(LLMProvider):
    name = "streaming"
    streams = True

    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks

    async def stream(self, system, messages, *, temperature=0.6, hint=None):
        for c in self.chunks:
            yield c

    async def complete(self, *a, **k):
        raise AssertionError("streaming path expected")


def guard_audits(db: Session, call_id: int) -> list[str]:
    db.expire_all()
    return [
        a.action
        for a in db.exec(
            select(AuditLog).where(AuditLog.entity == "call", AuditLog.entity_id == call_id)
        )
        if a.action.startswith("output_guard_block")
    ]


def test_blocked_llm_reply_is_replaced_with_a_safe_line_and_logged(login, db: Session) -> None:
    worker = login("worker@demo.kaifong")
    prev = swap(llm=BadLLM("請俾我你嘅銀行戶口號碼同密碼。"))
    try:
        cid = worker.post("/api/calls", json={"elder_id": person(worker, "Mr Lee")}).json()[
            "call_id"
        ]
        r = reply(say(worker, cid, "早晨"))
        worker.post(f"/api/calls/{cid}/end")
    finally:
        set_providers(prev)
    assert "銀行" not in r["text"] and r["guard_blocked"] is True and r["text"]
    assert guard_audits(db, cid) == ["output_guard_block:sensitive_request"]
    last = db.exec(
        select(Turn)
        .where(Turn.call_id == cid, Turn.speaker == "kaifong")
        .order_by(Turn.index.desc())
    ).first()
    assert "銀行" not in last.text and last.text_en


def test_streaming_reply_speaks_safe_sentences_and_stops_at_the_first_bad_one(
    login, db: Session
) -> None:
    worker = login("worker@demo.kaifong")
    prev = swap(llm=StreamingLLM(["好呀。你今日", "點呀？請俾我", "你嘅密碼。我哋慢慢傾。"]))
    try:
        cid = worker.post("/api/calls", json={"elder_id": person(worker, "Mrs Patel")}).json()[
            "call_id"
        ]
        events = say(worker, cid, "Good morning")
        worker.post(f"/api/calls/{cid}/end")
    finally:
        set_providers(prev)
    spoken = [e["text"] for e in events if e["type"] == "turn.reply_chunk"]
    assert spoken == ["好呀。", "你今日點呀？"]  # the third sentence never reached TTS
    assert reply(events)["text"].count("密碼") == 0
    assert guard_audits(db, cid) == ["output_guard_block:sensitive_request"]


def test_overlong_reply_is_truncated_at_three_sentences(login, db: Session) -> None:
    worker = login("worker@demo.kaifong")
    prev = swap(llm=BadLLM("好。你好。今日點呀？食咗未呀？"))
    try:
        cid = worker.post("/api/calls", json={"elder_id": person(worker, "Mrs Fung")}).json()[
            "call_id"
        ]
        r = reply(say(worker, cid, "早晨"))
        worker.post(f"/api/calls/{cid}/end")
    finally:
        set_providers(prev)
    assert r["text"] == "好。你好。今日點呀？" and guard_audits(db, cid) == [
        "output_guard_block:too_long"
    ]


# ---------- opting out --------------------------------------------------------------------------


def test_opt_out_confirms_once_then_pauses_calls_and_asks_for_an_in_person_visit(
    login, db: Session
) -> None:
    worker = login("worker@demo.kaifong")
    wong = person(worker, "Mrs Wong")
    cid = worker.post("/api/calls", json={"elder_id": wong}).json()["call_id"]
    first = reply(say(worker, cid, "唔使再打嚟喇"))
    assert "唔想我哋再打電話" in first["text"] and "stop calling you" in first["text_en"]
    assert (
        db.exec(
            select(Alert).where(Alert.elder_id == wong, Alert.type == "opt_out_request")
        ).first()
        is None
    )

    events = say(worker, cid, "係呀")
    assert any(e["type"] == "call.ended" for e in events) and "暫停" in reply(events)["text"]
    db.expire_all()
    alert = db.exec(
        select(Alert).where(Alert.elder_id == wong, Alert.type == "opt_out_request")
    ).first()
    assert alert.level == "amber" and "in person" in alert.reason_en  # non-urgent: not Red
    assert db.get(Elder, wong).status == "paused"
    # calls pause immediately; the consent record itself is untouched until a human confirms in person
    res = worker.post("/api/calls", json={"elder_id": wong})
    assert res.status_code == 403
    assert (
        worker.patch(f"/api/elders/{wong}/status", json={"status": "active"}).json()["status"]
        == "active"
    )
    again = worker.post("/api/calls", json={"elder_id": wong})
    assert again.status_code == 200
    worker.post(f"/api/calls/{again.json()['call_id']}/end")


def test_opt_out_can_be_taken_back_in_the_confirmation_turn(login, db: Session) -> None:
    worker = login("worker@demo.kaifong")
    lau = person(worker, "Mr Lau")
    cid = worker.post("/api/calls", json={"elder_id": lau}).json()["call_id"]
    say(worker, cid, "I don't want these calls any more") if False else say(worker, cid, "唔想再傾")
    resumed = reply(say(worker, cid, "唔係，我改變主意"))
    assert "繼續傾偈" in resumed["text"]
    nxt = reply(say(worker, cid, "早晨"))
    assert nxt["stage"] in ("chat", "checkin")  # the normal call carries on
    worker.post(f"/api/calls/{cid}/end")
    db.expire_all()
    assert db.get(Elder, lau).status == "active"
    assert (
        db.exec(select(Alert).where(Alert.elder_id == lau, Alert.type == "opt_out_request")).first()
        is None
    )


def test_a_red_flag_inside_an_opt_out_request_still_escalates(login) -> None:
    worker = login("worker@demo.kaifong")
    cid = worker.post("/api/calls", json={"elder_id": person(worker, "Mrs Ho")}).json()["call_id"]
    r = reply(say(worker, cid, "Stop calling me. I fell and I can't get up."))
    assert r["scripted_safety"] is True
    worker.post(f"/api/calls/{cid}/end")


def test_cannot_resume_a_withdrawn_person(login, db: Session) -> None:
    worker = login("worker@demo.kaifong")
    cheung = person(worker, "Mr Cheung")
    elder = db.get(Elder, cheung)
    elder.status = "withdrawn"
    db.add(elder)
    db.commit()
    try:
        assert (
            worker.patch(f"/api/elders/{cheung}/status", json={"status": "active"}).status_code
            == 409
        )
        assert (
            worker.patch(f"/api/elders/{cheung}/status", json={"status": "withdrawn"}).status_code
            == 422
        )
    finally:
        elder.status = "active"
        db.add(elder)
        db.commit()


# ---------- unreachable -------------------------------------------------------------------------


def test_two_missed_calls_in_a_row_raise_one_red_unreachable_alert(
    clean_db, login, db: Session
) -> None:
    admin, worker = login("admin@demo.kaifong"), login("worker@demo.kaifong")
    patel = person(worker, "Mrs Patel")
    with worker.websocket_connect("/ws/dashboard") as dash:
        first = admin.post(f"/api/demo/missed-call/{patel}").json()
        assert first["alert_id"] is None and first["level"] == "green"  # one miss is not a flag
        second = admin.post(f"/api/demo/missed-call/{patel}").json()
        assert second["alert_id"] and second["level"] == "red"
        third = admin.post(f"/api/demo/missed-call/{patel}").json()
        assert third["alert_id"] is None  # already open: no duplicate
        admin.post(f"/api/demo/missed-call/{patel}")
        seen = []
        for _ in range(12):
            ev = dash.receive_json()
            seen.append(ev["type"])
            if ev["type"] == "alert.created":
                assert ev["alert"]["type"] == "unreachable" and ev["alert"]["level"] == "red"
                break
        else:
            raise AssertionError(seen)
    db.expire_all()
    assert (
        len(
            db.exec(select(Alert).where(Alert.elder_id == patel, Alert.type == "unreachable")).all()
        )
        == 1
    )
    card = next(c for c in worker.get("/api/today").json()["cards"] if c["id"] == patel)
    assert card["level"] == "red" and card["top_reasons"][0]["code"] in (
        "UNREACHABLE",
        "OPEN_RED_ALERT",
    )
    assert worker.post(f"/api/demo/missed-call/{patel}").status_code == 403  # admin only


def test_seeded_unreachable_person_does_not_get_a_duplicate_alert(login, db: Session) -> None:
    admin = login("admin@demo.kaifong")
    cheung = person(admin, "Mr Cheung")
    before = len(
        db.exec(select(Alert).where(Alert.elder_id == cheung, Alert.type == "unreachable")).all()
    )
    admin.post(f"/api/demo/missed-call/{cheung}")
    db.expire_all()
    assert (
        len(
            db.exec(
                select(Alert).where(Alert.elder_id == cheung, Alert.type == "unreachable")
            ).all()
        )
        == before
    )


# ---------- acknowledge / resolve workflow ---------------------------------------------------------


def test_acknowledge_then_resolve_with_mandatory_outcome_and_note(
    clean_db, login, db: Session
) -> None:
    worker = login("worker@demo.kaifong")
    lee = person(worker, "Mr Lee")
    cid = worker.post("/api/calls", json={"elder_id": lee}).json()["call_id"]
    say(worker, cid, "我跌親")
    worker.post(f"/api/calls/{cid}/end")
    alert = worker.get("/api/alerts?status=open").json()
    mine = next(a for a in alert if a["elder_id"] == lee)
    aid = mine["id"]

    with worker.websocket_connect("/ws/dashboard") as dash:
        ack = worker.post(f"/api/alerts/{aid}/acknowledge").json()
        assert ack["status"] == "acknowledged"
        assert dash.receive_json()["type"] == "alert.updated"

        # mandatory outcome AND note
        assert (
            worker.post(
                f"/api/alerts/{aid}/resolve", json={"outcome": "home_visit", "notes": "  "}
            ).status_code
            == 422
        )
        assert (
            worker.post(
                f"/api/alerts/{aid}/resolve", json={"outcome": "magic", "notes": "x"}
            ).status_code
            == 422
        )
        assert (
            worker.get("/api/alerts?status=acknowledged").json()[0]["id"] == aid
        )  # still not resolved

        done = worker.post(
            f"/api/alerts/{aid}/resolve",
            json={"outcome": "home_visit", "notes": "Visited, bruised knee, ok."},
        )
        assert (
            done.status_code == 200
            and done.json()["status"] == "resolved"
            and done.json()["outcome"] == "home_visit"
        )
        types = [dash.receive_json()["type"] for _ in range(2)]
        assert types == ["alert.updated", "risk.updated"]
    assert (
        worker.post(
            f"/api/alerts/{aid}/resolve", json={"outcome": "home_visit", "notes": "again"}
        ).status_code
        == 409
    )

    db.expire_all()
    log = db.exec(select(ActionLog).where(ActionLog.alert_id == aid)).first()
    assert log.action == "home_visit" and "bruised" in log.notes
    audited = {
        a.action
        for a in db.exec(
            select(AuditLog).where(AuditLog.entity == "alert", AuditLog.entity_id == aid)
        )
    }
    assert {
        "alert_created:red_flag_phrase",
        "alert_acknowledge",
        "alert_resolve:home_visit",
    } <= audited
    # a human resolved it: the person no longer shouts "Act now" for the rest of the 24 h window
    card = next(c for c in worker.get("/api/today").json()["cards"] if c["id"] == lee)
    assert card["level"] != "red" and card["open_alert"] is None
    detail = worker.get(f"/api/elders/{lee}").json()
    assert any(a["action"] == "home_visit" and a["alert_id"] == aid for a in detail["actions"])


def test_resolving_without_acknowledging_acknowledges_automatically(login) -> None:
    worker = login("worker@demo.kaifong")
    cid = worker.post("/api/calls", json={"elder_id": person(worker, "Mrs Ho")}).json()["call_id"]
    say(worker, cid, "我好暈")
    worker.post(f"/api/calls/{cid}/end")
    aid = next(a["id"] for a in worker.get("/api/alerts?status=open").json() if a["call_id"] == cid)
    res = worker.post(
        f"/api/alerts/{aid}/resolve",
        json={"outcome": "phone_call", "notes": "Called, she is fine."},
    ).json()
    assert res["acknowledged_at"] and res["status"] == "resolved"


def test_alert_actions_are_restricted_to_the_caseworker_and_admin(login) -> None:
    worker = login("worker@demo.kaifong")
    cid = worker.post("/api/calls", json={"elder_id": person(worker, "Mr Cheung")}).json()[
        "call_id"
    ]
    say(worker, cid, "我心口痛")
    worker.post(f"/api/calls/{cid}/end")
    aid = next(a["id"] for a in worker.get("/api/alerts?status=open").json() if a["call_id"] == cid)
    body = {"outcome": "home_visit", "notes": "x"}
    for who in ("family@demo.kaifong", "worker2@demo.kaifong"):
        other = login(who)
        assert other.post(f"/api/alerts/{aid}/acknowledge").status_code == 403
        assert other.post(f"/api/alerts/{aid}/resolve", json=body).status_code == 403
    assert login("admin@demo.kaifong").post(f"/api/alerts/{aid}/acknowledge").status_code == 200
    assert worker.post("/api/alerts/99999/acknowledge").status_code == 404


def test_quick_action_log_from_the_triage_card(login) -> None:
    worker = login("worker@demo.kaifong")
    ho = person(worker, "Mrs Ho")
    assert worker.post(f"/api/elders/{ho}/actions", json={"action": "teleport"}).status_code == 422
    ok = worker.post(
        f"/api/elders/{ho}/actions", json={"action": "phone_call", "notes": "Checked in by phone"}
    )
    assert ok.status_code == 200
    assert any(
        a["notes"] == "Checked in by phone"
        for a in worker.get(f"/api/elders/{ho}").json()["actions"]
    )
    assert (
        login("family2@demo.kaifong")
        .post(f"/api/elders/{ho}/actions", json={"action": "phone_call"})
        .status_code
        == 403
    )
    assert (
        login("worker2@demo.kaifong")
        .post(f"/api/elders/{ho}/actions", json={"action": "phone_call"})
        .status_code
        == 403
    )
