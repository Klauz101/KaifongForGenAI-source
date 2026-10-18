"""Offline rule-based extractor so mock mode can fill a real summary (no LLM, no network).

It mirrors the real extraction contract (SPEC 7.4): values need evidence turns, `unknown` otherwise.
Only the *older person's* turns are scanned. Keyword lists are English + Cantonese.
TODO: native Cantonese speaker should review the Cantonese patterns.
"""

import re

from app.analysis.summaries import LOC_YUE, compose

R = re.compile  # shorthand

APPETITE_SKIP = R(
    r"(冇|未|唔)食(?!藥)|冇胃口|\b(skipped|skip(ped)? (my )?(lunch|breakfast|dinner|meal)|didn'?t eat|haven'?t eaten|not eaten|no appetite)\b",
    re.IGNORECASE,
)
APPETITE_LESS = R(r"食少|食得少|\b(eating less|less appetite)\b", re.IGNORECASE)
APPETITE_OK = R(
    # "食咗一半" is about pills; "食咗飯未？" is a QUESTION the person is asking, not an answer.
    r"食咗(?!藥|一半|.{0,3}未)|食飽|食過|\b(ate|eaten|had (my )?(breakfast|lunch|dinner))\b",
    re.IGNORECASE,
)
SLEEP_POOR = R(
    r"瞓唔著|瞓唔好|瞓得唔好|瞓唔到|失眠|醒咗好多次|\b(can'?t sleep|couldn'?t sleep|poor sleep|slept (badly|poorly)|didn'?t sleep well|insomnia)\b",
    re.IGNORECASE,
)
SLEEP_GOOD = R(r"瞓得(好|幾好|夠)|\b(slept (well|fine|okay)|good sleep)\b", re.IGNORECASE)
MEDS_PARTIAL = R(
    r"食咗一半|只食咗|唔記得食|\b(took half|only took|forgot the other)\b", re.IGNORECASE
)
MEDS_NO = R(
    r"(冇|未)食藥|\b(haven'?t taken|not taken|didn'?t take|forgot (my )?(medic\w*|pills))\b",
    re.IGNORECASE,
)
MEDS_YES = R(
    r"食咗藥|食晒藥|\b(took (it|my (medic\w*|pills))|taken my (medic\w*|pills))\b", re.IGNORECASE
)
PAIN_NONE = R(r"(冇|唔)(邊度)?痛|\b(no pain|not in pain|nothing hurts)\b", re.IGNORECASE)
PAIN_SEVERE = R(
    r"好痛|痛得好|痛到|犀利|\b(severe|terrible|terribly|unbearable|very painful|really hurts)\b",
    re.IGNORECASE,
)
PAIN_QUITE = R(r"幾痛|\bquite (a bit|a lot)\b|\bpretty (bad|sore)\b", re.IGNORECASE)
PAIN_MILD = R(r"少少痛|有啲痛|\b(a little|a bit|slight|mild)\b", re.IGNORECASE)
PAIN_ANY = R(r"痛|\b(pain|hurts?|sore|aching)\b", re.IGNORECASE)
LOCATIONS = [
    ("膝|knee", "knee"),
    ("背|\\bback\\b", "back"),
    ("頭|\\bhead\\b", "head"),
    ("腳|\\bleg\\b", "leg"),
    ("膊|\\bshoulder\\b", "shoulder"),
    ("心口|\\bchest\\b", "chest"),
]
OUT_NO = R(
    r"冇出(街|過街|去|門)?|唔想出街|冇落街|唔出街|\b(didn'?t go out|haven'?t been out|stayed (in|home)|stay(ed)? at home|didn'?t leave)\b",
    re.IGNORECASE,
)
OUT_YES = R(
    r"落咗街|出咗街|出咗去|去咗(公園|街市)|落街|買餸|\b(went out|went to the (park|market|shop)|went for a walk|been out|did some shopping)\b",
    re.IGNORECASE,
)
FAMILY = R(
    r"個仔|個女|孫|屋企人|打俾我|通電話|\b(son|daughter|grand(son|daughter|child(ren)?)|family|phoned me|called me)\b",
    re.IGNORECASE,
)
FRIEND = R(r"朋友|\bfriends?\b", re.IGNORECASE)
NEIGHBOUR = R(r"街坊|鄰居|\bneighbou?rs?\b", re.IGNORECASE)
CENTRE = R(r"中心|\bcent(re|er)\b", re.IGNORECASE)
LONELY = R(
    r"孤單|寂寞|好靜|成日(都)?得我一個|\b(lonely|alone|no one to talk|miss (my|them))\b",
    re.IGNORECASE,
)
MOOD_LOW = R(r"好悶|冇精神|唔開心|好攰|\b(sad|down|miserable)\b", re.IGNORECASE)
MOOD_HIGH = R(r"開心|\b(happy|great|wonderful|cheerful)\b", re.IGNORECASE)
MOOD_MID = R(r"一般|冇乜特別|\b(so-?so|okay|alright)\b", re.IGNORECASE)
CONF_REPEAT = R(
    r"頭先問過|你頭先.{0,6}問|\b(didn'?t you just ask|you already asked|i asked (you )?that)\b",
    re.IGNORECASE,
)
CONF_DAY = R(
    r"今日係星期幾|唔記得.{0,4}日子|\b(what day is it|what'?s the date|is it (mon|tues|wednes|thurs|fri|satur|sun)day)\b",
    re.IGNORECASE,
)
VISIT = R(r"(嚟|來)?探我|嚟探|\bvisit(ing|s)?\b|\bcoming (over|to see)\b", re.IGNORECASE)
DECLINE = R(
    r"唔使|唔想|冇興趣|算啦|\b(no thanks|not really|no,|don'?t want|can'?t be bothered)\b",
    re.IGNORECASE,
)
ACCEPT = R(
    r"好呀|好啊|得喎|可以|聽落好|有興趣|\b(ok|okay|sure|yes|good idea|why not|i will|i'?ll try)\b",
    re.IGNORECASE,
)
FAMILY_WORDS = [
    ("孫女", "granddaughter", "孫女"),
    ("孫仔", "grandson", "孫仔"),
    ("granddaughter", "granddaughter", "孫女"),
    ("grandson", "grandson", "孫仔"),
    ("個仔", "son", "個仔"),
    ("son", "son", "個仔"),
    ("個女", "daughter", "個女"),
    ("daughter", "daughter", "個女"),
]
PAIN_YUE = {"mild": "有少少", "moderate": "幾", "severe": "好"}


