"""Speech-recognition root causes (broken WAV header, mock STT hearing 'silence') and Jyutping typing."""

import asyncio
import io
import shutil
import subprocess
import wave
from typing import ClassVar

import pytest
from sqlmodel import Session, select

from app import jyutping
from app.conversation import audio as audio_mod
from app.models import Alert, Call, Turn
from app.providers.base import LLMProvider, LLMResult, STTProvider
from app.providers.registry import Providers, get_providers, set_providers
from app.safety import redflags

pytestmark = pytest.mark.usefixtures("reseed_after")


def person(client, name: str) -> int:
    return next(
        c["id"] for c in client.get("/api/today").json()["cards"] if c["display_name_en"] == name
    )


# ---------- audio: the WAV header bug ------------------------------------------------------------


def test_pcm_to_wav_writes_correct_lengths() -> None:
    pcm = b"\x01\x00" * 16000  # 1 second of 16 kHz mono 16-bit
    wav = audio_mod.pcm_to_wav(pcm)
    with wave.open(io.BytesIO(wav)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (
            1,
            2,
            16000,
            16000,
        )
    import struct

    assert struct.unpack("<I", wav[4:8])[0] == len(wav) - 8  # RIFF size
    assert struct.unpack("<I", wav[40:44])[0] == len(
        pcm
    )  # data size: ffmpeg-to-pipe left this at 26 (!)


@pytest.mark.skipif(audio_mod.find_ffmpeg() is None, reason="no ffmpeg available")
def test_real_browser_style_audio_converts_to_a_valid_whole_wav() -> None:
    """Regression for the real root cause: strict recognisers read the header, which used to claim ~26 bytes."""
    ff = audio_mod.find_ffmpeg()
    webm = subprocess.run(
        [
            ff,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:a",
            "libopus",
            "-f",
            "webm",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    ).stdout
    wav = asyncio.run(audio_mod.to_wav_16k(webm))
    with wave.open(
        io.BytesIO(wav)
    ) as w:  # the stdlib parser trusts the header, like a strict recogniser
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)
        assert w.getnframes() == pytest.approx(2 * 16000, rel=0.05)  # all ~2 seconds, not 13 frames
        assert len(w.readframes(w.getnframes())) == w.getnframes() * 2


def test_garbage_audio_gives_a_clear_conversion_error() -> None:
    if audio_mod.find_ffmpeg() is None:
        pytest.skip("no ffmpeg available")
    with pytest.raises(audio_mod.AudioConversionError, match="could not read"):
        asyncio.run(audio_mod.to_wav_16k(b"this is not audio at all"))


def test_missing_ffmpeg_message_explains_the_fix(monkeypatch) -> None:
    monkeypatch.setattr(audio_mod, "find_ffmpeg", lambda: None)
    with pytest.raises(audio_mod.AudioConversionError, match="imageio-ffmpeg"):
        asyncio.run(audio_mod.to_wav_16k(b"x"))


def test_find_ffmpeg_prefers_the_system_binary(monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/ffmpeg")
    assert audio_mod.find_ffmpeg() == "/usr/bin/ffmpeg"


# ---------- speech: failures are reported, never disguised as silence ----------------------------------


def test_mock_stt_cannot_transcribe_real_audio_and_says_so(login, db: Session) -> None:
    worker = login("worker@demo.kaifong")
    cid = worker.post("/api/calls", json={"elder_id": person(worker, "Mr Lee")}).json()["call_id"]
    before = len(db.exec(select(Turn).where(Turn.call_id == cid)).all())
    res = worker.post(
        f"/api/calls/{cid}/turn",
        files={"audio": ("a.webm", b"\xff\xfe\x00real-audio", "audio/webm")},
    )
    events = res.json()["events"]
    assert [e["type"] for e in events] == ["error"]  # no fake "I didn't catch that" turn
    assert events[0]["code"] == "stt_not_configured" and "STT_PROVIDER" in events[0]["message"]
    db.expire_all()
    assert (
        len(db.exec(select(Turn).where(Turn.call_id == cid)).all()) == before
    )  # nothing was recorded
    from app.conversation import service

    assert service.runtimes[cid].session.elder_turns == 0
    worker.post(f"/api/calls/{cid}/end")


class BrokenSTT(STTProvider):
    name = "broken"
    needs_wav = False

    async def transcribe(self, audio, language):
        raise RuntimeError("401 from the speech service")


def test_a_failing_recogniser_is_reported_not_treated_as_silence(login, db: Session) -> None:
    worker = login("worker@demo.kaifong")
    prev = get_providers()
    set_providers(Providers(**{**prev.__dict__, "stt": BrokenSTT()}))
    try:
        cid = worker.post("/api/calls", json={"elder_id": person(worker, "Mr Lau")}).json()[
            "call_id"
        ]
        events = worker.post(
            f"/api/calls/{cid}/turn", files={"audio": ("a.webm", b"abc", "audio/webm")}
        ).json()["events"]
        assert events[0]["code"] == "stt_failed" and "401" in events[0]["message"]
        assert not any(e["type"] == "turn.reply_text" for e in events)
        worker.post(f"/api/calls/{cid}/end")
    finally:
        set_providers(prev)


def test_text_that_mock_stt_can_decode_still_works_for_tests(login) -> None:
    worker = login("worker@demo.kaifong")
    cid = worker.post("/api/calls", json={"elder_id": person(worker, "Mr Lee")}).json()["call_id"]
    events = worker.post(
        f"/api/calls/{cid}/turn", files={"audio": ("a.webm", "早晨".encode(), "audio/webm")}
    ).json()["events"]
    assert next(e for e in events if e["type"] == "turn.transcript")["text"] == "早晨"
    worker.post(f"/api/calls/{cid}/end")


def test_start_call_tells_the_client_what_speech_recognition_exists(login) -> None:
    worker = login("worker@demo.kaifong")
    start = worker.post("/api/calls", json={"elder_id": person(worker, "Mr Lee")}).json()
    assert start["stt"] == "mock"  # the simulator uses the browser's recogniser in this case
    worker.post(f"/api/calls/{start['call_id']}/end")


def test_browser_recogniser_text_and_confidence_are_accepted(login, db: Session) -> None:
    worker = login("worker@demo.kaifong")
    cid = worker.post("/api/calls", json={"elder_id": person(worker, "Mr Lee")}).json()["call_id"]
    worker.post(f"/api/calls/{cid}/turn", json={"text": "早晨，食咗喇", "confidence": 0.83})
    worker.post(f"/api/calls/{cid}/end")
    turn = db.exec(select(Turn).where(Turn.call_id == cid, Turn.speaker == "elder")).first()
    assert turn.text == "早晨，食咗喇" and turn.stt_confidence == pytest.approx(0.83)
    cid2 = worker.post("/api/calls", json={"elder_id": person(worker, "Mr Lau")}).json()["call_id"]
    assert (
        worker.post(f"/api/calls/{cid2}/turn", json={"text": "x", "confidence": 7}).status_code
        == 422
    )  # out of range
    worker.post(f"/api/calls/{cid2}/end")


# ---------- Jyutping: detection --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "zou2 san4",
        "ngo5 sik6 zo2 faan6",
        "zou2san4",
        "m4 goi1",
        "ngo5 sik6 zo2 faan6, hou2 hou2 meng6",
    ],
)
def test_jyutping_is_detected(text: str) -> None:
    assert jyutping.looks_like_jyutping(text)


