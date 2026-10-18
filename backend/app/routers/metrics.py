import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.analysis.cost import estimate, summarise
from app.conversation.service import latency_stats
from app.db import get_session
from app.models import Call, Turn, User
from app.providers.registry import get_providers
from app.security.auth import require_staff

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


@router.get("/latency")
def latency(user: User = Depends(require_staff), db: Session = Depends(get_session)) -> dict:
    """Reply latency for live (non-seed) calls. ttfa = time from input received to first reply audio."""
    if user.role != "admin":
        raise HTTPException(403, "Admin only")
    rows = db.exec(
        select(Turn, Call)
        .join(Call, Turn.call_id == Call.id)
        .where(Call.mode != "seed", Turn.speaker == "kaifong")
        .order_by(Turn.id.desc())
        .limit(200)
    ).all()
    turns = [
        {"call_id": t.call_id, "index": t.index, **t.latency_breakdown}
        for t, _ in reversed(rows)
        if t.latency_breakdown.get("ttfa_ms") is not None
    ]
    return {
        "providers": get_providers().names(),
        "stats": latency_stats([t["ttfa_ms"] for t in turns]),
        "target_ms": 3000,
        "turns": turns[-40:],
    }


@router.get("/cost")
def cost(user: User = Depends(require_staff), db: Session = Depends(get_session)) -> dict:
    """Estimated cost per call and per person per month, from metered usage of live (non-seed) calls.

    Prices come from config/pricing.yaml; an unset price means "not yet measured", never a guess.
    """
    if user.role != "admin":
        raise HTTPException(403, "Admin only")
    calls = db.exec(
        select(Call)
        .where(Call.mode != "seed", Call.status != "in_progress")
        .order_by(Call.id.desc())
        .limit(200)
    ).all()
    calls = [c for c in calls if c.usage]
    return {
        **summarise([c.usage for c in calls]),
        "calls": [
            {"call_id": c.id, "mode": c.mode, "usage": c.usage, **estimate(c.usage)}
            for c in calls[:30]
        ],
    }


@router.get("/eval")
def evaluation(user: User = Depends(require_staff)) -> dict:
    """Latest `make eval` results (red flags, extraction, triage, STT, latency); null until it has been run."""
    if user.role != "admin":
        raise HTTPException(403, "Admin only")
    path = Path(__file__).resolve().parents[2] / "eval" / "results.json"
    return {"results": json.loads(path.read_text(encoding="utf-8")) if path.exists() else None}
