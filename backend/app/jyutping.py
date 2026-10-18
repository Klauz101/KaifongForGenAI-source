"""Jyutping (粵拼) input support: let someone type Cantonese as romanisation, e.g. "zou2 san4" -> 早晨.

Why: when speaking is not possible (testing, noisy room, no microphone) people type Jyutping, which the
Hanzi-based pieces (safety screen, extraction, the model) cannot read. This module converts it.

How: the rime-cantonese dictionary bundled with `pycantonese` (121k words) gives the reverse mapping
syllables -> characters. Many characters share one syllable (zou2 = 早, 澡, 棗...), so candidates are ranked by
real usage (HKCanCor conversational corpus + sentence corpora) and Kaifong's own vocabulary is boosted, then a
Viterbi segmentation picks the most likely words. The best guess is used by default and the UI shows
alternatives. It is an input aid, not a guarantee: ambiguous or unknown syllables are reported, never hidden.
"""

import json
import math
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

import yaml

MAX_INPUT_CHARS = 400
MAX_SPAN = 8  # longest word, in syllables
UNKNOWN_LOGP = -26.0
TONE_FALLBACK_PENALTY = 6.0  # a wrong/missing tone is common when typing; accepted but costly
INITIALS = (
    "ng",
    "gw",
    "kw",
    "b",
    "p",
    "m",
    "f",
    "d",
    "t",
    "n",
    "l",
    "g",
    "k",
    "h",
    "w",
    "z",
    "c",
    "s",
    "j",
)
FINALS = frozenset(
    [
        "aa",
        "aai",
        "aau",
        "aam",
        "aan",
        "aang",
        "aap",
        "aat",
        "aak",
        "ai",
        "au",
        "am",
        "an",
        "ang",
        "ap",
        "at",
        "ak",
        "e",
        "ei",
        "eu",
        "em",
        "eng",
        "ep",
        "ek",
        "i",
        "iu",
        "im",
        "in",
        "ing",
        "ip",
        "it",
        "ik",
        "o",
        "oi",
        "ou",
        "on",
        "ong",
        "ot",
        "ok",
        "u",
        "ui",
        "un",
        "ung",
        "ut",
        "uk",
        "oe",
        "oeng",
        "oek",
        "eoi",
        "eon",
        "eot",
        "yu",
        "yun",
        "yut",
        "m",
        "ng",
    ]
)
SYLLABLE_RUN = re.compile(r"(?:[a-z]+[1-6])+")
SYLLABLE = re.compile(r"[a-z]+[1-6]")
LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*")
PUNCT = {",": "，", ".": "。", "?": "？", "!": "！", ";": "；", ":": "："}
DATA_DIR = Path(__file__).resolve().parent
DOMAIN_FILES = [
    *DATA_DIR.glob("resources/*.yaml"),
    *DATA_DIR.glob("safety/*.yaml"),
    *DATA_DIR.glob("nudges/*.yaml"),
]


def valid_base(base: str) -> bool:
    if base in FINALS:  # m, ng and syllables with no initial
        return True
    return any(base.startswith(i) and base[len(i) :] in FINALS for i in INITIALS)


def split_syllables(word: str) -> list[str] | None:
    """'zou2san4' -> ['zou2', 'san4']; None if it is not a run of valid Jyutping syllables."""
    word = word.lower()
    if not SYLLABLE_RUN.fullmatch(word):
        return None
    parts = SYLLABLE.findall(word)
    return parts if all(valid_base(p[:-1]) for p in parts) else None


def looks_like_jyutping(text: str) -> bool:
    """True if every Latin word is tone-numbered Jyutping (so English like 'good morning' never matches)."""
    words = LATIN_WORD.findall(text[:MAX_INPUT_CHARS])
    return bool(words) and all(split_syllables(w) is not None for w in words)


def toneless(syllable: str) -> str:
    return syllable[:-1] if syllable and syllable[-1] in "123456" else syllable


def jyutping_syllables(text: str) -> list[str]:
    """All Jyutping syllables in order (tones kept), ignoring anything that is not Jyutping."""
    out: list[str] = []
    for w in LATIN_WORD.findall(text[:MAX_INPUT_CHARS]):
        out.extend(split_syllables(w) or [])
    return out


