"""Call orchestration: STT -> engine -> LLM -> output -> TTS -> persistence -> events (SPEC 6.1).

Each live call has a `CallRuntime` holding the engine session and an outbox queue; the WebSocket
handler (or the REST fallback) drains the outbox. Safety (Phase 4) hooks in before the LLM step.
"""

import asyncio
import base64
import logging
import statistics
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlmodel import Session, select

from app import jyutping, usage
from app.analysis import memory
from app.analysis.pipeline import run_post_call
from app.config import get_settings
from app.conversation import engine, output_guard, persona
from app.conversation.audio import AudioConversionError, to_wav_16k
from app.conversation.sentences import SentenceBuffer, split_sentences
from app.db import engine as db_engine
from app.models import Call, Consent, Elder, Nudge, Turn, User, utcnow
from app.nudges.nudges import choose_nudge
from app.providers.base import STTNotConfiguredError, TTSResult
from app.providers.registry import Providers, get_providers
from app.realtime.hub import hub
from app.safety import alerts as safety_alerts
from app.safety import redflags, responses
from app.security.audit import audit
from app.security.auth import check_elder_access
from app.security.pseudonymize import Pseudonymizer
from app.usage import add_usage, metered

logger = logging.getLogger(__name__)

_tts_cache: OrderedDict[tuple[str, str, str], TTSResult] = OrderedDict()


@dataclass
class CallRuntime:
    session: engine.CallSession
    providers: Providers
    next_index: int = 0
    outbox: asyncio.Queue = field(default_factory=asyncio.Queue)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    tasks: set = field(default_factory=set)

    def emit(self, event: dict) -> None:
        self.outbox.put_nowait(event)

    def drain(self) -> list[dict]:
        events = []
        while not self.outbox.empty():
            events.append(self.outbox.get_nowait())
        return events


runtimes: dict[int, CallRuntime] = {}
background: set[asyncio.Task] = set()  # post-call analyses in flight


async def wait_background() -> None:
    """Wait for post-call analyses (tests, graceful shutdown)."""
    while background:
        await asyncio.gather(*list(background), return_exceptions=True)


def ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def has_active_consent(db: Session, elder_id: int) -> bool:
    consent = db.exec(
        select(Consent).where(Consent.elder_id == elder_id).order_by(Consent.consented_at.desc())
    ).first()
    return bool(consent and consent.scope_calls and consent.withdrawn_at is None)


async def speak(
    text: str, language: str, providers: Providers | None = None, call_id: int | None = None
) -> TTSResult:
    """TTS with an in-memory cache (fixed lines repeat). Failures degrade to browser speech."""
    providers = providers or get_providers()
    key = (providers.tts.name, language, text)
    if key in _tts_cache:
        _tts_cache.move_to_end(key)
        return _tts_cache[key]
    try:
        result = await providers.tts.synthesize(text, language)
    except Exception:
        logger.exception("TTS failed; client will use browser speech")
        return TTSResult(audio=None)
    if result.audio:  # characters actually sent to a paid TTS service (cache hits cost nothing)
        add_usage(call_id, tts_chars=len(text))
    _tts_cache[key] = result
    while len(_tts_cache) > get_settings().tts_cache_size:
        _tts_cache.popitem(last=False)
    return result


def audio_fields(result: TTSResult) -> dict:
    return {
        "audio_b64": base64.b64encode(result.audio).decode() if result.audio else None,
        "mime": result.mime,
    }


async def filler(language: str) -> dict:
    text = persona.lines()["filler"][persona.lang_key(language)]
    return {"text": text, **audio_fields(await speak(text, language))}


def _facts_for(db: Session, elder_id: int) -> list[dict]:
    return memory.retrieve_facts(db, elder_id)


