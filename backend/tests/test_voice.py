"""Phase 2: the voice loop in mock mode, consent gate, latency, providers' request shapes."""

import asyncio
import base64
import json
from datetime import UTC
from typing import ClassVar

import httpx
import pytest
from sqlmodel import Session, select
from starlette.websockets import WebSocketDisconnect

from app.conversation import engine
from app.conversation.audio import AudioConversionError, to_wav_16k
from app.conversation.sentences import SentenceBuffer, split_sentences
from app.models import AuditLog, Call, Consent, Elder, Turn
from app.providers.base import LLMProvider, LLMResult, STTProvider, STTResult
from app.providers.registry import Providers, get_providers, set_providers
from app.security.pseudonymize import Pseudonymizer

pytestmark = pytest.mark.usefixtures("reseed_after")


def elder_id(client, en_name: str) -> int:
    return next(
        c["id"] for c in client.get("/api/today").json()["cards"] if c["display_name_en"] == en_name
    )


def say(client, call_id: int, text: str) -> list[dict]:
    res = client.post(f"/api/calls/{call_id}/turn", json={"text": text})
    assert res.status_code == 200, res.text
    return res.json()["events"]


# ---------- engine ----------------------------------------------------------------------------


def make_session(**kw) -> engine.CallSession:
    base = {
        "call_id": 1,
        "elder_id": 1,
        "language": "yue",
        "display_name": "陳婆婆",
        "display_name_en": "Mrs Chan",
        "ngo": "Sample Centre",
        "call_time": "09:30",
        "interests": ["wet market"],
        "facts": [{"yue": "孫女星期日嚟探佢", "en": "her granddaughter visits on Sunday"}],
        "pseud": Pseudonymizer("Chan Demo Mei", "0000 0001", ("Mrs Chan", "陳婆婆")),
    }
    base.update(kw)
    return engine.CallSession(**base)


def test_greeting_discloses_ai_and_uses_a_memory_fact() -> None:
    line = engine.start(make_session())
    assert "AI" in line["yue"] and "AI helper" in line["en"]
    assert "孫女星期日嚟探佢" in line["yue"] and "granddaughter" in line["en"]


def test_five_stages_in_order_and_call_ends_with_scripted_close() -> None:
    s = make_session()
    engine.start(s)
    stages = []
    for text in ["早晨", "好", "瞓得好", "冇痛", "開心", "出咗街", "朋友嚟過", "食咗藥"]:
        engine.register_elder_text(s, text)
        plan = engine.plan_reply(s, text)
        stages.append(plan.stage)
        engine.record_reply(s, "x", plan)
        if s.ended:
            break
    assert stages[:2] == ["chat", "chat"] and "checkin" in stages and "nudge" in stages
    assert stages.index("checkin") < stages.index("nudge") < stages.index("close")
    assert s.ended and stages[-1] == "close"


def test_goodbye_and_turn_limit_end_the_call() -> None:
    s = make_session()
    engine.register_elder_text(s, "好啦拜拜")
    assert engine.plan_reply(s, "好啦拜拜").stage == "close"
    s2 = make_session(max_elder_turns=2)
    for t in ("a", "b"):
        engine.register_elder_text(s2, t)
        plan = engine.plan_reply(s2, t)
    assert plan.stage == "close" and plan.ends_call


def test_one_checklist_topic_per_reply_and_reprompt_is_once_then_gentle() -> None:
    s = make_session()
    for t in ("x", "y", "z"):
        engine.register_elder_text(s, t)
        plan = engine.plan_reply(s, t)
    assert plan.stage == "checkin" and plan.hint["topic_q"] and s.pending_topic
    first, second = engine.reprompt(s), engine.reprompt(s)
    assert first != second


def test_sentence_splitting() -> None:
    assert split_sentences("好呀。今日點呀？Mrs. Chan is here. Costs 3.5 dollars!") == [
        "好呀。",
        "今日點呀？",
        "Mrs. Chan is here.",
        "Costs 3.5 dollars!",
    ]
    buf = SentenceBuffer()
    out = buf.feed("好") + buf.feed("呀。今日") + buf.feed("點呀？") + buf.flush()
    assert out == ["好呀。", "今日點呀？"]


