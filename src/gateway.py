
import base64
import binascii
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

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
MAX_PAYLOAD_BYTES = 2 * 1024 * 1024


def _response(status: int, data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Cache-Control": "no-store",
        },
        "body": json.dumps(data, allow_nan=False),
    }


def _authenticated_user_id(
    event: Dict[str, Any],
) -> Optional[str]:
    """
    Extract identity established by a trusted API Gateway authorizer.
    Never trust a user_id supplied in the request body.
    """
    request_context = event.get("requestContext") or {}
    authorizer = request_context.get("authorizer") or {}

    # API Gateway HTTP API JWT authorizer
    jwt = authorizer.get("jwt") or {}
    claims = jwt.get("claims") or {}

    user_id = claims.get("sub")

    # Other trusted authorizer configurations
    if not user_id:
        user_id = authorizer.get("principalId")

    if not user_id:
        user_id = authorizer.get("user_id")

    if not isinstance(user_id, str) or not user_id.strip():
        return None

    return user_id.strip()


def _release_quota_reservation(
    conn,
    user_id: str,
) -> None:
    """
    Refund a reservation after processing failure, but only within
    the same UTC calendar month in which the reservation was made.
    """
    current_month = datetime.now(timezone.utc).strftime("%Y-%m")

    with conn:
        conn.execute(
            """
            UPDATE users
            SET usage_count = usage_count - 1
            WHERE user_id = ?
              AND usage_month = ?
              AND usage_count > 0
            """,
            (user_id, current_month),
        )


def serverless_handler(
    event: Dict[str, Any],
    context: Any = None,
) -> Dict[str, Any]:
    """Process authenticated inbound requests."""

    if not isinstance(event, dict):
        return _response(400, {"error": "Invalid request format"})

    user_id = _authenticated_user_id(event)

    if not user_id:
        return _response(
            401,
            {"error": "Authentication required"},
        )

    body = event.get("body")

    if not isinstance(body, str) or not body:
        return _response(
            400,
            {"error": "Empty or invalid payload"},
        )

    is_base64 = event.get("isBase64Encoded", False)

    if not isinstance(is_base64, bool):
        return _response(
            400,
            {"error": "Invalid encoding flag"},
        )

    # Reject obviously oversized input before decoding.
    if len(body) > (MAX_PAYLOAD_BYTES * 2):
        return _response(
            413,
            {"error": "Payload exceeds the size limit"},
        )

    conn = None
    quota_reserved = False

    try:
        # Each invocation owns its connection.
        conn = initialize_database(DB_PATH)

        # Confirm that the authenticated identity has an account.
        quota = verify_user_quota(conn, user_id)

        if quota is None:
            return _response(
                403,
                {"error": "Account is not provisioned"},
            )

        tier, usage, limit = quota

        # Decode and validate the incoming payload.
        try:
            if is_base64:
                raw_bytes = base64.b64decode(
                    body,
                    validate=True,
                )
            else:
                raw_bytes = body.encode("utf-8")
        except (binascii.Error, ValueError, UnicodeEncodeError):
            return _response(
                400,
                {"error": "Invalid payload encoding"},
            )

        if not raw_bytes:
            return _response(
                400,
                {"error": "Empty decoded payload"},
            )

        if len(raw_bytes) > MAX_PAYLOAD_BYTES:
            return _response(
                413,
                {"error": "Payload exceeds the size limit"},
            )

        # Reserve quota atomically before expensive inference.
        # The database function must implement atomic reservation.
        if not increment_user_usage(conn, user_id):
            return _response(
                429,
                {"error": "Monthly usage allowance reached"},
            )

        quota_reserved = True

        # 1G/2G: text preprocessing.
        cleaned_text = execute_low_level_parse(raw_bytes)

        if not cleaned_text:
            _release_quota_reservation(conn, user_id)
            quota_reserved = False

            return _response(
                400,
                {"error": "Payload contains no usable text"},
            )

        # 5G: schema-constrained extraction.
        structured_data = map_unstructured_to_schema(cleaned_text)

        # Re-read quota to report the current account state.
        current_quota = verify_user_quota(conn, user_id)

        if current_quota is None:
            raise RuntimeError("Account disappeared during processing")

        current_tier, current_usage, current_limit = current_quota

        quota_reserved = False

        return _response(
            200,
            {
                "status": "success",
                "account_tier": current_tier,
                "monthly_consumption": current_usage,
                "monthly_allowance": current_limit,
                "payload": structured_data,
            },
        )

    except Exception:
        if conn is not None and quota_reserved:
            try:
                _release_quota_reservation(conn, user_id)
            except Exception:
                # Log this failure privately in production.
                pass

        # Do not expose exception text, credentials or provider details.
        return _response(
            500,
            {"error": "Internal processor error"},
        )

    finally:
        if conn is not None:
            conn.close()
