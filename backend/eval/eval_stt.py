"""Speech-to-text evaluation: character error rate (CER) on consented clips (SPEC 15 Phase 7).

Put clips in eval/stt_eval/ with a manifest.yaml (see README.md there). With no clips this reports
"not yet measured": it never invents a number. Run: `cd backend && .venv/bin/python -m eval.eval_stt`.
"""

import asyncio
import re
from pathlib import Path

import yaml

from app.conversation.audio import AudioConversionError, to_wav_16k
from app.providers.registry import get_providers
from eval.common import EVAL_DIR

STT_DIR = EVAL_DIR / "stt_eval"
_PUNCT = re.compile(r"[\s　，。！？、；：,.!?;:'\"`~()（）「」『』【】\[\]{}<>《》…—\-_/\\|*]+")


def normalise(text: str) -> str:
    return _PUNCT.sub("", text.lower())


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(reference: str, hypothesis: str) -> float:
    """Character error rate after dropping spaces/punctuation: edits / reference length."""
    ref, hyp = normalise(reference), normalise(hypothesis)
    if not ref:
        raise ValueError("empty reference")
    return edit_distance(ref, hyp) / len(ref)


async def _transcribe(clip: Path, language: str, stt) -> str:
    audio = clip.read_bytes()
    if stt.needs_wav:
        audio = await to_wav_16k(audio)
    return (await stt.transcribe(audio, language)).text


def run(directory: Path | None = None) -> dict:
    directory = directory or STT_DIR
    stt = get_providers().stt
    base = {
        "provider": stt.name,
        "clips": 0,
        "status": "not yet measured",
        "cer_pct": None,
        "per_clip": [],
        "errors": [],
    }
    manifest = directory / "manifest.yaml"
    if not manifest.exists():
        return {
            **base,
            "note": "No clips yet: add consented recordings (see eval/stt_eval/README.md).",
        }
    entries = (yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}).get("clips") or []
    per_clip, errors, edits, ref_chars = [], [], 0, 0
    for e in entries:
        if e.get("consented") is not True:
            errors.append({"file": e.get("file"), "error": "skipped: not marked consented: true"})
            continue
        try:
            hyp = asyncio.run(_transcribe(directory / e["file"], e.get("language", "yue"), stt))
            ref = normalise(e["reference"])
            d = edit_distance(ref, normalise(hyp))
        except (AudioConversionError, OSError, ValueError, KeyError) as exc:
            errors.append({"file": e.get("file"), "error": str(exc)})
            continue
        edits, ref_chars = edits + d, ref_chars + len(ref)
        per_clip.append(
            {
                "file": e["file"],
                "language": e.get("language", "yue"),
                "age_band": e.get("age_band"),
                "cer_pct": round(100 * d / len(ref), 1),
                "reference": e["reference"],
                "hypothesis": hyp,
            }
        )
    if not per_clip:
        return {**base, "errors": errors, "note": "Manifest has no usable consented clips."}
    return {
        **base,
        "clips": len(per_clip),
        "status": "measured",
        "cer_pct": round(100 * edits / ref_chars, 1),
        "per_clip": per_clip,
        "errors": errors,
    }


if __name__ == "__main__":
    out = run()
    print({k: v for k, v in out.items() if k != "per_clip"})