# ---------- pseudonymiser ----------------------------------------------------------------------


def test_pseudonymiser_scrubs_identifiers_and_rehydrates_display_name_only() -> None:
    p = Pseudonymizer("Chan Demo Mei", "9123 4567", ("Mrs Chan", "陳婆婆"))
    out = p.scrub("I am Chan Demo Mei, phone 9123-4567 or 91234567, HKID A123456(7).")
    assert "Chan Demo" not in out and "9123" not in out and "A123456" not in out
    assert "Mrs Chan" in p.scrub("Mrs Chan says hi")  # display name is not scrubbed
    assert p.rehydrate("Hello [ELDER] [PHONE][ID]", "Mrs Chan") == "Hello Mrs Chan "


class SpyLLM(LLMProvider):
    name = "spy"
    streams = False
    seen: ClassVar[list[tuple[str, list]]] = []

    async def complete(self, system, messages, *, temperature=0.6, json_mode=False, hint=None):
        SpyLLM.seen.append((system, messages))
        return LLMResult(text="我明白。你而家點呀？[ELDER]")


def with_providers(**over):
    base = get_providers()
    set_providers(Providers(**{**base.__dict__, **over}))


def test_llm_never_sees_name_phone_or_hkid_and_reply_is_rehydrated(login, db: Session) -> None:
    worker = login("worker@demo.kaifong")
    eid = elder_id(worker, "Mrs Patel")
    elder = db.get(Elder, eid)
    prev = get_providers()
    with_providers(llm=SpyLLM())
    SpyLLM.seen.clear()
    try:
        call_id = worker.post("/api/calls", json={"elder_id": eid}).json()["call_id"]
        events = say(
            worker, call_id, f"My name is {elder.full_name}, call {elder.phone}, ID A123456(7)."
        )
        say(worker, call_id, "bye")
    finally:
        set_providers(prev)
    blob = json.dumps(SpyLLM.seen)
    assert elder.full_name not in blob and elder.phone not in blob and "A123456" not in blob
    reply = next(e for e in events if e["type"] == "turn.reply_text")
    assert "[ELDER]" not in reply["text"] and "Mrs Patel" in reply["text"]


# ---------- calls: consent gate, permissions ---------------------------------------------------


def test_consent_gate_blocks_calls_without_active_consent(login, db: Session) -> None:
    worker = login("worker@demo.kaifong")
    eid = elder_id(worker, "Mr Lee")
    consent = db.exec(select(Consent).where(Consent.elder_id == eid)).first()
    from datetime import datetime

    consent.withdrawn_at = datetime.now(UTC)
    db.add(consent)
    db.commit()
    try:
        res = worker.post("/api/calls", json={"elder_id": eid})
        assert res.status_code == 403 and "consent" in res.json()["detail"].lower()
    finally:
        consent.withdrawn_at = None
        db.add(consent)
        db.commit()
    assert worker.post("/api/calls", json={"elder_id": eid}).status_code == 200
    worker.post(f"/api/calls/{db.exec(select(Call).order_by(Call.id.desc())).first().id}/end")


def test_no_consent_record_at_all_is_blocked(login, db: Session) -> None:
    worker = login("worker@demo.kaifong")
    eid = elder_id(worker, "Mr Lau")
    consent = db.exec(select(Consent).where(Consent.elder_id == eid)).first()
    db.delete(consent)
    db.commit()
    try:
        assert worker.post("/api/calls", json={"elder_id": eid}).status_code == 403
    finally:
        db.add(
            Consent(
                elder_id=eid,
                method="verbal_and_written",
                witnessed_by="Demo Staff",
                consented_at=consent.consented_at,
            )
        )
        db.commit()


def test_only_the_caseworker_or_admin_can_call(login) -> None:
    eid = elder_id(login("worker@demo.kaifong"), "Mrs Ho")
    assert (
        login("family2@demo.kaifong").post("/api/calls", json={"elder_id": eid}).status_code == 403
    )
    assert (
        login("worker2@demo.kaifong").post("/api/calls", json={"elder_id": eid}).status_code == 403
    )
    assert (
        login("worker@demo.kaifong").post("/api/calls", json={"elder_id": 9999}).status_code == 404
    )


