"""Sentence splitting so TTS can start on sentence one (SPEC 6.2)."""

import re

ENDERS = "。！？!?.…"
ABBREV = {"mr", "mrs", "ms", "dr", "st"}


def _is_boundary(buf: str, i: int) -> bool:
    ch = buf[i]
    if ch not in ENDERS:
        return False
    if ch == ".":
        word = re.search(r"(\w+)\.$", buf[: i + 1])
        if word and word.group(1).lower() in ABBREV:
            return False
        if i + 1 < len(buf) and not buf[i + 1].isspace():
            return False  # e.g. "3.5", "a.m"
        if i + 1 == len(buf):
            return False  # might be an abbreviation split mid-stream; flushed at the end
    return True


class SentenceBuffer:
    """Feed streamed text deltas; get back complete sentences as they finish."""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, delta: str) -> list[str]:
        self._buf += delta
        out: list[str] = []
        start = 0
        i = 0
        while i < len(self._buf):
            if _is_boundary(self._buf, i):
                j = i + 1
                while j < len(self._buf) and self._buf[j] in ENDERS + "”\"'」』）)":
                    j += 1
                sentence = self._buf[start:j].strip()
                if sentence:
                    out.append(sentence)
                start = i = j
            else:
                i += 1
        self._buf = self._buf[start:]
        return out

    def flush(self) -> list[str]:
        rest, self._buf = self._buf.strip(), ""
        return [rest] if rest else []


def split_sentences(text: str) -> list[str]:
    buf = SentenceBuffer()
    return buf.feed(text) + buf.flush()
