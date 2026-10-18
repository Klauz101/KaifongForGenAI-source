"""Kaifong FastAPI application."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import jyutping as jyutping_lexicon
from app.config import get_settings
from app.conversation import service
from app.db import init_db
from app.jobs.retention import retention_loop
from app.providers.base import ProviderConfigError
from app.providers.registry import get_providers
from app.routers import (
    alerts,
    auth,
    calls,
    demo,
    digest,
    elders,
    jyutping,
    metrics,
    privacy,
    telephony,
    ws,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    stale = service.abort_stale_calls()
    if stale:
        logger.warning("Marked %d stale in-progress call(s) as aborted", stale)
    try:  # pre-generate filler audio so the first call is not slower
        for lang in ("yue", "en"):
            await service.filler(lang)
    except ProviderConfigError:
        logger.exception("Provider misconfigured; /api/health will report it")
    # warm the Jyutping lexicon so the first user is not kept waiting (keep a reference: tasks can be collected)
    warm_task = asyncio.create_task(asyncio.to_thread(jyutping_lexicon.warm))
    retention_task = asyncio.create_task(retention_loop())
    yield
    retention_task.cancel()
    await asyncio.gather(warm_task, return_exceptions=True)


app = FastAPI(title="Kaifong 街坊", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)
init_db()  # also at import time so tests/scripts using the app without lifespan work
for _router in (
    auth.router,
    elders.router,
    calls.router,
    alerts.router,
    metrics.router,
    demo.router,
    digest.router,
    telephony.router,
    privacy.router,
    jyutping.router,
    ws.router,
):
    app.include_router(_router)


@app.get("/api/health")
def health() -> dict:
    try:
        names = get_providers().names()
        warnings = []
        if any(v != "mock" for v in names.values()) and not settings.provider_no_training_confirmed:
            # PDPO DPP3: the team must switch on each vendor's "do not train on our data" setting
            warnings.append(
                "Real AI providers are in use but PROVIDER_NO_TRAINING_CONFIRMED is not set: confirm each "
                "vendor's 'no training on our data' setting is enabled, then set the flag."
            )
        return {"status": "ok", "env": settings.app_env, "providers": names, "warnings": warnings}
    except (ProviderConfigError, NotImplementedError) as exc:
        return {"status": "misconfigured", "env": settings.app_env, "error": str(exc)}