# ---------- the full mock call -----------------------------------------------------------------


def test_full_typed_call_over_rest_runs_all_stages_and_logs_latency(login, db: Session) -> None:
    worker = login("worker@demo.kaifong")
    eid = elder_id(worker, "Mrs Chan")
    start = worker.post("/api/calls", json={"elder_id": eid}).json()
    call_id = start["call_id"]
    assert start["greeting"]["text"].startswith("陳婆婆") and "AI" in start["greeting"]["text"]
    assert (
        worker.post("/api/calls", json={"elder_id": eid}).status_code == 409
    )  # one call at a time

    stages, last = [], []
    for text in [
        "早晨，食咗喇",
        "落咗街買餸",
        "瞓得幾好",
        "膝頭哥有啲痛",
        "心情一般",
        "朋友嚟過",
        "食咗藥",
    ]:
        last = say(worker, call_id, text)
        stages.append(next(e for e in last if e["type"] == "turn.reply_text")["stage"])
        if any(e["type"] == "call.ended" for e in last):
            break
    assert {"chat", "checkin", "nudge", "close"} <= set(stages)
    assert any(e["type"] == "call.ended" for e in last)

    db.expire_all()
    call = db.get(Call, call_id)
    assert call.status == "completed" and call.turn_count >= 10 and call.avg_latency_ms is not None
    turns = db.exec(select(Turn).where(Turn.call_id == call_id).order_by(Turn.index)).all()
    assert [t.index for t in turns] == list(range(len(turns)))
    kaifong = [t for t in turns if t.speaker == "kaifong"]
    assert all({"stt_ms", "llm_ms", "ttfa_ms"} <= set(t.latency_breakdown) for t in kaifong[1:])
    assert all(t.text_en for t in kaifong)  # English subtitles exist for every Kaifong line
    # no raw audio retained, and the call is auditable
    assert db.exec(
        select(AuditLog).where(AuditLog.action == "start_call", AuditLog.entity_id == call_id)
    ).first()
    # post-call: a second call is allowed again
    again = worker.post("/api/calls", json={"elder_id": eid})
    assert again.status_code == 200
    worker.post(f"/api/calls/{again.json()['call_id']}/end")


def test_full_call_over_websocket_with_typed_and_audio_turns(login) -> None:
    worker = login("worker@demo.kaifong")
    eid = elder_id(worker, "Mrs Patel")
    start = worker.post("/api/calls", json={"elder_id": eid, "language": "en"}).json()
    assert start["greeting"]["text"].startswith(("Good morning", "Good afternoon", "Good evening"))
    events: list[dict] = []
    with worker.websocket_connect(f"/ws/calls/{start['call_id']}") as ws:
        ws.send_json({"type": "text", "text": "Good morning, I had my breakfast"})
        ws.send_json(
            {
                "type": "audio",
                "data": base64.b64encode(b"I went to the park").decode(),
                "mime": "audio/webm",
            }
        )
        for _ in range(40):
            events.append(ws.receive_json())
            if sum(e["type"] == "turn.reply_text" for e in events) == 2:
                break
        ws.send_json({"type": "end"})
        while events[-1]["type"] != "call.ended":
            events.append(ws.receive_json())
    kinds = [e["type"] for e in events]
    assert (
        kinds.count("turn.transcript") == 2
        and "turn.reply_chunk" in kinds
        and "turn.reply_audio" in kinds
    )
    transcripts = [e["text"] for e in events if e["type"] == "turn.transcript"]
    assert transcripts[1] == "I went to the park"  # mock STT decoded the 'audio'
    assert kinds[-1] == "call.ended"


def test_websocket_rejects_unauthenticated_and_other_workers(login) -> None:
    worker = login("worker@demo.kaifong")
    eid = elder_id(worker, "Mr Lee")
    cid = worker.post("/api/calls", json={"elder_id": eid}).json()["call_id"]
    try:
        for client in (login("worker2@demo.kaifong"), login("family@demo.kaifong")):
            with pytest.raises(WebSocketDisconnect), client.websocket_connect(f"/ws/calls/{cid}"):
                pass
    finally:
        worker.post(f"/api/calls/{cid}/end")


