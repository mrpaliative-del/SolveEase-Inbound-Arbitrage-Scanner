
import base64
import binascii
import os
import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

from .core_wasm import execute_low_level_parse
from .constraint_engine import map_unstructured_to_schema
from .database import (
    complete_daily_slot,
    get_daily_usage,
    initialize_database,
    release_daily_slot,
    reserve_daily_slot,
)


DB_PATH = os.getenv("DATABASE_PATH", "data/inbound_arbitrage.db")
MAX_FILE_BYTES = 2 * 1024 * 1024
DAILY_REQUEST_LIMIT = max(1, int(os.getenv("DAILY_REQUEST_LIMIT", "100")))
PER_CLIENT_PER_MINUTE = max(
    1, int(os.getenv("PER_CLIENT_PER_MINUTE", "5"))
)

app = FastAPI(
    title="SolveEase Inbound Arbitrage Scanner",
    description="A text document extraction API.",
    version="1.0.0",
)

initialize_database(DB_PATH)

cors_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "").split(",")
    if origin.strip()
]

if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
    )


class TaskPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    body: str = Field(min_length=1, max_length=3_000_000)
    is_base64_encoded: bool = Field(
        default=False,
        alias="isBase64Encoded",
    )


# In-memory throttle: not a durable quota or a distributed rate limiter.
_request_times = defaultdict(deque)
_rate_limit_lock = Lock()


def _check_rate_limit(client_key: str) -> None:
    now = time.monotonic()
    cutoff = now - 60

    with _rate_limit_lock:
        recent = _request_times[client_key]

        while recent and recent[0] < cutoff:
            recent.popleft()

        if len(recent) >= PER_CLIENT_PER_MINUTE:
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please wait before trying again.",
            )

        recent.append(now)


def _decode_request_body(payload: TaskPayload) -> bytes:
    try:
        if payload.is_base64_encoded:
            raw = base64.b64decode(payload.body, validate=True)
        else:
            raw = payload.body.encode("utf-8")
    except (binascii.Error, ValueError, UnicodeEncodeError) as exc:
        raise HTTPException(
            status_code=400,
            detail="The submitted content is not valid input.",
        ) from exc

    if not raw:
        raise HTTPException(status_code=400, detail="Input is empty.")

    if len(raw) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail="Input exceeds the 2 MiB limit.",
        )

    return raw


@app.get("/")
def root():
    return {
        "service": "SolveEase Inbound Arbitrage Scanner",
        "health": "/health",
        "process_endpoint": "/api/v1/process-task",
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "service": "SolveEase Inbound Arbitrage Scanner",
        "inference_configured": bool(
            os.getenv("INFERENCE_PROVIDER_API_KEY", "").strip()
        ),
    }


@app.get("/api/v1/usage")
def usage():
    current = get_daily_usage(DB_PATH)
    return {
        "daily_used": current["used"],
        "daily_reserved": current["reserved"],
        "daily_limit": DAILY_REQUEST_LIMIT,
    }


@app.post("/api/v1/process-task")
def process_task(payload: TaskPayload, request: Request):
    client_host = request.client.host if request.client else "unknown"
    _check_rate_limit(client_host)

    raw = _decode_request_body(payload)

    try:
        cleaned_text = execute_low_level_parse(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not reserve_daily_slot(DB_PATH, DAILY_REQUEST_LIMIT):
        raise HTTPException(
            status_code=429,
            detail="The service has reached its daily processing limit.",
        )

    try:
        result = map_unstructured_to_schema(cleaned_text)
        complete_daily_slot(DB_PATH)
    except Exception as exc:
        try:
            release_daily_slot(DB_PATH)
        except Exception:
            # Keep the original processing failure as the primary error.
            pass

        # Log details server-side if needed; never return provider credentials
        # or raw exception details to the browser.
        raise HTTPException(
            status_code=502,
            detail=(
                "Extraction failed. Check the server configuration "
                "and provider logs, then try again."
            ),
        ) from exc

    current = get_daily_usage(DB_PATH)

    return {
        "status": "success",
        "payload": result,
        "usage": {
            "daily_used": current["used"],
            "daily_limit": DAILY_REQUEST_LIMIT,
        },
    }
