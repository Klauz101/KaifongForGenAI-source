"""Shared helpers for the evaluation scripts. Everything here runs offline; no network is needed."""

from pathlib import Path

import yaml

EVAL_DIR = Path(__file__).resolve().parent
SYNTHETIC_NOTE = (
    "All evaluation data here is SYNTHETIC and was written by the developers, not by real older speakers. "
    "Scores show the software behaves as designed on these cases; they are not accuracy claims about real people."
)


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def pct(n: float, d: float) -> float | None:
    return None if not d else round(100.0 * n / d, 1)