def _last(turns: list[dict], *patterns: re.Pattern) -> tuple[re.Match | None, int | None]:
    """Most recent elder turn matching any pattern (first pattern wins within a turn)."""
    for t in reversed(turns):
        for p in patterns:
            if m := p.search(t["text"]):
                return m, t["index"]
    return None, None


def _find(turns: list[dict], pattern: re.Pattern) -> int | None:
    return _last(turns, pattern)[1]


def extract(turns: list[dict], nudge_turn: int | None = None) -> dict:
    """`turns`: [{index, speaker, text}]. Returns a dict that validates as `Extraction`."""
    elder = [t for t in turns if t["speaker"] == "elder" and t["text"].strip()]

    def pick(*pairs: tuple[re.Pattern, str], default: str = "unknown") -> tuple[str, list[int]]:
        for pat, value in pairs:
            if (idx := _find(elder, pat)) is not None:
                return value, [idx]
        return default, []

    appetite, ev_app = pick(
        (APPETITE_SKIP, "skipped_meals"), (APPETITE_LESS, "reduced"), (APPETITE_OK, "normal")
    )
    sleep, ev_sleep = pick((SLEEP_POOR, "poor"), (SLEEP_GOOD, "good"))
    meds, ev_meds = pick((MEDS_PARTIAL, "partial"), (MEDS_NO, "no"), (MEDS_YES, "yes"))
    out, ev_out = pick((OUT_NO, "no"), (OUT_YES, "yes"))

    pain, ev_pain, loc = "unknown", [], None
    pain_turn = next(
        (t for t in reversed(elder) if PAIN_NONE.search(t["text"]) or PAIN_ANY.search(t["text"])),
        None,
    )
    if pain_turn:
        text, ev_pain = pain_turn["text"], [pain_turn["index"]]
        if PAIN_NONE.search(text):
            pain = "none"
        else:
            pain = (
                "severe"
                if PAIN_SEVERE.search(text)
                else (
                    "moderate"
                    if PAIN_QUITE.search(text)  # "quite a bit" is not "a bit"
                    else "mild" if PAIN_MILD.search(text) else "moderate"
                )
            )
            loc = next(
                (name for pat_, name in LOCATIONS if re.search(pat_, text, re.IGNORECASE)), None
            )

    contact, ev_contact = [], []
    for pat, name in (
        (FAMILY, "family_call"),
        (FRIEND, "friend"),
        (NEIGHBOUR, "neighbour"),
        (CENTRE, "centre"),
    ):
        if (idx := _find(elder, pat)) is not None:
            contact.append(name)
            ev_contact.append(idx)
    lonely_idx = _find(elder, LONELY)

    mood, ev_mood = None, []
    if lonely_idx is not None:
        mood, ev_mood = 2, [lonely_idx]
    else:
        for value, pat in ((2, MOOD_LOW), (4, MOOD_HIGH), (3, MOOD_MID)):
            if (idx := _find(elder, pat)) is not None:
                mood, ev_mood = value, [idx]
                break

    cues, ev_conf = [], []
    for pat, name in ((CONF_REPEAT, "repeated_question"), (CONF_DAY, "wrong_day_or_date")):
        if (idx := _find(elder, pat)) is not None:
            cues.append(name)
            ev_conf.append(idx)

    facts = []
    for t in elder:
        if VISIT.search(t["text"]):
            who = next(
                (
                    (en, yue)
                    for key, en, yue in FAMILY_WORDS
                    if re.search(key, t["text"], re.IGNORECASE)
                ),
                None,
            )
            if who:
                facts.append(
                    {
                        "fact_en": f"A visit from their {who[0]} is coming up",
                        "fact_yue": f"{who[1]}會嚟探佢",
                        "category": "event",
                        "expires_in_days": 7,
                    }
                )
    if pain in ("moderate", "severe") and loc:
        facts.append(
            {
                "fact_en": f"Has {pain} pain in the {loc}",
                "fact_yue": f"{LOC_YUE.get(loc, loc)}{PAIN_YUE[pain]}痛",
                "category": "health",
                "expires_in_days": 14,
            }
        )

    nudge = "not_offered"
    if nudge_turn is not None:
        nxt = next((t for t in elder if t["index"] > nudge_turn), None)
        nudge = (
            "unclear"
            if nxt is None
            else (
                "declined"
                if DECLINE.search(nxt["text"])
                else "interested" if ACCEPT.search(nxt["text"]) else "unclear"
            )
        )

    concerns = []
    if appetite == "skipped_meals":
        concerns.append("Skipped a meal")
    if pain in ("moderate", "severe"):
        concerns.append(f"{pain.capitalize()} pain" + (f" ({loc})" if loc else ""))
    if lonely_idx is not None:
        concerns.append("Feels lonely")
    if cues:
        concerns.append("Signs of confusion")
    if meds in ("no", "partial"):
        concerns.append("Missed some medication")

    evidence_fields = sum(bool(e) for e in (ev_app, ev_sleep, ev_meds, ev_pain, ev_out, ev_mood))
    x = {
        "mood": {"value": mood, "confidence": "medium", "evidence_turns": ev_mood},
        "sleep": {"value": sleep, "confidence": "medium", "evidence_turns": ev_sleep},
        "appetite": {"value": appetite, "confidence": "medium", "evidence_turns": ev_app},
        "meds_taken": {"value": meds, "confidence": "medium", "evidence_turns": ev_meds},
        "pain": {"value": pain, "location": loc, "evidence_turns": ev_pain},
        "went_out_today": {"value": out, "evidence_turns": ev_out},
        "social_contact": {"value": contact or ["none_mentioned"], "evidence_turns": ev_contact},
        "loneliness_expressed": {
            "value": lonely_idx is not None,
            "evidence_turns": [lonely_idx] if lonely_idx is not None else [],
        },
        "confusion_cues": {"value": cues or ["none"], "evidence_turns": ev_conf},
        "concerns": concerns,
        "new_memory_facts": facts,
        "nudge_response": nudge,
        "overall_confidence": "medium" if evidence_fields >= 3 else "low",
    }
    x["summary_en"], x["summary_yue"] = compose(x)
    return x