async def start_call(
    user: User, elder_id: int, language: str | None, mode: str = "simulator"
) -> dict:
    settings, providers = get_settings(), get_providers()
    with Session(db_engine) as db:
        elder = db.get(Elder, elder_id)
        if elder is None:
            raise HTTPException(404, "Not found")
        check_elder_access(user, elder)
        if elder.status != "active" or not has_active_consent(db, elder_id):
            raise HTTPException(
                403, "No active consent for calls: the person must consent before any call."
            )
        for stale_id, r in list(runtimes.items()):
            if r.session.elder_id != elder_id:
                continue
            age = (datetime.now(UTC) - r.session.started).total_seconds()
            if age < settings.max_call_seconds + 60:
                raise HTTPException(409, "A call with this person is already in progress")
            await end_call(stale_id, "aborted")  # abandoned (browser closed): free the line
        lang = language if language in ("yue", "en") else elder.language
        call = Call(
            elder_id=elder_id,
            started_at=utcnow(),
            mode=mode,
            language=lang,
            providers_used={
                "stt": providers.stt.name,
                "tts": providers.tts.name,
                "llm": providers.llm.name,
                "translate": providers.translator.name,
            },
        )
        db.add(call)
        db.commit()
        db.refresh(call)
        call_id = call.id
        session = engine.CallSession(
            call_id=call_id,
            elder_id=elder_id,
            language=lang,
            display_name=elder.display_name,
            display_name_en=elder.display_name_en,
            ngo=settings.ngo_name,
            call_time=elder.preferred_call_time,
            interests=list(elder.interests),
            nudge=choose_nudge(db, elder),
            facts=_facts_for(db, elder_id),
            pseud=Pseudonymizer(
                elder.full_name, elder.phone, (elder.display_name, elder.display_name_en)
            ),
            max_elder_turns=settings.max_elder_turns,
            max_seconds=settings.max_call_seconds,
        )
        line = engine.start(session)
        key = persona.lang_key(lang)
        db.add(
            Turn(call_id=call_id, index=0, speaker="kaifong", text=line[key], text_en=line["en"])
        )
        db.commit()
        audit(db, user.id, "start_call", "call", call_id)
        elder_names = {
            "id": elder.id,
            "display_name": elder.display_name,
            "display_name_en": elder.display_name_en,
        }
    rt = CallRuntime(session=session, providers=providers, next_index=1)
    runtimes[call_id] = rt
    tts = await speak(line[key], lang, providers, call_id)
    await hub.broadcast({"type": "call.started", "call_id": call_id, "elder": elder_names})
    return {
        "call_id": call_id,
        "stt": providers.stt.name,  # "mock" = the browser should do the listening
        "language": lang,
        "elder": elder_names,
        "greeting": {
            "type": "call.greeting",
            "text": line[key],
            "text_en": line["en"],
            **audio_fields(tts),
        },
    }


def _save_turn(
    call_id: int,
    index: int,
    speaker: str,
    text: str,
    text_en: str | None = None,
    conf: float | None = None,
    latency: dict | None = None,
    scripted_safety: bool = False,
) -> int:
    with Session(db_engine) as db:
        turn = Turn(
            call_id=call_id,
            index=index,
            speaker=speaker,
            text=text,
            text_en=text_en,
            stt_confidence=conf,
            is_scripted_safety=scripted_safety,
            latency_breakdown=latency or {},
        )
        db.add(turn)
        db.commit()
        db.refresh(turn)
        return turn.id


def _translate_later(rt: CallRuntime, turn_id: int, index: int, text: str) -> None:
    async def run() -> None:
        try:
            with metered(rt.session.call_id, "translate"):
                en = await rt.providers.translator.to_english(text, rt.session.language)
        except Exception:
            logger.exception("translation failed")
            return
        with Session(db_engine) as db:
            turn = db.get(Turn, turn_id)
            if turn:
                turn.text_en = en
                db.add(turn)
                db.commit()
        rt.emit({"type": "turn.translation", "index": index, "text_en": en})

    task = asyncio.create_task(run())
    rt.tasks.add(task)
    task.add_done_callback(rt.tasks.discard)


async def _say(rt: CallRuntime, sentence: str, seq: int, t0: float, marks: dict) -> None:
    """Emit one sentence as text, then its audio (first audio time recorded for latency)."""
    lang = rt.session.language
    rt.emit({"type": "turn.reply_chunk", "seq": seq, "text": sentence})
    t_tts = time.perf_counter()
    result = await speak(sentence, lang, rt.providers, rt.session.call_id)
    if "tts_ms" not in marks:
        marks["tts_ms"] = ms(t_tts)
        marks["ttfa_ms"] = ms(t0)
    rt.emit({"type": "turn.reply_audio", "seq": seq, **audio_fields(result)})


