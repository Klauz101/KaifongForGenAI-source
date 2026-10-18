"""Phase 4b: telephony adapter and cost tracking."""

import asyncio
import base64
from datetime import UTC

import httpx
import pytest
from sqlmodel import Session, select

from app.analysis import cost
from app.config import get_settings
from app.conversation import service
from app.models import Alert, Call, Consent, Elder
from app.providers.base import (
    LLMProvider,
    LLMResult,
    STTProvider,
    STTResult,
    TTSProvider,
    TTSResult,
)
from app.providers.registry import Providers, get_providers, set_providers
from app.telephony import audio_store
from app.telephony.base import MockTelephony, normalise_number
from app.telephony.registry import set_telephony
from app.usage import report_tokens

pytestmark = pytest.mark.usefixtures("reseed_after")

PRICES = {
    "currency": "HKD",
    "calls_per_month": 30,
    "prices": {
        "stt_per_minute": 0.12,
        "tts_per_1k_chars": 0.8,
        "llm_input_per_1m_tokens": 20.0,
        "llm_output_per_1m_tokens": 60.0,
        "telephony_per_minute": 0.5,
    },
}
NO_PRICES = {"currency": "HKD", "calls_per_month": 30, "prices": dict.fromkeys(PRICES["prices"])}


def person(client, name: str) -> int:
    return next(
        c["id"] for c in client.get("/api/today").json()["cards"] if c["display_name_en"] == name
    )


# ---------- cost estimation ------------------------------------------------------------------------


def test_missing_prices_mean_not_yet_measured_never_zero() -> None:
    est = cost.estimate({"tts_chars": 500, "llm_chat_in": 1000}, NO_PRICES)
    assert est["hkd"] is None and est["missing"] == ["llm_input_per_1m_tokens", "tts_per_1k_chars"]
    summary = cost.summarise([{"tts_chars": 500}], NO_PRICES)
    assert summary["status"] == "not yet measured" and summary["avg_cost_per_call"] is None
    assert summary["est_monthly_per_person"] is None and summary["calls_measured"] == 1


def test_cost_arithmetic_per_call_and_per_month() -> None:
    usage = {
        "stt_seconds": 120,
        "tts_chars": 2000,
        "llm_chat_in": 500_000,
        "llm_extract_in": 500_000,
        "llm_chat_out": 100_000,
        "telephony_minutes": 4,
    }
    est = cost.estimate(usage, PRICES)
    # stt 2 min x 0.12 + tts 2 x 0.8 + in 1.0 x 20 + out 0.1 x 60 + 4 min x 0.5
    assert est["parts"] == {
        "stt": 0.24,
        "tts": 1.6,
        "llm_input": 20.0,
        "llm_output": 6.0,
        "telephony": 2.0,
    }
    assert est["hkd"] == pytest.approx(29.84) and est["missing"] == []
    summary = cost.summarise([usage, {"tts_chars": 1000}], PRICES)
    assert summary["avg_cost_per_call"] == pytest.approx((29.84 + 0.8) / 2, abs=1e-3)
    assert summary["est_monthly_per_person"] == pytest.approx((29.84 + 0.8) / 2 * 30, abs=0.01)
    assert summary["status"] == "ok" and summary["calls_per_month"] == 30


def test_components_never_used_do_not_need_a_price() -> None:
    partial = {**PRICES, "prices": {**PRICES["prices"], "telephony_per_minute": None}}
    assert cost.estimate({"tts_chars": 1000}, partial)["hkd"] == 0.8  # no telephony used: fine


def test_shipped_pricing_file_has_every_key_and_no_invented_prices() -> None:
    p = cost.load_pricing()
    assert set(p["prices"]) == set(PRICES["prices"]) and all(
        v is None for v in p["prices"].values()
    )
    assert p["calls_per_month"] == 30


# ---------- metering inside a real call ----------------------------------------------------------


