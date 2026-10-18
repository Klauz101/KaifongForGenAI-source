"""Per-call usage metering (SPEC 9.3): what each call sent to paid services.

Providers report LLM tokens into a context variable; the call loop wraps each LLM use in `metered(...)`
so chat, extraction and translation tokens are counted separately without changing provider interfaces.
"""

from contextlib import contextmanager
from contextvars import ContextVar, Token

from sqlmodel import Session

from app.db import engine
from app.models import Call

usage_sink: ContextVar[dict | None] = ContextVar("usage_sink", default=None)


def report_tokens(input_tokens: int, output_tokens: int) -> None:
    """Called by LLM providers when the vendor reports token usage."""
    sink = usage_sink.get()
    if sink is not None:
        sink["in"] += input_tokens or 0
        sink["out"] += output_tokens or 0


def add_usage(call_id: int | None, **amounts: float) -> None:
    """Add to a call's usage counters (atomic enough for one-process SQLite)."""
    amounts = {k: v for k, v in amounts.items() if v}
    if call_id is None or not amounts:
        return
    with Session(engine) as db:
        call = db.get(Call, call_id)
        if call is None:
            return
        usage = dict(call.usage or {})
        for key, value in amounts.items():
            usage[key] = round(usage.get(key, 0) + value, 4)
        call.usage = usage
        db.add(call)
        db.commit()


def begin() -> tuple[Token, dict]:
    sink = {"in": 0, "out": 0}
    return usage_sink.set(sink), sink


def end(handle: tuple[Token, dict], call_id: int | None, kind: str) -> None:
    token, sink = handle
    usage_sink.reset(token)
    add_usage(call_id, **{f"llm_{kind}_in": sink["in"], f"llm_{kind}_out": sink["out"]})


@contextmanager
def metered(call_id: int | None, kind: str):
    """`kind` is chat | extract | translate."""
    handle = begin()
    try:
        yield
    finally:
        end(handle, call_id, kind)