async def _transcribe(rt: CallRuntime, audio: bytes) -> tuple[str, float | None, int]:
    t = time.perf_counter()
    stt = rt.providers.stt
    if stt.needs_wav:
        audio = await to_wav_16k(audio)
    if stt.needs_wav:  # 16 kHz mono 16-bit WAV: bytes / 32000 = seconds of audio sent to STT
        add_usage(rt.session.call_id, stt_seconds=max(0, len(audio) - 44) / 32000)
    result = await stt.transcribe(audio, rt.session.language)
    return result.text, result.confidence, ms(t)


async def handle_turn(
    call_id: int,
    *,
    text: str | None = None,
    audio: bytes | None = None,
    confidence: float | None = None,
    raw_input: str | None = None,
) -> None:
    """Process one elder utterance (typed or spoken). Results arrive as events on the outbox.

    `raw_input` is the Jyutping the person typed when the client already converted it to `text`.
    """
    rt = runtimes.get(call_id)
    if rt is None:
        raise HTTPException(404, "Call is not in progress")
    async with rt.lock:
        if rt.session.ended:
            raise HTTPException(409, "Call has ended")
        await _handle_turn_locked(rt, call_id, text, audio, confidence, raw_input)


async def _handle_turn_locked(
    rt: CallRuntime,
    call_id: int,
    text: str | None,
    audio: bytes | None,
    confidence: float | None = None,
    raw_input: str | None = None,
) -> None:
    s, settings = rt.session, get_settings()
    t0 = time.perf_counter()
    conf, stt_ms = confidence, 0
    if audio is not None:
        try:
            text, conf, stt_ms = await _transcribe(rt, audio)
        except (
            STTNotConfiguredError
        ) as exc:  # nothing was heard: tell the user why, do not fake a turn
            rt.emit({"type": "error", "code": "stt_not_configured", "message": str(exc)})
            return
        except (AudioConversionError, Exception) as exc:
            # A broken recogniser must never look like "the person said nothing": report it, keep the turn open.
            logger.exception("STT failed")
            rt.emit(
                {
                    "type": "error",
                    "code": "stt_failed",
                    "message": f"Speech recognition failed: {exc}",
                }
            )
            return
    text = (text or "").strip()
    raw_jyutping = raw_input
    if text and raw_jyutping is None and jyutping.looks_like_jyutping(text):
        # Typed Jyutping: act on the characters (best guess); the original is kept with the turn
        raw_jyutping, text = text, jyutping.convert(text).hanzi

    idx = rt.next_index
    rt.next_index += 2  # elder turn + kaifong turn
    elder_turn_id = _save_turn(
        call_id,
        idx,
        "elder",
        text,
        conf=conf,
        latency={"input": "jyutping", "raw": raw_jyutping} if raw_jyutping else None,
    )
    rt.emit(
        {
            "type": "turn.transcript",
            "index": idx,
            "text": text,
            "confidence": conf,
            "stt_ms": stt_ms,
            "raw": raw_jyutping,
        }
    )
    if text:
        _translate_later(rt, elder_turn_id, idx, text)

    # --- Safety first (SPEC 8.1): the deterministic red-flag check runs on ANY non-empty text, even
    # low-confidence speech, and always BEFORE the LLM. The LLM can neither skip nor undo it. ---
    detection = await _screen(rt, text, raw_jyutping)
    if detection.near_misses:
        _audit_near_misses(call_id, detection)
    new_categories = [c for c in detection.categories if c not in s.safety_categories]
    if new_categories:
        engine.register_elder_text(s, text)
        await _escalate(rt, call_id, idx, detection, new_categories, t0, stt_ms)
        return

    unclear = not text or (conf is not None and conf < settings.stt_min_confidence)
    if unclear and not s.reprompted:
        await _reply_scripted(rt, call_id, idx + 1, engine.reprompt(s), t0, stt_ms, ends=False)
        return
    if not text:  # still nothing after one re-prompt: stay gentle, do not count a turn
        await _reply_scripted(rt, call_id, idx + 1, engine.reprompt(s), t0, stt_ms, ends=False)
        return

    if not s.safety_escalated and (s.optout_pending or redflags.detect_opt_out(text)):
        await _handle_opt_out(rt, call_id, idx, text, t0, stt_ms)
        return

    engine.register_elder_text(s, text)
    plan = (
        engine.plan_safety_followup(s, text) if s.safety_escalated else engine.plan_reply(s, text)
    )
    if plan.scripted:
        await _reply_scripted(
            rt,
            call_id,
            idx + 1,
            plan.scripted,
            t0,
            stt_ms,
            ends=plan.ends_call,
            plan=plan,
            safety=plan.stage in ("safety", "close") and s.safety_escalated,
        )
        return
    await _reply_llm(rt, call_id, idx + 1, plan, t0, stt_ms)


