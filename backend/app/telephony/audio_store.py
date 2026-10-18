"""Short-lived in-memory store so the carrier can fetch our TTS audio by an unguessable URL."""

import secrets
import time

TTL_SECONDS = 600
_store: dict[str, tuple[float, bytes, str]] = {}


def put(audio: bytes, mime: str) -> str:
    now = time.time()
    for token in [t for t, (ts, _, _) in _store.items() if now - ts > TTL_SECONDS]:
        _store.pop(token, None)
    token = secrets.token_urlsafe(24)
    _store[token] = (now, audio, mime)
    return token


def get(token: str) -> tuple[bytes, str] | None:
    item = _store.get(token)
    if item is None or time.time() - item[0] > TTL_SECONDS:
        return None
    return item[1], item[2]
