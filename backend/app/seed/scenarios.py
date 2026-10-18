"""Scripted 21-day trajectories for the 8 synthetic personas (SPEC Section 12).

`d` is days ago: 0 = today, 20 = oldest. All people and numbers are fake.
"""

import random
from collections.abc import Callable
from dataclasses import dataclass, field, replace


@dataclass
class DaySpec:
    status: str = "completed"  # completed | no_answer
    mood: int | None = 4
    sleep: str = "good"
    appetite: str = "normal"
    meds: str = "yes"
    pain: str = "none"
    pain_loc: str | None = None
    went_out: str = "yes"
    contact: list[str] = field(default_factory=lambda: ["family_call"])
    lonely: bool = False
    confusion: list[str] = field(default_factory=lambda: ["none"])
    confidence: str = "high"
    hard_to_hear: bool = False


@dataclass
class Persona:
    key: str
    display_name: str
    display_name_en: str
    full_name: str
    phone: str
    age: int
    gender: str
    language: str
    call_time: str
    interests: list[str]
    care_notes: str
    day: Callable[[int, random.Random], DaySpec]
    facts: list[tuple[str, str, str]]  # (fact_en, fact_yue, category)
    family_digest: bool = True


def _stable(d: int, rng: random.Random) -> DaySpec:
    spec = DaySpec(
        mood=rng.choice([4, 4, 5]), contact=[rng.choice(["family_call", "friend", "neighbour"])]
    )
    if d == 9:
        spec = replace(spec, sleep="poor")
    return spec


def _chan(d: int, rng: random.Random) -> DaySpec:
    s = DaySpec(mood=4, contact=["family_call"])
    if d >= 14:
        return replace(s, pain=rng.choice(["none", "mild"]), pain_loc="knee")
    if d >= 7:
        return replace(
            s,
            pain="mild" if d >= 10 else "moderate",
            pain_loc="knee",
            went_out="no" if d in (8, 11) else "yes",
            mood=4 if d >= 10 else 3,
        )
    s = replace(s, pain="moderate", pain_loc="knee", mood=3, contact=["none_mentioned"])
    if d <= 4:
        s = replace(s, went_out="no")
    if d in (0, 1, 3):
        s = replace(s, appetite="skipped_meals")
    if d == 2:
        s = replace(s, pain="severe")
    return s


def _wong(d: int, rng: random.Random) -> DaySpec:
    s = DaySpec(mood=rng.choice([3, 4]), contact=["neighbour"])
    if d in (0, 1, 3, 5):
        s = replace(s, confusion=["repeated_question"] if d != 3 else ["wrong_day_or_date"])
    return s


def _cheung(d: int, rng: random.Random) -> DaySpec:
    if d in (0, 1):
        return DaySpec(status="no_answer", mood=None)
    return replace(_stable(d, rng), contact=["friend"])


def _ho(d: int, rng: random.Random) -> DaySpec:
    s = DaySpec(mood=4 if d >= 8 else 3 if d >= 5 else 2, contact=["none_mentioned"])
    if d == 6:
        s = replace(s, mood=2)
    if d <= 7 and d in (1, 3, 5):
        s = replace(s, went_out="no")
    if d in (0, 1, 2, 4, 6):
        s = replace(s, lonely=True)
    if d in (11, 5):
        s = replace(s, contact=["family_call"])
    return s


def _fung(d: int, rng: random.Random) -> DaySpec:
    if d <= 4:
        return DaySpec(
            mood=None,
            sleep="unknown",
            appetite="unknown",
            meds="unknown",
            pain="unknown",
            went_out="unknown",
            contact=["none_mentioned"],
            confidence="low",
            hard_to_hear=True,
        )
    return replace(_stable(d, rng), contact=["family_call"])


PERSONAS: list[Persona] = [
    Persona(
        "chan",
        "陳婆婆",
        "Mrs Chan",
        "Chan Demo Mei",
        "0000 0001",
        78,
        "F",
        "yue",
        "09:30",
        ["wet market", "grandchildren", "Cantonese opera"],
        "Synthetic. Lives alone in public housing.",
        _chan,
        [
            ("Granddaughter is visiting on Sunday", "孫女星期日嚟探佢", "event"),
            ("Left knee pain, worse when walking", "左邊膝頭哥痛，行路更痛", "health"),
            ("Enjoys Cantonese opera on the radio", "鍾意聽粵劇", "interest"),
        ],
    ),
    Persona(
        "lee",
        "李伯伯",
        "Mr Lee",
        "Lee Demo Kwok",
        "0000 0002",
        82,
        "M",
        "yue",
        "09:00",
        ["Chinese chess", "park", "tea"],
        "Synthetic.",
        _stable,
        [
            ("Plays Chinese chess in the park most mornings", "朝早喺公園落象棋", "routine"),
            ("Son calls every Sunday", "個仔逢星期日打嚟", "family"),
        ],
    ),
    Persona(
        "wong",
        "黃太",
        "Mrs Wong",
        "Wong Demo Siu",
        "0000 0003",
        75,
        "F",
        "yue",
        "10:00",
        ["mahjong", "cooking"],
        "Synthetic.",
        _wong,
        [
            ("Neighbour brings soup on Fridays", "鄰居逢星期五送湯俾佢", "routine"),
            ("Granddaughter's exam is next week", "孫女下星期考試", "event"),
        ],
    ),
    Persona(
        "cheung",
        "張伯",
        "Mr Cheung",
        "Cheung Demo Wai",
        "0000 0004",
        80,
        "M",
        "yue",
        "09:15",
        ["birds", "newspapers"],
        "Synthetic.",
        _cheung,
        [("Keeps a pet bird", "養咗隻雀仔", "interest")],
    ),
    Persona(
        "ho",
        "何婆婆",
        "Mrs Ho",
        "Ho Demo Lan",
        "0000 0005",
        86,
        "F",
        "yue",
        "09:45",
        ["knitting", "old films"],
        "Synthetic. Son emigrated last year.",
        _ho,
        [
            ("Son emigrated last year and calls rarely", "個仔移民咗，好少打嚟", "family"),
            ("Likes knitting scarves", "鍾意織頸巾", "interest"),
        ],
    ),
    Persona(
        "lau",
        "劉生",
        "Mr Lau",
        "Lau Demo Ming",
        "0000 0006",
        70,
        "M",
        "yue",
        "08:45",
        ["elderly centre", "table tennis"],
        "Synthetic.",
        _stable,
        [("Goes to the elderly centre most days", "好多日都去長者中心", "routine")],
    ),
    Persona(
        "fung",
        "馮婆婆",
        "Mrs Fung",
        "Fung Demo Yee",
        "0000 0007",
        88,
        "F",
        "yue",
        "10:30",
        ["radio", "gardening"],
        "Synthetic. Hearing difficulty.",
        _fung,
        [("Hard of hearing; prefers slow speech", "耳仔重聽，鍾意慢啲講", "health")],
    ),
    Persona(
        "patel",
        "Mrs Patel",
        "Mrs Patel",
        "Patel Demo Asha",
        "0000 0008",
        72,
        "F",
        "en",
        "10:00",
        ["gardening", "cricket"],
        "Synthetic. English-speaking.",
        _stable,
        [
            ("Grows tomatoes on the balcony", "喺露台種蕃茄", "interest"),
            ("Daughter lives in Canada", "女兒喺加拿大", "family"),
        ],
    ),
]