async def _screen(
    rt: CallRuntime, text: str, raw_jyutping: str | None = None
) -> redflags.Detection:
    """Deterministic detector first (on the characters AND on any Jyutping typed); the optional LLM classifier may only ADD a flag."""
    detection = redflags.detect(text) if text else redflags.Detection()
    if raw_jyutping:
        redflags.merge(detection, redflags.detect_jyutping(raw_jyutping))
    if text and not detection.hits and get_settings().safety_llm_classifier:
        extra = await redflags.classify_with_llm(rt.providers.llm, rt.session.pseud.scrub(text))
        if extra is not None:
            detection.hits.append(extra)
    return detection


def _audit_near_misses(call_id: int, detection: redflags.Detection) -> None:
    """Negated mentions ("我冇跌親") do not escalate but are logged for human review."""
    with Session(db_engine) as db:
        for hit in detection.near_misses:
            audit(db, None, f"redflag_near_miss:{hit.category}", "call", call_id)


async def _escalate(
    rt: CallRuntime,
    call_id: int,
    idx: int,
    detection: redflags.Detection,
    new_categories: list[str],
    t0: float,
    stt_ms: int,
) -> None:
    """RED alert (pushed live) + a fixed scripted reply. The LLM is not called for this turn."""
    s = rt.session
    phrase = {h.category: h.phrase for h in reversed(detection.hits)}
    alert_ids: list[int] = []
    with Session(db_engine) as db:
        call = db.get(Call, call_id)
        call.safety_triggered = True
        db.add(call)
        db.commit()
        for category in new_categories:
            label = redflags.label(category)
            alert = await safety_alerts.raise_alert(
                db,
                s.elder_id,
                call_id,
                "red_flag_phrase",
                "red",
                f"Red flag ({label['en']}): said \"{phrase[category]}\" on today's call (turn #{idx})",
                f"紅旗（{label['zh']}）：通話中講到「{phrase[category]}」（第 {idx} 句）",
            )
            alert_ids.append(alert.id)
    s.safety_escalated = True
    s.safety_categories |= set(new_categories)
    s.stage = "safety"
    rt.emit({"type": "call.safety_triggered", "categories": new_categories, "alert_ids": alert_ids})
    line = responses.for_category(new_categories[0], s.display_name, s.display_name_en)
    plan = engine.Plan("safety", scripted=line)
    await _reply_scripted(
        rt, call_id, idx + 1, line, t0, stt_ms, ends=False, plan=plan, safety=True
    )