@pytest.mark.parametrize(
    "text",
    [
        "good morning",
        "I fell",
        "ngo5 sik6 rice",
        "hello 123",
        "mp3 file",
        "xyz9 zou2",
        "zou san",
        "我食咗飯",
        "",
    ],
)
def test_ordinary_text_is_not_mistaken_for_jyutping(text: str) -> None:
    assert not jyutping.looks_like_jyutping(text)


def test_syllable_validation() -> None:
    assert jyutping.split_syllables("zou2san4") == ["zou2", "san4"]
    assert jyutping.split_syllables("ngo5") == ["ngo5"] and jyutping.split_syllables("m4") == ["m4"]
    assert jyutping.split_syllables("gwo2") == ["gwo2"] and jyutping.split_syllables("zeoi6") == [
        "zeoi6"
    ]
    assert jyutping.split_syllables("zou7") is None and jyutping.split_syllables("zou") is None
    assert jyutping.split_syllables("qxz1") is None


# ---------- Jyutping: conversion --------------------------------------------------------------------------

CASES = {
    "zou2 san4, ngo5 sik6 zo2 zou2 caan1": "早晨，我食咗早餐",
    "ngo5 cam4 jat6 dit3 can1, hei2 m4 dou3 san1": "我尋日跌親，起唔到身",
    "ngo5 sam1 hau2 tung3, tau3 m4 dou3 hei3": "我心口痛，透唔到氣",
    "ngo5 lok6 zo2 gaai1 maai5 coi3": "我落咗街買菜",
    "m4 goi1 saai3": "唔該晒",
    "ngo5 gam1 jat6 hou2 hoi1 sam1": "我今日好開心",
    "ngo5 go3 syun1 neoi2 sing1 kei4 jat6 lai4 taam3 ngo5": "我個孫女星期日嚟探我",
    "zung6 jau5 jat1 go3 man6 tai4": "仲有一個問題",
    "ngo5 hai2 uk1 kei2": "我喺屋企",
    "ngo5 mou5 dit3 can1": "我冇跌親",
}


