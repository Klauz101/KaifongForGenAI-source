"""Jyutping to characters, for the simulator's type box (live preview with alternatives)."""

import asyncio

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app import jyutping
from app.models import User
from app.security.auth import require_staff

router = APIRouter(prefix="/api/jyutping", tags=["jyutping"])


class ConvertBody(BaseModel):
    text: str = Field(max_length=jyutping.MAX_INPUT_CHARS)


@router.post("/convert")
async def convert(body: ConvertBody, _: User = Depends(require_staff)) -> dict:
    """Best-guess characters plus alternatives per word. Non-Jyutping text comes back unchanged."""
    result = await asyncio.to_thread(
        jyutping.convert, body.text
    )  # first call builds the lexicon (~2 s)
    return {
        "is_jyutping": result.is_jyutping,
        "hanzi": result.hanzi,
        "segments": [
            {
                "kind": s.kind,
                "text": s.text,
                "options": s.options,
                "chosen": s.chosen,
                "known": s.known,
            }
            for s in result.segments
        ],
    }