async def _handle_opt_out(
    rt: CallRuntime, call_id: int, idx: int, text: str, t0: float, stt_ms: int
) -> None:
    """SPEC 8.3: thank warmly, confirm once, then pause calls and ask the social worker to follow up."""
    s = rt.session
    if not s.optout_pending:
        s.optout_pending = True
        line = responses.opt_out("confirm", s.display_name, s.display_name_en)
        await _reply_scripted(rt, call_id, idx + 1, line, t0, stt_ms, ends=False)
        return
    if redflags.declines_opt_out(text):
        s.optout_pending = False
        line = responses.opt_out("resumed", s.display_name, s.display_name_en)
        await _reply_scripted(rt, call_id, idx + 1, line, t0, stt_ms, ends=False)
        return
    with Session(db_engine) as db:
        elder = db.get(Elder, s.elder_id)
        elder.status = "paused"  # calls pause immediately; a human confirms in person
        db.add(elder)
        db.commit()
        await safety_alerts.raise_alert(
            db,
            s.elder_id,
            call_id,
            "opt_out_request",
            "amber",
            "Asked to stop the daily calls. Calls are paused: please visit to confirm in person.",
            "要求停止每日通話。通話已暫停，請親身探訪確認。",
        )
    s.stage = "optout_done"
    await _reply_scripted(
        rt,
        call_id,
        idx + 1,
        responses.opt_out("done", s.display_name, s.display_name_en),
        t0,
        stt_ms,
        ends=True,
    )


async def _reply_scripted(
    rt: CallRuntime,
    call_id: int,
    index: int,
    line: dict[str, str],
    t0: float,
    stt_ms: int,
    *,
    ends: bool,
    plan: engine.Plan | None = None,
    safety: bool = False,
) -> None:
    s = rt.session
    text = line[persona.lang_key(s.language)]
    marks: dict = {}
    for seq, sentence in enumerate(split_sentences(text)):
        await _say(rt, sentence, seq, t0, marks)
    _save_turn(
        call_id,
        index,
        "kaifong",
        text,
        line["en"],
        latency={"stt_ms": stt_ms, "llm_ms": 0, "stage": s.stage, **marks},
        scripted_safety=safety,
    )
    rt.emit(
        {
            "type": "turn.reply_text",
            "index": index,
            "text": text,
            "text_en": line["en"],
            "stage": s.stage,
            "scripted_safety": safety,
        }
    )
    if plan is not None:
        engine.record_reply(s, text, plan)
    if ends:
        await end_call(call_id, "completed")


def _audit_guard(call_id: int, reason: str) -> None:
    with Session(db_engine) as db:
        audit(db, None, f"output_guard_block:{reason}", "call", call_id)


def _fallback_line(plan: engine.Plan) -> dict:
    return plan.hint.get("topic_q") or persona.lines()["fallback"]["generic"]


async def _reply_llm(
    rt: CallRuntime, call_id: int, index: int, plan: engine.Plan, t0: float, stt_ms: int
) -> None:
    s, llm = rt.session, rt.providers.llm
    system = s.pseud.scrub(plan.system)
    marks: dict = {}
    pieces: list[str] = []
    text_en: str | None = None
    seq = 0
    blocked = False
    t_llm = time.perf_counter()

    async def emit_sentence(raw: str) -> bool:
        """Speak one sentence unless the output guard blocks it (SPEC 8.4). False = stop the reply."""
        nonlocal seq, blocked
        sentence = s.pseud.rehydrate(raw, s.name)
        verdict = output_guard.check(sentence, seq)
        if not verdict.ok:
            blocked = True
            _audit_guard(call_id, verdict.reason)
            return False
        if "llm_first_ms" not in marks:
            marks["llm_first_ms"] = ms(t_llm)
        pieces.append(sentence)
        await _say(rt, sentence, seq, t0, marks)
        seq += 1
        return True

    meter = usage.begin()
    try:
        if llm.streams:
            buf = SentenceBuffer()
            async for delta in llm.stream(system, plan.messages, hint=plan.hint):
                for sentence in buf.feed(delta):
                    if not await emit_sentence(sentence):
                        break
                if blocked:
                    break
            if not blocked:
                for sentence in buf.flush():
                    if not await emit_sentence(sentence):
                        break
        else:
            result = await llm.complete(system, plan.messages, hint=plan.hint)
            text_en = result.meta.get("text_en")
            for sentence in split_sentences(result.text):
                if not await emit_sentence(sentence):
                    break
    except Exception:  # a flaky LLM must not drop the call
        logger.exception("LLM failed; using a scripted line")
    usage.end(meter, call_id, "chat")
    if not pieces:  # failed or fully blocked: a safe scripted line instead
        line = _fallback_line(plan)
        pieces = [line[persona.lang_key(s.language)]]
        text_en = line["en"]
        await _say(rt, pieces[0], seq, t0, marks)
    elif blocked:
        text_en = None  # the pre-translated text no longer matches what was actually said
    marks["llm_ms"] = ms(t_llm)
    reply = ("" if s.language == "yue" else " ").join(pieces)
    turn_id = _save_turn(
        call_id,
        index,
        "kaifong",
        reply,
        text_en,
        latency={"stt_ms": stt_ms, "stage": plan.stage, **marks},
    )
    if plan.stage == "nudge":
        _record_nudge(s, plan)
    engine.record_reply(s, reply, plan)
    rt.emit(
        {
            "type": "turn.reply_text",
            "index": index,
            "text": reply,
            "text_en": text_en,
            "stage": plan.stage,
            "latency": {"stt_ms": stt_ms, **marks},
            "guard_blocked": blocked,
            "scripted_safety": False,
        }
    )
    if text_en is None and reply:
        _translate_later(rt, turn_id, index, reply)