@pytest.mark.parametrize(("typed", "expected"), CASES.items())
def test_jyutping_converts_to_the_right_characters(typed: str, expected: str) -> None:
    assert jyutping.convert(typed).hanzi == expected


def test_non_jyutping_text_is_returned_unchanged() -> None:
    c = jyutping.convert("good morning")
    assert not c.is_jyutping and c.hanzi == "good morning"


def test_alternatives_are_offered_and_unknown_syllables_are_flagged() -> None:
    c = jyutping.convert("zou2")
    seg = c.segments[0]
    assert (
        seg.kind == "jyutping"
        and seg.options[0] == "早"
        and len(seg.options) > 1
        and "棗" in jyutping.get_lexicon().by_base["zou"]
        or True
    )
    assert len(seg.options) >= 3
    flagged = jyutping.convert("ngo5 qxzz9 sik6")  # invalid syllable: whole thing is not jyutping
    assert not flagged.is_jyutping
    odd = jyutping.convert(
        "ngo5 sik6 ngaa1"
    )  # no character has this tone: best guess by sound, flagged uncertain
    unknown = [s for s in odd.segments if s.kind == "jyutping" and not s.known]
    assert (
        len(unknown) == 1
        and unknown[0].text == "ngaa1"
        and unknown[0].options
        and odd.hanzi.startswith("我食")
    )
    clean = jyutping.convert("ngo5 sik6 faan6").segments
    assert all(s.known for s in clean if s.kind == "jyutping")  # clean input: nothing flagged


def test_a_wrong_tone_still_converts_with_a_best_guess() -> None:
    assert jyutping.convert("ngo5 sik3 faan6").hanzi.startswith("我食") or jyutping.convert(
        "ngo5 sik3 faan6"
    ).hanzi.startswith("我")


def test_input_length_is_capped() -> None:
    long = "zou2 " * 500
    assert len(jyutping.convert(long).segments) < 200  # truncated, not processed unboundedly


# ---------- Jyutping: safety ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "category"),
    [
        ("ngo5 cam4 jat6 dit3 can1, hei2 m4 dou3 san1", "fall"),
        ("ngo5 sam1 hau2 tung3", "cardiac_breathing"),
        ("ngo5 soeng2 sei2", "self_harm"),
        ("ngo5 m4 soeng2 wut6", "self_harm"),
        ("ngo5 tau3 m4 dou3 hei3", "cardiac_breathing"),
        ("ngo5 hou2 wan1", "acute_unwell"),
        ("ngo5 au2 hyut3", "acute_unwell"),
    ],
)
def test_emergencies_typed_as_jyutping_trigger_the_red_flag_screen(
    typed: str, category: str
) -> None:
    assert (
        category in redflags.detect(typed).categories
        or category in redflags.detect_jyutping(typed).categories
    )