class MeteredLLM(LLMProvider):
    name = "metered"
    streams = False

    async def complete(self, system, messages, *, temperature=0.6, json_mode=False, hint=None):
        report_tokens(10, 5)
        if json_mode:
            import json

            from app.analysis.mock_extractor import extract

            report_tokens(190, 45)
            return LLMResult(text=json.dumps(extract(hint["turns"], hint.get("nudge_turn"))))
        return LLMResult(text="我明白。你今日點呀？")


class AudioTTS(TTSProvider):
    name = "audio"

    async def synthesize(self, text, language):
        return TTSResult(audio=b"MP3" + text.encode(), mime="audio/mpeg")


class WavSTT(STTProvider):
    name = "wav"
    needs_wav = True

    async def transcribe(self, audio, language):
        return STTResult(text="早晨，食咗喇", confidence=0.9)


def test_a_call_meters_stt_tts_and_llm_use_separately(login, db: Session, monkeypatch) -> None:
    async def fake_wav(_: bytes) -> bytes:
        return b"\0" * (44 + 32000 * 2)  # exactly 2.0 seconds of 16 kHz mono 16-bit audio

    monkeypatch.setattr(service, "to_wav_16k", fake_wav)
    worker = login("worker@demo.kaifong")
    prev = get_providers()
    set_providers(
        Providers(
            **{
                **prev.__dict__,
                "llm": MeteredLLM(),
                "tts": AudioTTS(),
                "stt": WavSTT(),
                "llm_extract": MeteredLLM(),
            }
        )
    )
    try:
        cid = worker.post("/api/calls", json={"elder_id": person(worker, "Mr Lau")}).json()[
            "call_id"
        ]
        res = worker.post(f"/api/calls/{cid}/turn", files={"audio": ("a.webm", b"x", "audio/webm")})
        spoken = "".join(e["text"] for e in res.json()["events"] if e["type"] == "turn.reply_chunk")
        worker.post(f"/api/calls/{cid}/end")
        for _ in range(60):  # post-call analysis runs in the background
            db.expire_all()
            u = db.get(Call, cid).usage
            if "llm_extract_in" in u:
                break
            import time

            time.sleep(0.1)
    finally:
        set_providers(prev)
    u = db.get(Call, cid).usage
    assert u["stt_seconds"] == pytest.approx(2.0)
    assert u["llm_chat_in"] == 10 and u["llm_chat_out"] == 5  # one chat reply
    assert (
        u["llm_extract_in"] == 200 and u["llm_extract_out"] == 50
    )  # extraction counted separately
    greeting = db.exec(select(Call).where(Call.id == cid)).first()
    assert greeting and u["tts_chars"] >= len(spoken) > 0  # greeting + the spoken reply


def test_cost_endpoint_is_admin_only_and_honest_about_missing_prices(login, monkeypatch) -> None:
    admin = login("admin@demo.kaifong")
    assert login("worker@demo.kaifong").get("/api/metrics/cost").status_code == 403
    body = admin.get("/api/metrics/cost").json()
    assert body["calls_measured"] >= 1 and body["status"] == "not yet measured"
    assert body["avg_cost_per_call"] is None and "tts_per_1k_chars" in body["missing_prices"]
    monkeypatch.setattr(cost, "load_pricing", lambda: PRICES)
    priced = admin.get("/api/metrics/cost").json()
    assert (
        priced["status"] == "ok"
        and priced["est_monthly_per_person"] > 0
        and priced["calls"][0]["hkd"] is not None
    )


# ---------- telephony adapter -----------------------------------------------------------------------


@pytest.fixture
def phone():
    """Mock carrier, public URL configured, one number allowed."""
    s = get_settings()
    saved = (s.public_base_url, s.telephony_allowed_numbers)
    s.public_base_url, s.telephony_allowed_numbers = "https://kf.example.test", "+85291234567"
    mock = MockTelephony()
    set_telephony(mock)
    yield mock
    s.public_base_url, s.telephony_allowed_numbers = saved
    set_telephony(None)


