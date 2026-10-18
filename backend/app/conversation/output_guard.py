"""Output guard (SPEC 8.4): every LLM sentence is checked before it is spoken.

Blocks: requests for money/bank/ID/password/OTP/address; dosing or diagnosis language; more than 3
sentences; markdown/emoji/lists; claiming to be human. Scripted lines are trusted and not checked.
"""

import re
from dataclasses import dataclass

MAX_SENTENCES = 3

# Mentioning these is only allowed inside a refusal ("Kaifong never handles money; hang up on anyone who asks").
SENSITIVE = re.compile(
    r"銀行|戶口號碼|賬戶|帳戶|密碼|身份證|身分證|驗證碼|信用卡|八達通密碼|轉賬|轉帳|匯款|俾錢|畀錢|地址|住址|"
    r"\b(bank|account number|password|passcode|pin|hkid|id number|otp|one[- ]time|credit card|card number|"
    r"transfer money|send money|wire|home address|your address)\b",
    re.IGNORECASE,
)
REFUSAL = re.compile(
    r"從來唔|從來冇|唔會(問|要|收)|千祈唔好|掛線|唔好(俾|畀|講)|"
    r"\b(never (ask|handle|need|take)|do not share|don'?t share|hang up|never share)\b",
    re.IGNORECASE,
)
DOSING = re.compile(
    r"\d+\s*(mg|毫克|毫升|ml|粒|顆|片|tablets?|pills?|capsules?)|每日.{0,3}(次|粒|片)|加藥|減藥|停藥|轉藥|"
    r"\b(increase|double|reduce|lower|skip|stop) (your |the )?(dose|dosage|medic\w*|pills?)\b|"
    r"\btake (two|three|\d+) |\bstop taking\b",
    re.IGNORECASE,
)
DIAGNOSIS = re.compile(
    r"診斷|你患有|你有(.{0,4})(症|病)|\b(you (have|suffer from|might have|probably have|may have) "
    r"(diabetes|dementia|depression|arthritis|cancer|parkinson|a heart|hypertension|high blood pressure)|"
    r"i diagnos\w+|diagnosis)\b",
    re.IGNORECASE,
)
FORMATTING = re.compile(
    r"^\s*([-*•]|\d+[.)])\s|\*\*|__|^#{1,6}\s|`|\[[^\]]+\]\([^)]+\)|"
    r"[\U0001F300-\U0001FAFF☀-➿⭐❤️]",
    re.MULTILINE,
)
CLAIMS_HUMAN = re.compile(
    r"我係真人|我係人類|我唔係(AI|機械人|機器人)|我係一個人|"
    r"\b(i am|i'?m) (a |an )?(real |actual )?(human|person|man|woman)\b|\bi'?m not (an? )?(ai|robot|bot|machine)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    reason: str = ""


def check(sentence: str, index: int) -> GuardResult:
    """Check one sentence; `index` is its 0-based position in the reply."""
    if index >= MAX_SENTENCES:
        return GuardResult(False, "too_long")
    if FORMATTING.search(sentence):
        return GuardResult(False, "formatting")
    if CLAIMS_HUMAN.search(sentence):
        return GuardResult(False, "claims_human")
    if SENSITIVE.search(sentence) and not REFUSAL.search(sentence):
        return GuardResult(False, "sensitive_request")
    if DOSING.search(sentence):
        return GuardResult(False, "medication_advice")
    if DIAGNOSIS.search(sentence):
        return GuardResult(False, "diagnosis")
    return GuardResult(True)


def check_reply(text: str) -> GuardResult:
    """Convenience for whole replies (tests, non-streaming paths)."""
    from app.conversation.sentences import split_sentences

    for i, sentence in enumerate(split_sentences(text) or [text]):
        result = check(sentence, i)
        if not result.ok:
            return result
    return GuardResult(True)