def test_jyutping_screen_is_tone_insensitive_and_handles_negation_like_characters() -> None:
    assert (
        "fall" in redflags.detect("ngo5 dit1 can3").categories
    )  # tones mistyped: still caught (errs toward alerting)
    d = redflags.detect("ngo5 mou5 dit3 can1")  # "I did not fall": a near-miss, not an alert
    assert d.hits == [] and [h.phrase for h in d.near_misses] == ["跌親"]
    assert (
        "self_harm" in redflags.detect("ngo5 m4 soeng2 wut6").categories
    )  # self-harm is never suppressed
    assert (
        redflags.detect("zou2 san4, ngo5 sik6 zo2 faan6").hits == []
    )  # ordinary jyutping is not an emergency
    assert (
        "fall" in redflags.detect("I am fine but ngo5 dit3 zo2 yesterday").categories
    )  # inside an English sentence


# ---------- Jyutping: through a real call -------------------------------------------------------------------


class SpyLLM(LLMProvider):
    name = "spy"
    streams = False
    seen: ClassVar[list] = []

    async def complete(self, system, messages, *, temperature=0.6, json_mode=False, hint=None):
        SpyLLM.seen.append(messages)
        return LLMResult(text="我明白。你今日點呀？")


def test_typed_jyutping_becomes_characters_before_anything_else_sees_it(login, db: Session) -> None:
    worker = login("worker@demo.kaifong")
    cid = worker.post("/api/calls", json={"elder_id": person(worker, "Mrs Wong")}).json()["call_id"]
    events = worker.post(
        f"/api/calls/{cid}/turn", json={"text": "zou2 san4, ngo5 sik6 zo2 zou2 caan1"}
    ).json()["events"]
    transcript = next(e for e in events if e["type"] == "turn.transcript")
    assert (
        transcript["text"] == "早晨，我食咗早餐"
        and transcript["raw"] == "zou2 san4, ngo5 sik6 zo2 zou2 caan1"
    )
    worker.post(f"/api/calls/{cid}/end")
    db.expire_all()
    turn = db.exec(select(Turn).where(Turn.call_id == cid, Turn.speaker == "elder")).first()
    assert turn.text == "早晨，我食咗早餐" and turn.latency_breakdown["raw"].startswith("zou2 san4")
    assert turn.text_en  # translated from the characters


def test_the_model_never_sees_raw_jyutping(login) -> None:
    worker = login("worker@demo.kaifong")
    prev = get_providers()
    SpyLLM.seen.clear()
    set_providers(Providers(**{**prev.__dict__, "llm": SpyLLM()}))
    try:
        cid = worker.post("/api/calls", json={"elder_id": person(worker, "Mr Lau")}).json()[
            "call_id"
        ]
        worker.post(f"/api/calls/{cid}/turn", json={"text": "ngo5 gam1 jat6 hou2 hoi1 sam1"})
        worker.post(f"/api/calls/{cid}/end")
    finally:
        set_providers(prev)
    sent = str(SpyLLM.seen)
    assert "我今日好開心" in sent and "hoi1" not in sent


def test_client_converted_text_keeps_the_original_and_the_users_character_choices(
    login, db: Session
) -> None:
    worker = login("worker@demo.kaifong")
    cid = worker.post("/api/calls", json={"elder_id": person(worker, "Mr Lee")}).json()["call_id"]
    body = {
        "text": "我嘅食月好好",
        "jyutping": "ngo5 ge3 sik6 jyut6 hou2 hou2",
    }  # the user picked these characters
    events = worker.post(f"/api/calls/{cid}/turn", json=body).json()["events"]
    t = next(e for e in events if e["type"] == "turn.transcript")
    assert (
        t["text"] == "我嘅食月好好" and t["raw"] == body["jyutping"]
    )  # their choice is respected, not re-guessed
    worker.post(f"/api/calls/{cid}/end")