class SilentSTT(STTProvider):
    """A recogniser that works but hears nothing (silence / mumbling)."""

    name = "silent"
    needs_wav = False

    async def transcribe(self, audio, language):
        return STTResult(text="", confidence=0.0)


def test_unclear_speech_is_reprompted_once_then_gentle_and_does_not_count(
    login, db: Session
) -> None:
    worker = login("worker@demo.kaifong")
    eid = elder_id(worker, "Mr Lee")
    prev = get_providers()
    set_providers(Providers(**{**prev.__dict__, "stt": SilentSTT()}))
    try:
        cid = worker.post("/api/calls", json={"elder_id": eid}).json()["call_id"]
        audio = {"audio": ("a.webm", b"\xff\xfe\x00", "audio/webm")}
        first = next(
            e
            for e in worker.post(f"/api/calls/{cid}/turn", files=audio).json()["events"]
            if e["type"] == "turn.reply_text"
        )
        second = next(
            e
            for e in worker.post(f"/api/calls/{cid}/turn", files=audio).json()["events"]
            if e["type"] == "turn.reply_text"
        )
        assert first["text"] != second["text"] and first["stage"] is not None
        from app.conversation import service

        assert service.runtimes[cid].session.elder_turns == 0
        worker.post(f"/api/calls/{cid}/end")
    finally:
        set_providers(prev)


class BrokenLLM(LLMProvider):
    name = "broken"
    streams = False

    async def complete(self, *a, **k):
        raise RuntimeError("network down")


def test_llm_failure_falls_back_to_a_scripted_line(login) -> None:
    worker = login("worker@demo.kaifong")
    eid = elder_id(worker, "Mrs Wong")
    prev = get_providers()
    with_providers(llm=BrokenLLM())
    try:
        cid = worker.post("/api/calls", json={"elder_id": eid}).json()["call_id"]
        events = say(worker, cid, "早晨")
        worker.post(f"/api/calls/{cid}/end")
    finally:
        set_providers(prev)
    reply = next(e for e in events if e["type"] == "turn.reply_text")
    assert reply["text"] and reply["text_en"]


def test_metrics_latency_admin_only_and_reports_live_calls(login) -> None:
    res = login("admin@demo.kaifong").get("/api/metrics/latency")
    assert res.status_code == 200
    body = res.json()
    assert body["providers"]["llm"] == "mock" and body["target_ms"] == 3000
    assert body["stats"]["count"] > 0 and body["stats"]["median_ms"] is not None
    assert login("worker@demo.kaifong").get("/api/metrics/latency").status_code == 403


def test_filler_endpoint(login) -> None:
    body = login("worker@demo.kaifong").get("/api/audio/filler/yue").json()
    assert body["text"] == "嗯…" and body["audio_b64"] is None  # mock TTS: browser speaks it


# ---------- real providers: request shapes (no network, no keys) -------------------------------


def test_ffmpeg_missing_gives_clear_error(monkeypatch) -> None:
    monkeypatch.setattr("app.conversation.audio.find_ffmpeg", lambda: None)
    with pytest.raises(AudioConversionError, match="ffmpeg"):
        asyncio.run(to_wav_16k(b"x"))


