"""Real phone calls (SPEC 11). Outbound call -> carrier fetches our TwiML -> each speech turn posts back.

The older person talks to the same engine as the browser simulator. Every safety rule still applies
(the red-flag screen runs on whatever the carrier transcribes, before any LLM). Nothing can dial a
number that is not on TELEPHONY_ALLOWED_NUMBERS.
"""

import logging
import math
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlmodel import Session, select
from twilio.twiml.voice_response import Gather, VoiceResponse

from app.config import get_settings
from app.conversation import service
from app.db import engine as db_engine
from app.db import get_session
from app.jobs.scheduler import finalize_no_answer
from app.models import Call, Elder, Turn, User, utcnow
from app.providers.base import ProviderConfigError
from app.security.audit import audit
from app.security.auth import check_elder_access, require_staff
from app.telephony import audio_store
from app.telephony.base import allowed_numbers, normalise_number
from app.telephony.registry import get_telephony
from app.usage import add_usage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/telephony", tags=["telephony"])
MAX_EMPTY_GATHERS = 3
MISSED = {"no-answer", "busy", "failed", "canceled"}


class DialBody(BaseModel):
    elder_id: int


def _base() -> str:
    base = get_settings().public_base_url.rstrip("/")
    if not base:
        raise HTTPException(503, "PUBLIC_BASE_URL is not set: the carrier cannot reach this server")
    return base


def _speech_lang(language: str) -> str:
    s = get_settings()
    return s.twilio_speech_language_yue if language == "yue" else s.twilio_speech_language_en


async def _verified_form(request: Request) -> dict[str, str]:
    """Parse a carrier webhook and reject it unless its signature is valid (never trust the caller id)."""
    form = {k: str(v) for k, v in (await request.form()).items()}
    url = _base() + request.url.path + (f"?{request.url.query}" if request.url.query else "")
    if not get_telephony().valid_signature(
        url, form, request.headers.get("x-twilio-signature", "")
    ):
        raise HTTPException(403, "Invalid webhook signature")
    return form


def _xml(response: VoiceResponse) -> Response:
    return Response(content=str(response), media_type="application/xml")


async def _speak_into(target, text: str, language: str, call_id: int) -> None:
    """Add one spoken line to a TwiML element: our TTS audio if we have it, else the carrier's voice."""
    result = await service.speak(text, language, call_id=call_id)
    if result.audio:
        target.play(f"{_base()}/api/telephony/audio/{audio_store.put(result.audio, result.mime)}")
    else:  # mock TTS: let the carrier speak it (voice/language support for Cantonese is unverified)
        target.say(text, language="zh-HK" if language == "yue" else "en-US")


def _gather(call_id: int, language: str, empty: int = 0) -> Gather:
    q = urlencode({"call_id": call_id, "empty": empty})
    return Gather(
        input="speech",
        language=_speech_lang(language),
        action=f"{_base()}/api/telephony/twilio/gather?{q}",
        method="POST",
        speech_timeout="auto",
        timeout=6,
    )


@router.post("/call")
async def place_call(
    body: DialBody, user: User = Depends(require_staff), db: Session = Depends(get_session)
) -> dict:
    """Start a real outbound call to one person. Consent gate, caseload and the dial allowlist all apply."""
    elder = db.get(Elder, body.elder_id)
    if elder is None:
        raise HTTPException(404, "Not found")
    check_elder_access(user, elder)
    number = normalise_number(elder.phone)
    if number not in allowed_numbers(get_settings().telephony_allowed_numbers):
        raise HTTPException(
            403, "This number is not on TELEPHONY_ALLOWED_NUMBERS: Kaifong will not dial it"
        )
    try:
        provider = get_telephony()
    except ProviderConfigError as exc:
        raise HTTPException(503, str(exc)) from exc
    base = _base()
    started = await service.start_call(
        user, body.elder_id, None, mode="phone"
    )  # consent gate lives here
    call_id = started["call_id"]
    try:
        sid = await provider.place_call(
            number,
            f"{base}/api/telephony/twilio/voice?call_id={call_id}",
            f"{base}/api/telephony/twilio/status?call_id={call_id}",
        )
    except Exception as exc:
        logger.exception("could not place call")
        await service.end_call(call_id, "aborted")
        raise HTTPException(502, "The carrier refused or failed to place the call") from exc
    with Session(db_engine) as s2:
        call = s2.get(Call, call_id)
        call.providers_used = {
            **call.providers_used,
            "telephony": provider.name,
            "telephony_sid": sid,
        }
        s2.add(call)
        s2.commit()
    audit(db, user.id, "place_phone_call", "call", call_id)
    return {"call_id": call_id, "carrier_sid": sid}