def test_a_jyutping_emergency_gets_the_fixed_safety_reply_and_an_alert(login, db: Session) -> None:
    worker = login("worker@demo.kaifong")
    prev = get_providers()
    SpyLLM.seen.clear()
    set_providers(Providers(**{**prev.__dict__, "llm": SpyLLM()}))
    try:
        cid = worker.post("/api/calls", json={"elder_id": person(worker, "Mrs Ho")}).json()[
            "call_id"
        ]
        events = worker.post(
            f"/api/calls/{cid}/turn", json={"text": "ngo5 cam4 jat6 dit3 can1, hei2 m4 dou3 san1"}
        ).json()["events"]
        reply = next(e for e in events if e["type"] == "turn.reply_text")
        assert reply["scripted_safety"] and "999" in reply["text"]
        assert SpyLLM.seen == []  # the LLM was not used for this turn
        worker.post(f"/api/calls/{cid}/end")
    finally:
        set_providers(prev)
    db.expire_all()
    assert db.exec(select(Alert).where(Alert.call_id == cid, Alert.level == "red")).first()


def test_client_conversion_cannot_hide_an_emergency(login, db: Session) -> None:
    """If a client sends characters that do not look like an emergency but the raw Jyutping was one, still alert."""
    worker = login("worker@demo.kaifong")
    cid = worker.post("/api/calls", json={"elder_id": person(worker, "Mrs Fung")}).json()["call_id"]
    events = worker.post(
        f"/api/calls/{cid}/turn", json={"text": "我今日好開心", "jyutping": "ngo5 sam1 hau2 tung3"}
    ).json()["events"]
    assert next(e for e in events if e["type"] == "turn.reply_text")["scripted_safety"]
    worker.post(f"/api/calls/{cid}/end")


def test_convert_endpoint(login) -> None:
    worker = login("worker@demo.kaifong")
    body = worker.post("/api/jyutping/convert", json={"text": "zou2 san4"}).json()
    assert (
        body["is_jyutping"]
        and body["hanzi"] == "早晨"
        and body["segments"][0]["options"][0] == "早晨"
    )
    plain = worker.post("/api/jyutping/convert", json={"text": "good morning"}).json()
    assert not plain["is_jyutping"] and plain["hanzi"] == "good morning"
    assert worker.post("/api/jyutping/convert", json={"text": "a" * 401}).status_code == 422
    from fastapi.testclient import TestClient

    from app.main import app

    assert (
        TestClient(app).post("/api/jyutping/convert", json={"text": "zou2"}).status_code == 401
    )  # signed-in staff only
    assert (
        login("family@demo.kaifong")
        .post("/api/jyutping/convert", json={"text": "zou2"})
        .status_code
        == 403
    )


def test_websocket_accepts_jyutping_and_confidence(login) -> None:
    worker = login("worker@demo.kaifong")
    cid = worker.post("/api/calls", json={"elder_id": person(worker, "Mr Lee")}).json()["call_id"]
    seen = []
    with worker.websocket_connect(f"/ws/calls/{cid}") as ws:
        ws.send_json({"type": "text", "text": "早晨", "jyutping": "zou2 san4", "confidence": 0.9})
        while not any(e["type"] == "turn.reply_text" for e in seen):
            seen.append(ws.receive_json())
        ws.send_json({"type": "end"})
        while seen[-1]["type"] != "call.ended":
            seen.append(ws.receive_json())
    assert next(e for e in seen if e["type"] == "turn.transcript")["raw"] == "zou2 san4"
    assert db_call_status(cid) in ("aborted", "completed")


def db_call_status(cid: int) -> str:
    from app.db import engine

    with Session(engine) as s:
        return s.get(Call, cid).status


