import logging
import logging.config
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

from multi_p_h.config import settings
from multi_p_h.core import Service


# Module-level app: uvicorn workers re-import this module, so `app` and
# `service` are constructed fresh in each worker process.
app = FastAPI(
    title="multi_p_h",
    description="Multi-process HTTP service",
    version="0.1.0",
)
service = Service(settings.service)


class EchoRequest(BaseModel):
    message: str


class EchoResponse(BaseModel):
    text: str


@app.get("/ping")
def ping() -> dict[str, str]:
    return {"message": service.ping()}


@app.post("/echo", response_model=EchoResponse)
def echo(req: EchoRequest) -> EchoResponse:
    return EchoResponse(text=service.echo(req.message))


def main() -> int:
    """Entry point: wire dictConfig + start uvicorn in prefork mode.

    Multi-worker uvicorn forks N children that all re-import this module;
    each child binds the same socket via SO_REUSEPORT (uvicorn handles it).
    """
    # Apply dictConfig in the parent so logger names exist before workers fork.
    # Workers will re-apply on import. Both safe.
    logging.config.dictConfig(settings.logs)

    uvicorn.run(
        "multi_p_h.main:app",
        host=settings.service.host,
        port=int(settings.service.port),
        workers=int(settings.service.workers),
        log_config=settings.logs.to_dict() if hasattr(settings.logs, "to_dict") else dict(settings.logs),
        access_log=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
