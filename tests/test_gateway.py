
import os
import time

# app.py reads JWT_SECRET during import.
os.environ.setdefault(
    "JWT_SECRET",
    "test-only-secret-that-must-never-be-used-in-production",
)

import jwt
import pytest
from fastapi.testclient import TestClient

from src import app as gateway
from src.database import initialize_database, create_user


@pytest.fixture
def test_context(tmp_path, monkeypatch):
    """Create an isolated database and authenticated test client."""

    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(gateway, "DB_PATH", db_path)

    conn = initialize_database(db_path)
    try:
        create_user(
            conn,
            user_id="usr_free",
            email="free-test@example.com",
            tier="FREE",
            monthly_allowance=15,
        )
        create_user(
            conn,
            user_id="usr_limited",
            email="limited-test@example.com",
            tier="FREE",
            monthly_allowance=1,
        )
    finally:
        conn.close()

    with TestClient(gateway.app) as client:
        yield client, db_path


def make_token(user_id: str) -> str:
    """Generate a short-lived test JWT."""
    return jwt.encode(
        {
            "sub": user_id,
            "exp": int(time.time()) + 300,
        },
        os.environ["JWT_SECRET"],
        algorithm="HS256",
    )


def auth_headers(user_id: str) -> dict:
    return {
        "Authorization": f"Bearer {make_token(user_id)}"
    }


def mock_extraction(monkeypatch):
    """Replace external inference with deterministic test output."""
    monkeypatch.setattr(
        gateway,
        "map_unstructured_to_schema",
        lambda text: {
            "document_origin": "TEST FIXTURE",
            "transaction_date": "",
            "calculated_net_value": 45.0,
            "extracted_metadata_array": [
                {
                    "item_name": "Test transaction",
                    "unit_cost": 45.0,
                }
            ],
        },
    )


def test_gateway_operational_health_route(test_context):
    client, _ = test_context

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert response.json()["service"] == (
        "SolveEase Inbound Arbitrage Scanner"
    )


def test_gateway_empty_payload_rejection(test_context):
    client, _ = test_context

    response = client.post(
        "/api/v1/process-task",
        headers=auth_headers("usr_free"),
        json={
            "body": "   ",
            "isBase64Encoded": False,
        },
    )

    assert response.status_code == 400


def test_gateway_rejects_missing_authentication(test_context):
    client, _ = test_context

    response = client.post(
        "/api/v1/process-task",
        json={"body": "Test transaction"},
    )

    assert response.status_code == 401


def test_gateway_rejects_invalid_token(test_context):
    client, _ = test_context

    response = client.post(
        "/api/v1/process-task",
        headers={"Authorization": "Bearer invalid-token"},
        json={"body": "Test transaction"},
    )

    assert response.status_code == 401


def test_gateway_rejects_unknown_user(test_context):
    client, _ = test_context

    response = client.post(
        "/api/v1/process-task",
        headers=auth_headers("usr_unknown"),
        json={"body": "Test transaction"},
    )

    assert response.status_code == 403


def test_gateway_successful_processing_loop(
    test_context,
    monkeypatch,
):
    client, db_path = test_context
    mock_extraction(monkeypatch)

    response = client.post(
        "/api/v1/process-task",
        headers=auth_headers("usr_free"),
        json={
            "body": (
                "INBOUND STATEMENT: "
                "$45.00 transaction completed successfully."
            ),
            "isBase64Encoded": False,
        },
    )

    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "success"
    assert data["account_tier"] == "FREE"
    assert data["monthly_consumption"] == 1
    assert data["monthly_allowance"] == 15
    assert data["payload"]["calculated_net_value"] == 45.0

    conn = initialize_database(db_path)
    try:
        quota = conn.execute(
            """
            SELECT usage_count
            FROM users
            WHERE user_id = ?
            """,
            ("usr_free",),
        ).fetchone()

        assert quota == (1,)
    finally:
        conn.close()


def test_gateway_rejects_invalid_base64(test_context):
    client, db_path = test_context

    response = client.post(
        "/api/v1/process-task",
        headers=auth_headers("usr_free"),
        json={
            "body": "%%%not-base64%%%",
            "isBase64Encoded": True,
        },
    )

    assert response.status_code == 400

    conn = initialize_database(db_path)
    try:
        usage = conn.execute(
            "SELECT usage_count FROM users WHERE user_id = ?",
            ("usr_free",),
        ).fetchone()[0]

        assert usage == 0
    finally:
        conn.close()


def test_gateway_enforces_monthly_quota(
    test_context,
    monkeypatch,
):
    client, db_path = test_context
    mock_extraction(monkeypatch)

    headers = auth_headers("usr_limited")
    payload = {"body": "Test transaction"}

    first = client.post(
        "/api/v1/process-task",
        headers=headers,
        json=payload,
    )
    second = client.post(
        "/api/v1/process-task",
        headers=headers,
        json=payload,
    )

    assert first.status_code == 200
    assert second.status_code == 429

    conn = initialize_database(db_path)
    try:
        usage = conn.execute(
            "SELECT usage_count FROM users WHERE user_id = ?",
            ("usr_limited",),
        ).fetchone()[0]

        assert usage == 1
    finally:
        conn.close()