def test_azure_tts_builds_documented_request() -> None:
    from app.providers.tts_azure import AzureTTS

    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(url=str(req.url), headers=req.headers, body=req.content.decode())
        return httpx.Response(200, content=b"MP3DATA")

    tts = AzureTTS(
        "k",
        "eastasia",
        "zh-HK-HiuMaanNeural",
        "en-US-JennyNeural",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    out = asyncio.run(tts.synthesize("你好 <&>", "yue"))
    assert (
        out.audio == b"MP3DATA"
        and seen["url"] == "https://eastasia.tts.speech.microsoft.com/cognitiveservices/v1"
    )
    h = seen["headers"]
    assert h["ocp-apim-subscription-key"] == "k" and h["content-type"] == "application/ssml+xml"
    assert h["x-microsoft-outputformat"] and h["user-agent"]
    assert (
        "zh-HK-HiuMaanNeural" in seen["body"]
        and "rate='-10%'" in seen["body"]
        and "&lt;&amp;&gt;" in seen["body"]
    )


def test_azure_stt_parses_detailed_response() -> None:
    from app.providers.stt_azure import AzureSTT

    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(url=req.url, headers=req.headers)
        return httpx.Response(
            200,
            json={
                "RecognitionStatus": "Success",
                "NBest": [{"Confidence": 0.81, "Display": "你好"}],
            },
        )

    stt = AzureSTT(
        "k", "eastasia", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    res = asyncio.run(stt.transcribe(b"RIFF", "yue"))
    assert (res.text, res.confidence) == ("你好", 0.81)
    assert seen["url"].params["language"] == "zh-HK" and seen["url"].params["format"] == "detailed"
    assert seen["headers"]["content-type"].startswith("audio/wav")


def test_azure_stt_no_match_is_empty_low_confidence() -> None:
    from app.providers.stt_azure import AzureSTT

    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, json={"RecognitionStatus": "NoMatch"})
    )
    res = asyncio.run(
        AzureSTT("k", "eastasia", http_client=httpx.AsyncClient(transport=transport)).transcribe(
            b"", "yue"
        )
    )
    assert res.text == "" and res.confidence == 0.0


def test_tencent_requests_use_documented_fields() -> None:
    from app.providers.stt_tencent import TencentSTT
    from app.providers.tts_tencent import TencentTTS

    stt = TencentSTT("id", "key", "ap-hongkong", "16k_yue", "16k_en")
    req = stt._request(b"abc", "yue")
    assert (req.EngSerViceType, req.SourceType, req.DataLen, req.VoiceFormat) == (
        "16k_yue",
        1,
        3,
        "wav",
    )
    assert base64.b64decode(req.Data) == b"abc"
    tts = TencentTTS("id", "key", "ap-hongkong", 101019, None)
    r = tts._request("你好", "yue")
    assert (r.Text, r.VoiceType, r.Codec, r.PrimaryLanguage) == ("你好", 101019, "mp3", 1)
    from app.providers.base import ProviderConfigError

    with pytest.raises(ProviderConfigError, match="TENCENT_TTS_VOICE_TYPE"):
        TencentTTS("id", "key", "ap-hongkong", None, None)


def test_openai_compatible_llm_request_and_stream() -> None:
    from app.providers.llm_openai_compat import OpenAICompatibleLLM

    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.update(url=str(req.url), auth=req.headers["authorization"], body=body)
        if body.get("stream"):
            chunks = [{"choices": [{"delta": {"content": t}}]} for t in ("好", "呀。")]
            sse = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
            return httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"})
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "你好"}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 2},
            },
        )

    llm = OpenAICompatibleLLM(
        "https://llm.example/v1",
        "sk-test",
        "m",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    res = asyncio.run(
        llm.complete("sys", [{"role": "user", "content": "hi"}], temperature=0.0, json_mode=True)
    )
    assert res.text == "你好" and (res.input_tokens, res.output_tokens) == (7, 2)
    assert (
        seen["url"] == "https://llm.example/v1/chat/completions"
        and seen["auth"] == "Bearer sk-test"
    )
    assert seen["body"]["messages"][0] == {"role": "system", "content": "sys"}
    assert (
        seen["body"]["response_format"] == {"type": "json_object"}
        and seen["body"]["temperature"] == 0.0
    )

    async def collect() -> list[str]:
        return [d async for d in llm.stream("sys", [{"role": "user", "content": "hi"}])]

    assert asyncio.run(collect()) == ["好", "呀。"]


def test_llm_config_errors_are_explicit() -> None:
    from app.providers.base import ProviderConfigError
    from app.providers.llm_openai_compat import OpenAICompatibleLLM

    with pytest.raises(ProviderConfigError):
        OpenAICompatibleLLM("", "", "")