@router.post("/twilio/voice")
async def voice(request: Request, call_id: int) -> Response:
    """The person picked up: speak the greeting, then listen."""
    await _verified_form(request)
    rt = service.runtimes.get(call_id)
    if rt is None:
        return _xml(VoiceResponse().hangup())
    with Session(db_engine) as db:
        greeting = db.exec(select(Turn).where(Turn.call_id == call_id, Turn.index == 0)).first()
        text = greeting.text if greeting else ""
    lang = rt.session.language
    response = VoiceResponse()
    gather = _gather(call_id, lang)
    await _speak_into(gather, text, lang, call_id)
    response.append(gather)
    response.redirect(
        f"{_base()}/api/telephony/twilio/gather?call_id={call_id}&empty=1", method="POST"
    )
    return _xml(response)


@router.post("/twilio/gather")
async def gather(request: Request, call_id: int, empty: int = 0) -> Response:
    """One speech turn. The carrier's transcript goes through the same engine (red-flag screen first)."""
    form = await _verified_form(request)
    rt = service.runtimes.get(call_id)
    if rt is None:
        return _xml(VoiceResponse().hangup())
    speech = (form.get("SpeechResult") or "").strip()
    if (
        not speech and empty >= MAX_EMPTY_GATHERS
    ):  # nobody is speaking: do not hold the line forever
        await service.end_call(call_id, "aborted")
        return _xml(VoiceResponse().hangup())
    try:
        conf = float(form["Confidence"]) if form.get("Confidence") else None
    except ValueError:
        conf = None
    await service.handle_turn(call_id, text=speech, confidence=conf)
    events = rt.drain()
    lang = rt.session.language
    ended = any(e["type"] == "call.ended" for e in events)
    chunks = {e["seq"]: e["text"] for e in events if e["type"] == "turn.reply_chunk"}
    response = VoiceResponse()
    gather_el = response if ended else _gather(call_id, lang)
    for seq in sorted(chunks):  # sentences were already synthesised (cached) by the call loop
        await _speak_into(gather_el, chunks[seq], lang, call_id)
    if ended:
        response.hangup()
    else:
        response.append(gather_el)
        next_empty = empty + 1 if not speech else 1
        response.redirect(
            f"{_base()}/api/telephony/twilio/gather?call_id={call_id}&empty={next_empty}",
            method="POST",
        )
    return _xml(response)


@router.post("/twilio/status")
async def status(request: Request, call_id: int) -> dict:
    """Carrier status callback: missed calls count toward the unreachable rule; minutes are metered."""
    form = await _verified_form(request)
    call_status = form.get("CallStatus", "")
    with Session(db_engine) as db:
        call = db.get(Call, call_id)
        if call is None:
            raise HTTPException(404, "Not found")
        elder_id = call.elder_id
        in_progress = call.status == "in_progress"
    if call_status in MISSED and in_progress:
        service.runtimes.pop(call_id, None)
        with Session(db_engine) as db:
            call = db.get(Call, call_id)
            call.status, call.ended_at = "no_answer", utcnow()
            db.add(call)
            db.commit()
            await finalize_no_answer(db, elder_id)
        return {"ok": True, "recorded": "no_answer"}
    if call_status == "completed":
        duration = int(form.get("CallDuration") or 0)
        add_usage(
            call_id, telephony_minutes=math.ceil(duration / 60)
        )  # carriers bill started minutes
        if call_id in service.runtimes:  # the person hung up before Kaifong said goodbye
            await service.end_call(call_id, "aborted")
    return {"ok": True}


@router.get("/audio/{token}")
def audio(token: str) -> Response:
    """Our TTS audio for the carrier to fetch. The token is unguessable and expires after ten minutes."""
    item = audio_store.get(token)
    if item is None:
        raise HTTPException(404, "Not found")
    return Response(content=item[0], media_type=item[1])