# ---------------------------------------------------------------------------------------------
# Lexicon (built once, lazily; ~1-2 s)
# ---------------------------------------------------------------------------------------------


@dataclass
class Lexicon:
    words: dict[tuple[str, ...], list[tuple[str, float]]] = field(
        default_factory=dict
    )  # syllables -> [(hanzi, logp)]
    by_base: dict[str, list[tuple[str, float]]] = field(
        default_factory=dict
    )  # toneless syllable -> chars (tone fallback)


_lock = threading.Lock()
_lexicon: Lexicon | None = None


def _cjk_runs() -> list[str]:
    runs: set[str] = set()
    for f in DOMAIN_FILES:
        runs.update(re.findall(r"[一-鿿]{2,}", f.read_text(encoding="utf-8")))
    return sorted(runs)


def _build() -> Lexicon:
    from collections import Counter

    import pycantonese

    base = Path(pycantonese.__file__).parent / "data"
    rime: dict[str, str] = json.loads(
        (base / "rime_cantonese" / "chars_to_jyutping.json").read_text(encoding="utf-8")
    )

    entries: dict[tuple[str, ...], dict[str, float]] = {}

    def add(hanzi: str, syl: list[str], p: float) -> None:
        if len(hanzi) != len(syl) or not all(
            s[-1:] in "123456" and valid_base(s[:-1]) for s in syl
        ):
            return
        slot = entries.setdefault(tuple(syl), {})
        slot[hanzi] = max(slot.get(hanzi, 0.0), p)

    # usage statistics: HKCanCor conversational tokens (words) and sentence corpora (characters)
    tokens = list(pycantonese.hkcancor().tokens())
    word_count: Counter[str] = Counter(t.word for t in tokens if t.word)
    n_tokens = max(1, sum(word_count.values()))
    char_count: Counter[str] = Counter()
    for fname in ("ctcpc", "common_voice"):
        for sent in json.loads((base / fname / "sents.json").read_text(encoding="utf-8")):
            char_count.update(c for c in sent if "一" <= c <= "鿿")
    for w, c in word_count.items():
        char_count.update({ch: c for ch in w if "一" <= ch <= "鿿"})
    n_chars = max(1, sum(char_count.values()))

    def p_char(ch: str) -> float:
        return (char_count.get(ch, 0) + 0.3) / n_chars

    for key, val in rime.items():
        syl = val.split()
        if len(syl) != len(key):
            continue
        if len(key) == 1:  # spoken usage counts: written corpora favour 重 over the colloquial 仲
            add(key, syl, max(p_char(key), 0.6 * word_count.get(key, 0) / n_tokens))
        elif word_count.get(key):
            add(key, syl, 0.6 * word_count[key] / n_tokens + math.prod(p_char(c) for c in key))
        else:  # unseen compound: a mild bonus over independent characters, so real words beat random pairs
            add(key, syl, 4.0 * math.prod(p_char(c) for c in key))
    for (
        t
    ) in (
        tokens
    ):  # colloquial words the dictionary lacks (jyutping per token, possibly multi-syllable)
        syl = SYLLABLE.findall(t.jyutping or "")
        if t.word and syl and len(syl) == len(t.word):
            add(
                t.word,
                syl,
                0.6 * word_count[t.word] / n_tokens + math.prod(p_char(c) for c in t.word),
            )

    vocab = yaml.safe_load((DATA_DIR / "resources" / "colloquial.yaml").read_text(encoding="utf-8"))
    for ch in vocab["chars"]:
        if ch in rime and len(rime[ch].split()) == 1:
            add(ch, rime[ch].split(), 4e-3)
    for w in vocab["words"]:
        segs = pycantonese.characters_to_jyutping(w)
        if all(jp for _, jp in segs):
            add(w, " ".join(jp for _, jp in segs).split(), 6e-3)

    # Kaifong's own vocabulary (red-flag phrases, checklist words, persona lines...) strongly preferred
    for run in _cjk_runs():
        segs = pycantonese.characters_to_jyutping(run)
        if all(j for _, j in segs):
            add(run, " ".join(j for _, j in segs).split(), 8e-3 if len(run) <= 8 else 0)
        for seg, j in segs:
            if j:
                add(seg, j.split(), 5e-3)

    lex = Lexicon()
    for syl, cands in entries.items():
        ranked = sorted(cands.items(), key=lambda kv: -kv[1])[:8]
        lex.words[syl] = [(h, math.log(p)) for h, p in ranked if p > 0]
        if len(syl) == 1:
            lex.by_base.setdefault(toneless(syl[0]), []).extend(lex.words[syl])
    for lst in lex.by_base.values():
        lst.sort(key=lambda x: -x[1])
    return lex