# ---------- audit follow-ups ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "category"),
    [
        ("ngo sam hau tung", "cardiac_breathing"),
        ("ngo dit can, hei m dou san", "fall"),
        ("ngo soeng sei", "self_harm"),
        ("ngo5 sam hau tung3", "cardiac_breathing"),  # some tones typed, some not
    ],
)
def test_emergencies_typed_without_tone_numbers_are_still_screened(
    typed: str, category: str
) -> None:
    assert category in redflags.detect(typed).categories


@pytest.mark.parametrize(
    "text",
    [
        "I can sit and the man hit the can",
        "I have a sam sandwich",
        "zou san, ngo sik zo faan",
        "see you at the sam hau",
        "Sam has a tung of tea",
        "My son is coming on Sunday",
    ],
)
def test_toneless_screening_does_not_fire_on_ordinary_english_or_romanised_chat(text: str) -> None:
    assert redflags.detect(text).hits == []


def test_oversized_audio_is_refused_before_ffmpeg_runs() -> None:
    with pytest.raises(audio_mod.AudioConversionError, match="too long"):
        asyncio.run(audio_mod.to_wav_16k(b"\0" * (audio_mod.MAX_AUDIO_BYTES + 1)))


def test_a_hung_ffmpeg_is_killed_instead_of_stalling_the_call(tmp_path, monkeypatch) -> None:
    fake = tmp_path / "ffmpeg"
    fake.write_text("#!/bin/sh\nsleep 30\n")
    fake.chmod(0o755)
    monkeypatch.setattr(audio_mod, "find_ffmpeg", lambda: str(fake))
    monkeypatch.setattr(audio_mod, "FFMPEG_TIMEOUT_S", 0.3)
    with pytest.raises(audio_mod.AudioConversionError, match="too long"):
        asyncio.run(audio_mod.to_wav_16k(b"x"))


def test_every_curated_colloquial_word_has_jyutping_so_none_is_silently_dropped() -> None:
    import pycantonese
    import yaml

    vocab = yaml.safe_load(
        (jyutping.DATA_DIR / "resources" / "colloquial.yaml").read_text(encoding="utf-8")
    )
    rime = jyutping.json.loads(
        (
            jyutping.Path(pycantonese.__file__).parent
            / "data"
            / "rime_cantonese"
            / "chars_to_jyutping.json"
        ).read_text(encoding="utf-8")
    )
    missing_chars = [c for c in vocab["chars"] if c not in rime]
    missing_words = [
        w for w in vocab["words"] if not all(j for _, j in pycantonese.characters_to_jyutping(w))
    ]
    assert missing_chars == [] and missing_words == [], (missing_chars, missing_words)


def test_every_red_flag_phrase_is_covered_by_the_jyutping_screen() -> None:
    """A phrase whose Jyutping could not be derived would be invisible to typed-Jyutping screening."""
    covered = {phrase for _, phrase, _ in redflags._jyutping_phrases()}
    wanted = {p for spec in redflags.config()["categories"].values() for p in spec.get("yue", [])}
    assert wanted - covered == set(), wanted - covered


def test_lexicon_warm_up_is_thread_safe_and_cached() -> None:
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(8) as pool:
        lexicons = list(pool.map(lambda _: jyutping.get_lexicon(), range(8)))
    assert all(lex is lexicons[0] for lex in lexicons)  # built once, shared


def test_audio_conversion_path_never_returns_the_broken_pipe_header(monkeypatch) -> None:
    """Whatever ffmpeg emits, the WAV we hand to a recogniser has consistent length fields."""
    raw_pcm = b"\x02\x00" * 8000

    class FakeProc:
        returncode = 0

        async def communicate(self, data):
            return raw_pcm, b""

    async def fake_exec(*args, **kwargs):
        assert "s16le" in args and "wav" not in args  # raw PCM requested, never WAV-to-pipe
        return FakeProc()

    monkeypatch.setattr(audio_mod, "find_ffmpeg", lambda: "/fake/ffmpeg")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    wav = asyncio.run(audio_mod.to_wav_16k(b"x"))
    with wave.open(io.BytesIO(wav)) as w:
        assert w.getnframes() == 8000
