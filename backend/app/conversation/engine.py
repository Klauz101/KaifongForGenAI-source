"""Call state machine (SPEC 7.1). Pure logic: no database, network or provider calls.

Stages: greeting -> chat -> checkin -> nudge -> close. The service layer feeds it elder text and
plays the reply. Safety hooks (Phase 4) plug in before `plan_reply`.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.conversation import checklist, persona
from app.security.pseudonymize import Pseudonymizer

NUDGE_KINDS = [
    "go_outside",
    "call_family",
    "meet_friend",
]  # fallback only; calls normally carry a nudge chosen by nudges.choose_nudge
HKT = timedelta(hours=8)


@dataclass
class CallSession:
    call_id: int
    elder_id: int
    language: str
    display_name: str
    display_name_en: str
    ngo: str
    call_time: str
    interests: list[str]
    facts: list[dict]  # [{"yue": ..., "en": ...}]
    pseud: Pseudonymizer
    max_elder_turns: int = 8
    max_seconds: int = 300
    started: datetime = field(default_factory=lambda: datetime.now(UTC))
    stage: str = "greeting"
    history: list[dict[str, str]] = field(default_factory=list)
    covered: set[str] = field(default_factory=set)
    pending_topic: str | None = None
    elder_turns: int = 0
    reprompted: bool = False
    nudge_said: bool = False
    nudge: dict | None = None  # {"kind", "resource_id", "idea": {"yue","en"}}
    ended: bool = False
    safety_escalated: bool = False  # a red flag fired: gentle, short, no more checklist questions
    safety_categories: set[str] = field(default_factory=set)
    safety_followups: int = 0
    optout_pending: bool = False  # asked to stop calls; waiting for one confirmation

    @property
    def name(self) -> str:
        return self.display_name if self.language == "yue" else self.display_name_en


@dataclass
class Plan:
    stage: str
    scripted: dict[str, str] | None = None  # fixed line {"yue","en"}: no LLM
    system: str = ""
    messages: list[dict[str, str]] = field(default_factory=list)
    hint: dict = field(default_factory=dict)
    ends_call: bool = False


def start(s: CallSession) -> dict[str, str]:
    """Greeting text (both languages). Counts the meal question as pending."""
    # Open with something warm (family/event/interest) rather than a health fact when we can.
    warm = [f for f in s.facts if f.get("category") in ("family", "event", "interest", "routine")]
    fact = (warm or s.facts or [None])[0]
    line = persona.greeting(s.display_name, s.display_name_en, s.ngo, datetime.now(UTC) + HKT, fact)
    s.pending_topic = "ate_meals"
    s.history.append({"role": "assistant", "content": line[persona.lang_key(s.language)]})
    return line


def is_goodbye(text: str, language: str) -> bool:
    lowered = text.lower()
    return any(w in lowered for w in persona.lines()["goodbye"][persona.lang_key(language)])


def reprompt(s: CallSession) -> dict[str, str]:
    """Polite 'say that again' (once), then a gentle 'let's take our time'. Does not count as a turn."""
    key = "second" if s.reprompted else "first"
    s.reprompted = True
    return persona.pair(persona.lines()["reprompt"][key])


def register_elder_text(s: CallSession, text: str) -> None:
    s.elder_turns += 1
    s.reprompted = False
    s.covered |= checklist.detect(text, s.language)
    if s.pending_topic:
        s.covered.add(s.pending_topic)  # asked, and they answered
        s.pending_topic = None
    s.history.append({"role": "user", "content": s.pseud.scrub(text)})


def _elapsed(s: CallSession) -> float:
    return (datetime.now(UTC) - s.started).total_seconds()


def plan_reply(s: CallSession, text: str) -> Plan:
    """Decide the next stage and what to say. Call `register_elder_text` first."""
    k = persona.lang_key(s.language)
    if (
        is_goodbye(text, s.language)
        or s.elder_turns >= s.max_elder_turns
        or _elapsed(s) >= s.max_seconds
        or s.nudge_said
    ):
        s.stage = "close"
        return Plan("close", scripted=persona.close(s.call_time), ends_call=True)

    topic = None
    if len(s.covered) >= 5 or s.elder_turns >= 7:
        s.stage, s.nudge_said = "nudge", True
    elif s.elder_turns >= 3:
        s.stage, topic = "checkin", checklist.next_topic(s.covered)
        if topic is None:
            s.stage, s.nudge_said = "nudge", True
    else:
        s.stage = "chat"

    if s.nudge:  # chosen at call start from risk reasons, interests and history (nudges/nudges.py)
        kind, idea = s.nudge["kind"], s.nudge["idea"]
    else:
        kind = NUDGE_KINDS[s.call_id % len(NUDGE_KINDS)]
        idea = persona.lines()["nudge_ideas"][kind]
    topic_spec = checklist.topics()[topic] if topic else None
    s.pending_topic = topic
    system = persona.system_prompt(
        s.language,
        s.name,
        s.ngo,
        s.facts,
        s.stage,
        s.interests,
        topic_desc=topic_spec["desc"][k] if topic_spec else "",
        nudge_idea=idea[k],
    )
    hint = {
        "language": s.language,
        "stage": s.stage,
        "elder_turns": s.elder_turns,
        "topic_q": topic_spec["q"] if topic_spec else None,
        "nudge_idea": idea,
        "nudge_kind": kind,
        "nudge_resource_id": s.nudge.get("resource_id") if s.nudge else None,
        "facts": s.facts,
        "interests": s.interests,
    }
    return Plan(s.stage, system=system, messages=list(s.history), hint=hint)


def record_reply(s: CallSession, text: str, plan: Plan) -> None:
    s.history.append({"role": "assistant", "content": text})
    if plan.ends_call:
        s.ended = True


def plan_safety_followup(s: CallSession, text: str) -> Plan:
    """After a red flag: stay gentle and short, no checklist, then close (all scripted, no LLM)."""
    from app.safety import responses

    if (
        is_goodbye(text, s.language)
        or s.safety_followups >= 2
        or s.elder_turns >= s.max_elder_turns
    ):
        s.stage = "close"
        return Plan(
            "close",
            scripted=responses.safety_close(s.display_name, s.display_name_en),
            ends_call=True,
        )
    s.safety_followups += 1
    s.stage = "safety"
    return Plan("safety", scripted=responses.followup(s.safety_followups))