def warm() -> None:
    """Build the lexicon now (call at start-up in a thread so the first user is not kept waiting)."""
    get_lexicon()


def get_lexicon() -> Lexicon:
    global _lexicon
    if _lexicon is None:
        with _lock:
            if _lexicon is None:
                _lexicon = _build()
    return _lexicon


# ---------------------------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------------------------


@dataclass
class Segment:
    kind: str  # "jyutping" (convertible run) or "text" (copied as is)
    text: str  # the original Jyutping for a run (e.g. "zou2 san4"), or the literal text
    options: list[str] = field(default_factory=list)  # candidate characters, best first
    chosen: int = 0
    known: bool = True

    @property
    def output(self) -> str:
        return self.options[self.chosen] if self.kind == "jyutping" and self.options else self.text


@dataclass
class Conversion:
    is_jyutping: bool
    segments: list[Segment]

    @property
    def hanzi(self) -> str:
        return "".join(s.output for s in self.segments)


def _viterbi(syl: list[str], lex: Lexicon) -> list[tuple[int, int, list[str], bool]]:
    """Best segmentation of one syllable run: [(start, length, options, known)]."""
    n = len(syl)
    best = [-math.inf] * (n + 1)
    back: list[tuple[int, int] | None] = [None] * (n + 1)
    best[0] = 0.0
    for i in range(n):
        if best[i] == -math.inf:
            continue
        for length in range(1, min(MAX_SPAN, n - i) + 1):
            cands = lex.words.get(tuple(syl[i : i + length]))
            if cands:
                score = best[i] + cands[0][1]
            elif length == 1:
                score = best[i] + UNKNOWN_LOGP
                alt = lex.by_base.get(toneless(syl[i]))
                if alt:
                    score = best[i] + alt[0][1] - TONE_FALLBACK_PENALTY
            else:
                continue
            if score > best[i + length]:
                best[i + length], back[i + length] = score, (i, length)
    spans, j = [], n
    while j > 0:
        i, length = back[j]  # type: ignore[misc]
        key = tuple(syl[i:j])
        if key in lex.words:
            spans.append((i, length, [h for h, _ in lex.words[key][:5]], True))
        else:
            alt = lex.by_base.get(toneless(syl[i]))
            # a wrong/missing tone: best guess by sound only, flagged as uncertain so the UI highlights it
            spans.append((i, 1, [h for h, _ in alt[:5]] if alt else [], False))
        j = i
    return spans[::-1]


def convert(text: str) -> Conversion:
    """Convert Jyutping in `text` to characters. Non-Jyutping text is kept; unknown syllables stay as typed."""
    text = text[:MAX_INPUT_CHARS]
    is_jp = looks_like_jyutping(text)
    if not is_jp:
        return Conversion(False, [Segment("text", text)])
    lex = get_lexicon()
    segments: list[Segment] = []
    pending: list[tuple[int, int, list[str]]] = (
        []
    )  # consecutive Jyutping words separated only by spaces

    def flush() -> None:
        if not pending:
            return
        syl = [s for _, _, group in pending for s in group]
        for start, length, options, known in _viterbi(syl, lex):
            typed = " ".join(syl[start : start + length])
            segments.append(Segment("jyutping", typed, options if options else [typed], 0, known))
        pending.clear()

    for m in re.finditer(r"[A-Za-z][A-Za-z0-9]*|[^A-Za-z]+", text):
        piece = m.group(0)
        if piece[0].isalpha():
            pending.append((m.start(), m.end(), split_syllables(piece) or []))
        elif piece.isspace() and pending:
            continue  # spaces between syllables are not part of Chinese text
        else:
            flush()
            # Chinese text has no spaces: map punctuation to full-width and drop the spacing around it
            segments.append(
                Segment("text", "".join(PUNCT.get(c, c) for c in piece if not c.isspace()))
            )
    flush()
    return Conversion(True, segments)
