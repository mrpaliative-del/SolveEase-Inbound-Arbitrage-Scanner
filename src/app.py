
import base64
import binascii
import os
import sqlite3
from typing import Any

import jwt
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, ConfigDict

from .database import (
    initialize_database,
    verify_user_quota,
    increment_user_usage,
)
from .core_wasm import execute_low_level_parse
from .constraint_engine import map_unstructured_to_schema


DB_PATH = os.getenv(
    "DATABASE_PATH",
    "/var/data/inbound_arbitrage.db",
)
JWT_SECRET = os.getenv("JWT_SECRET", "")
MAX_PAYLOAD_BYTES = 2 * 1024 * 1024

if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET must be configured")

app = FastAPI(
    title="SolveEase Inbound Arbitrage Scanner",
    description="1G-5G structured ingestion and processing gateway",
    version="1.1.0",
)

# Configure exact frontend origins, separated by commas.
allowed_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "").split(",")
    if origin.strip()
]

if allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

bearer_scheme = HTTPBearer(auto_error=False)


class TaskPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    body: str = Field(
        ...,
        min_length=1,
        description="UTF-8 text or base64-encoded bytes",
    )
    is_base64: bool = Field(
        default=False,
        alias="isBase64Encoded",
    )


def authenticated_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(
        bearer_scheme
    ),
) -> str:
    """Validate a signed HS256 JWT and extract its subject."""

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        claims = jwt.decode(
            credentials.credentials,
            JWT_SECRET,
            algorithms=["HS256"],
            options={"require": ["sub", "exp"]},
        )
        user_id = claims.get("sub")

        if not isinstance(user_id, str) or not user_id.strip():
            raise jwt.InvalidTokenError("Invalid subject")

        return user_id.strip()

    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )


@app.get("/health")
def operational_health_check() -> dict[str, str]:
    """Basic liveness check; does not claim inference is operational."""
    return {
        "status": "healthy",
        "service": "SolveEase Inbound Arbitrage Scanner",
    }


@app.post("/api/v1/process-task")
def handle_inbound_task_operation(
    payload: TaskPayload,
    user_id: str = Depends(authenticated_user_id),
) -> dict[str, Any]:
    """Authenticate, reserve quota, process input, and return extraction."""

    if not payload.body.strip():
        raise HTTPException(
            status_code=400,
            detail="Empty payload",
        )

    # Bound encoded input before decoding.
    if len(payload.body) > MAX_PAYLOAD_BYTES * 2:
        raise HTTPException(
            status_code=413,
            detail="Payload exceeds the size limit",
        )

    try:
        if payload.is_base64:
            raw_bytes = base64.b64decode(
                payload.body,
                validate=True,
            )
        else:
            raw_bytes = payload.body.encode("utf-8")

    except (binascii.Error, ValueError, UnicodeEncodeError):
        raise HTTPException(
            status_code=400,
            detail="Invalid payload encoding",
        )

    if not raw_bytes:
        raise HTTPException(
            status_code=400,
            detail="Decoded payload is empty",
        )

    if len(raw_bytes) > MAX_PAYLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail="Payload exceeds the 2 MiB limit",
        )

    conn = None
    reserved = False

    try:
        # Use a separate connection for each request.
        conn = initialize_database(DB_PATH)

        quota = verify_user_quota(conn, user_id)

        if quota is None:
            raise HTTPException(
                status_code=403,
                detail="Account is not provisioned",
            )

        # Atomically reserve one unit before inference.
        if not increment_user_usage(conn, user_id):
            raise HTTPException(
                status_code=429,
                detail="Monthly usage allowance reached",
            )

        reserved = True

        cleaned_text = execute_low_level_parse(raw_bytes)

        if not cleaned_text:
            raise HTTPException(
                status_code=400,
                detail="Payload contains no usable text",
            )

        result = map_unstructured_to_schema(cleaned_text)

        current_quota = verify_user_quota(conn, user_id)

        if current_quota is None:
            raise RuntimeError("Account missing after reservation")

        tier, usage, limit = current_quota
        reserved = False

        return {
            "status": "success",
            "account_tier": tier,
            "monthly_consumption": usage,
            "monthly_allowance": limit,
            "payload": result,
        }

    except HTTPException:
        # HTTP errors are intentional and should retain their status.
        if reserved and conn is not None:
            _refund_reservation(conn, user_id)
            reserved = False
        raise

    except Exception:
        if reserved and conn is not None:
            _refund_reservation(conn, user_id)
            reserved = False

        # Do not send internal exception details to clients.
        raise HTTPException(
            status_code=502,
            detail="Processing provider or internal service failed",
        )

    finally:
        if conn is not None:
            conn.close()


def _refund_reservation(
    conn: sqlite3.Connection,
    user_id: str,
) -> None:
    """
    Best-effort refund for a failed request in the current UTC month.
    A reservation ledger is recommended for stronger accounting.
    """
    from datetime import datetime, timezone

    month = datetime.now(timezone.utc).strftime("%Y-%m")

    try:
        with conn:
            conn.execute(
                """
                UPDATE users
                SET usage_count = usage_count - 1
                WHERE user_id = ?
                  AND usage_month = ?
                  AND usage_count > 0
                """,
                (user_id, month),
            )
    except sqlite3.Error:
        # Log privately in production; never expose database details.
        pass