def set_phone(db: Session, name: str, number: str) -> int:
    e = db.exec(select(Elder).where(Elder.display_name_en == name)).first()
    e.phone = number
    db.add(e)
    db.commit()
    return e.id


def dial(worker, db: Session, name: str, number: str = "9123 4567") -> dict:
    eid = set_phone(db, name, number)
    res = worker.post("/api/telephony/call", json={"elder_id": eid})
    assert res.status_code == 200, res.text
    return res.json()


def hook(client, path: str, data: dict | None = None, sig: str | None = "mock"):
    return client.post(path, data=data or {}, headers={"x-twilio-signature": sig} if sig else {})


def test_number_normalisation() -> None:
    assert (
        normalise_number("9123 4567") == "+85291234567"
        and normalise_number("+44 20 7946 0958") == "+442079460958"
    )


def test_only_allowlisted_numbers_can_ever_be_dialled(login, db: Session, phone) -> None:
    worker = login("worker@demo.kaifong")
    eid = set_phone(db, "Mrs Fung", "0000 0007")  # a seeded fake number: not on the list
    res = worker.post("/api/telephony/call", json={"elder_id": eid})
    assert res.status_code == 403 and "ALLOWED_NUMBERS" in res.json()["detail"]
    assert phone.placed == [] and not service.runtimes
    s = get_settings()
    s.telephony_allowed_numbers = ""  # empty list = nothing may be dialled
    set_phone(db, "Mrs Fung", "9123 4567")
    assert worker.post("/api/telephony/call", json={"elder_id": eid}).status_code == 403


def test_placing_a_call_applies_consent_caseload_and_records_the_carrier(
    login, db: Session, phone
) -> None:
    worker = login("worker@demo.kaifong")
    out = dial(worker, db, "Mrs Patel")
    assert phone.placed[0]["to"] == "+85291234567"
    assert (
        phone.placed[0]["voice_url"]
        == f"https://kf.example.test/api/telephony/twilio/voice?call_id={out['call_id']}"
    )
    call = db.get(Call, out["call_id"])
    assert call.mode == "phone" and call.providers_used["telephony_sid"] == out["carrier_sid"]
    worker.post(f"/api/calls/{out['call_id']}/end")

    other = login("worker2@demo.kaifong")
    assert other.post("/api/telephony/call", json={"elder_id": call.elder_id}).status_code == 403
    consent = db.exec(select(Consent).where(Consent.elder_id == call.elder_id)).first()
    from datetime import datetime

    consent.withdrawn_at = datetime.now(UTC)
    db.add(consent)
    db.commit()
    try:
        before = len(phone.placed)
        assert (
            worker.post("/api/telephony/call", json={"elder_id": call.elder_id}).status_code == 403
        )
        assert len(phone.placed) == before  # no consent: never dialled
    finally:
        consent.withdrawn_at = None
        db.add(consent)
        db.commit()


def test_calls_through_the_api_must_use_the_telephony_endpoint(login) -> None:
    worker = login("worker@demo.kaifong")
    assert (
        worker.post(
            "/api/calls", json={"elder_id": person(worker, "Mr Lee"), "mode": "phone"}
        ).status_code
        == 422
    )


def test_webhooks_reject_missing_or_wrong_signatures(login, db: Session, phone) -> None:
    worker = login("worker@demo.kaifong")
    out = dial(worker, db, "Mr Lau")
    cid = out["call_id"]
    for path in ("voice", "gather", "status"):
        url = f"/api/telephony/twilio/{path}?call_id={cid}"
        assert hook(worker, url, sig=None).status_code == 403
        assert hook(worker, url, sig="forged").status_code == 403
    worker.post(f"/api/calls/{cid}/end")


def test_voice_webhook_greets_in_cantonese_and_listens_for_cantonese(
    login, db: Session, phone
) -> None:
    worker = login("worker@demo.kaifong")
    cid = dial(worker, db, "Mr Lau")["call_id"]
    xml = hook(worker, f"/api/telephony/twilio/voice?call_id={cid}", {"CallSid": "CAmock0001"}).text
    assert 'input="speech"' in xml and 'language="yue-Hant-HK"' in xml
    assert f"https://kf.example.test/api/telephony/twilio/gather?call_id={cid}" in xml
    assert (
        "劉生" in xml and "AI小幫手" in xml
    )  # mock TTS: the carrier speaks the AI-disclosing greeting
    worker.post(f"/api/calls/{cid}/end")