def _record_nudge(s: engine.CallSession, plan: engine.Plan) -> None:
    idea = plan.hint["nudge_idea"]
    with Session(db_engine) as db:
        db.add(
            Nudge(
                call_id=s.call_id,
                elder_id=s.elder_id,
                type=plan.hint["nudge_kind"],
                resource_id=plan.hint.get("nudge_resource_id"),
                text_yue=idea["yue"],
                text_en=idea["en"],
                elder_response="unclear",
            )
        )
        db.commit()


async def end_call(call_id: int, reason: str = "completed") -> dict:
    rt = runtimes.get(call_id)
    finished_now = False
    with Session(db_engine) as db:
        call = db.get(Call, call_id)
        if call is None:
            raise HTTPException(404, "Not found")
        if call.status == "in_progress":
            finished_now = True
            turns = db.exec(select(Turn).where(Turn.call_id == call_id)).all()
            ttfa = [
                t.latency_breakdown["ttfa_ms"] for t in turns if t.latency_breakdown.get("ttfa_ms")
            ]
            call.ended_at = utcnow()
            call.status = (
                "safety_escalated"
                if call.safety_triggered
                else "completed" if reason == "completed" else "aborted"
            )
            call.duration_s = int((call.ended_at - call.started_at).total_seconds())
            call.turn_count = len(turns)
            call.avg_latency_ms = int(statistics.fmean(ttfa)) if ttfa else None
            db.add(call)
            db.commit()
        status, duration = call.status, call.duration_s
        elder_id = call.elder_id
    event = {"type": "call.ended", "call_id": call_id, "status": status, "duration_s": duration}
    if rt:
        rt.session.ended = True
        rt.emit(event)
        runtimes.pop(call_id, None)
    await hub.broadcast({**event, "elder_id": elder_id})
    if finished_now:  # extraction, memory, risk recompute: never block the call or hang up
        task = asyncio.create_task(_post_call(call_id))
        background.add(task)
        task.add_done_callback(background.discard)
    return event


async def _post_call(call_id: int) -> None:
    try:
        await run_post_call(call_id)
    except Exception:
        logger.exception("post-call analysis failed for call %s", call_id)


def abort_stale_calls() -> int:
    """On startup, calls left in_progress by a previous process can never continue."""
    with Session(db_engine) as db:
        stale = db.exec(select(Call).where(Call.status == "in_progress")).all()
        for call in stale:
            call.status, call.ended_at = "aborted", utcnow()
            db.add(call)
        db.commit()
        return len(stale)


def latency_stats(values: list[int]) -> dict:
    if not values:
        return {"count": 0, "median_ms": None, "mean_ms": None, "p90_ms": None}
    ordered = sorted(values)
    return {
        "count": len(values),
        "median_ms": int(statistics.median(ordered)),
        "mean_ms": int(statistics.fmean(ordered)),
        "p90_ms": ordered[min(len(ordered) - 1, int(0.9 * len(ordered)))],
    }
