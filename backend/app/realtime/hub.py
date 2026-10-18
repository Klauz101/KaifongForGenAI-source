"""WebSocket broadcast for staff dashboards (`/ws/dashboard`)."""

import asyncio
import logging

from fastapi import WebSocket
from fastapi.encoders import jsonable_encoder

logger = logging.getLogger(__name__)


class Hub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        self._clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def broadcast(self, event: dict) -> None:
        payload = jsonable_encoder(
            event
        )  # events carry datetimes etc.; plain json.dumps would fail

        async def send(ws: WebSocket) -> None:
            try:
                await ws.send_json(payload)
            except Exception:  # a dead socket must not break the others, but never fail silently
                logger.exception(
                    "dropping dashboard connection after failed send of %s", event.get("type")
                )
                self.disconnect(ws)

        await asyncio.gather(*(send(ws) for ws in list(self._clients)))


hub = Hub()