def test_voice_webhook_plays_our_tts_audio_when_we_have_it(login, db: Session, phone) -> None:
    worker = login("worker@demo.kaifong")
    prev = get_providers()
    set_providers(Providers(**{**prev.__dict__, "tts": AudioTTS()}))
    try:
        cid = dial(worker, db, "Mrs Patel")["call_id"]
        xml = hook(worker, f"/api/telephony/twilio/voice?call_id={cid}").text
    finally:
        set_providers(prev)
    token = xml.split("/api/telephony/audio/")[1].split("<")[0]
    audio = worker.get(f"/api/telephony/audio/{token}")
    assert (
        audio.status_code == 200
        and audio.content.startswith(b"MP3")
        and audio.headers["content-type"] == "audio/mpeg"
    )
    assert worker.get("/api/telephony/audio/not-a-token").status_code == 404
    worker.post(f"/api/calls/{cid}/end")


def test_a_spoken_turn_gets_a_spoken_reply_and_another_gather(login, db: Session, phone) -> None:
    worker = login("worker@demo.kaifong")
    cid = dial(worker, db, "Mr Lau")["call_id"]
    xml = hook(
        worker,
        f"/api/telephony/twilio/gather?call_id={cid}&empty=0",
        {"SpeechResult": "早晨，食咗喇", "Confidence": "0.91"},
    ).text
    assert "<Say" in xml and xml.count("<Gather") >= 1 and "Hangup" not in xml
    bye = hook(
        worker,
        f"/api/telephony/twilio/gather?call_id={cid}&empty=0",
        {"SpeechResult": "好啦，拜拜"},
    ).text
    assert "<Hangup" in bye  # goodbye ends the call
    assert db.get(Call, cid).status == "completed"


def test_red_flag_spoken_on_the_phone_is_screened_before_the_llm(login, db: Session, phone) -> None:
    worker = login("worker@demo.kaifong")
    cid = dial(worker, db, "Mrs Patel")["call_id"]
    xml = hook(
        worker,
        f"/api/telephony/twilio/gather?call_id={cid}&empty=0",
        {"SpeechResult": "I fell and I can't get up", "Confidence": "0.4"},
    ).text
    assert "not alone" not in xml and "call 999" in xml and "social worker" in xml
    alert = db.exec(select(Alert).where(Alert.call_id == cid)).first()
    assert alert and alert.level == "red"  # even at 0.4 confidence the flag fired, no re-prompt
    worker.post(f"/api/calls/{cid}/end")


def test_low_confidence_speech_is_reprompted_and_silence_eventually_hangs_up(
    login, db: Session, phone
) -> None:
    worker = login("worker@demo.kaifong")
    cid = dial(worker, db, "Mr Lau")["call_id"]
    low = hook(
        worker,
        f"/api/telephony/twilio/gather?call_id={cid}&empty=0",
        {"SpeechResult": "mumble", "Confidence": "0.05"},
    ).text
    assert "冇聽清楚" in low
    for n in (1, 2):
        assert (
            "<Hangup"
            not in hook(worker, f"/api/telephony/twilio/gather?call_id={cid}&empty={n}", {}).text
        )
    assert "<Hangup" in hook(worker, f"/api/telephony/twilio/gather?call_id={cid}&empty=3", {}).text
    assert db.get(Call, cid).status == "aborted"


def test_missed_phone_calls_twice_raise_the_unreachable_alert(login, db: Session, phone) -> None:
    worker = login("worker@demo.kaifong")
    first = dial(worker, db, "Mrs Wong")["call_id"]
    eid = db.get(Call, first).elder_id
    assert (
        hook(
            worker, f"/api/telephony/twilio/status?call_id={first}", {"CallStatus": "no-answer"}
        ).json()["recorded"]
        == "no_answer"
    )
    assert (
        db.exec(select(Alert).where(Alert.elder_id == eid, Alert.type == "unreachable")).first()
        is None
    )
    second = worker.post("/api/telephony/call", json={"elder_id": eid}).json()["call_id"]
    hook(worker, f"/api/telephony/twilio/status?call_id={second}", {"CallStatus": "busy"})
    db.expire_all()
    assert db.get(Call, first).status == "no_answer" and db.get(Call, second).status == "no_answer"
    alert = db.exec(select(Alert).where(Alert.elder_id == eid, Alert.type == "unreachable")).first()
    assert alert and alert.level == "red"
    assert not service.runtimes


def test_completed_phone_call_meters_started_minutes_and_ends_the_call(
    login, db: Session, phone
) -> None:
    worker = login("worker@demo.kaifong")
    cid = dial(worker, db, "Mr Lau")["call_id"]
    hook(worker, f"/api/telephony/twilio/gather?call_id={cid}&empty=0", {"SpeechResult": "早晨"})
    hook(
        worker,
        f"/api/telephony/twilio/status?call_id={cid}",
        {"CallStatus": "completed", "CallDuration": "61"},
    )
    db.expire_all()
    assert db.get(Call, cid).usage["telephony_minutes"] == 2  # 61 s = 2 started minutes
    assert (
        db.get(Call, cid).status == "aborted" and cid not in service.runtimes
    )  # hung up before goodbye


# ---------- Twilio provider: request shape and signature validation --------------------------------


def test_twilio_places_calls_with_the_documented_request() -> None:
    from app.telephony.twilio_provider import TwilioTelephony

    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(url=str(req.url), auth=req.headers["authorization"], body=req.content.decode())
        return httpx.Response(201, json={"sid": "CA123", "status": "queued"})

    t = TwilioTelephony(
        "ACxx",
        "tok",
        "+15005550006",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    sid = asyncio.run(
        t.place_call("+85291234567", "https://h/v?call_id=1", "https://h/s?call_id=1")
    )
    assert (
        sid == "CA123"
        and seen["url"] == "https://api.twilio.com/2010-04-01/Accounts/ACxx/Calls.json"
    )
    assert seen["auth"] == "Basic " + base64.b64encode(b"ACxx:tok").decode()
    for fragment in (
        "To=%2B85291234567",
        "From=%2B15005550006",
        "Url=https%3A%2F%2Fh%2Fv%3Fcall_id%3D1",
        "StatusCallback=",
    ):
        assert fragment in seen["body"], fragment


def test_twilio_signature_validation_uses_the_sdk_and_rejects_tampering() -> None:
    from twilio.request_validator import RequestValidator

    from app.telephony.twilio_provider import TwilioTelephony

    t = TwilioTelephony("ACxx", "secret-token", "+15005550006")
    url, params = "https://kf.example.test/api/telephony/twilio/gather?call_id=1", {
        "SpeechResult": "hello",
        "CallSid": "CA1",
    }
    good = RequestValidator("secret-token").compute_signature(url, params)
    assert t.valid_signature(url, params, good)
    assert not t.valid_signature(url, {**params, "SpeechResult": "tampered"}, good)
    assert not t.valid_signature(url, params, "") and not t.valid_signature(
        url + "&x=1", params, good
    )


def test_twilio_requires_configuration() -> None:
    from app.providers.base import ProviderConfigError
    from app.telephony.twilio_provider import TwilioTelephony

    with pytest.raises(ProviderConfigError):
        TwilioTelephony("", "", "")


def test_audio_store_expires(monkeypatch) -> None:
    token = audio_store.put(b"abc", "audio/mpeg")
    assert audio_store.get(token) == (b"abc", "audio/mpeg")
    monkeypatch.setattr(audio_store, "TTL_SECONDS", -1)
    assert audio_store.get(token) is None
