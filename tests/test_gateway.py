
import base64
import json

import pytest
from fastapi.testclient import TestClient

import src.app as app_module
from src.database import get_daily_usage, initialize_database


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(app_module, "DB_PATH", db_path)
    monkeypatch.setattr(app_module, "DAILY_REQUEST_LIMIT", 100)

    initialize_database(db_path)
    app_module._request_times.clear()

    with TestClient(app_module.app) as test_client:
        yield test_client, db_path


def encoded(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def fake_extraction(_text: str) -> dict:
    return {
        "document_origin": "Sample supplier",
        "transaction_date": "2026-10-18",
        "calculated_net_value": 45.0,
        "extracted_metadata_array": [
            {"item_name": "Sample item", "unit_cost": 45.0}
        ],
    }


def test_health(client):
    http, _ = client
    response = http.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_empty_input_is_rejected(client):
    http, _ = client
    response = http.post(
        "/api/v1/process-task",
        json={"body": "   ", "isBase64Encoded": False},
    )

    assert response.status_code == 400


def test_invalid_base64_is_rejected(client):
    http, _ = client
    response = http.post(
        "/api/v1/process-task",
        json={"body": "!!!", "isBase64Encoded": True},
    )

    assert response.status_code == 400


def test_successful_extraction(client, monkeypatch):
    http, db_path = client
    monkeypatch.setattr(
        app_module, "map_unstructured_to_schema", fake_extraction
    )

    response = http.post(
        "/api/v1/process-task",
        json={
            "body": encoded("Supplier: Sample supplier\nItem: Sample item"),
            "isBase64Encoded": True,
        },
    )

    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "success"
    assert body["payload"]["document_origin"] == "Sample supplier"
    assert body["payload"]["calculated_net_value"] == 45.0
    assert body["usage"]["daily_used"] == 1
    assert get_daily_usage(db_path)["used"] == 1


def test_provider_failure_releases_reservation(client, monkeypatch):
    http, db_path = client

    def fail(_text):
        raise RuntimeError("simulated provider failure")

    monkeypatch.setattr(app_module, "map_unstructured_to_schema", fail)

    response = http.post(
        "/api/v1/process-task",
        json={"body": encoded("Example document"), "isBase64Encoded": True},
    )

    assert response.status_code == 502

    usage = get_daily_usage(db_path)
    assert usage["used"] == 0
    assert usage["reserved"] == 0


def test_daily_limit_is_enforced(client, monkeypatch):
    http, db_path = client
    monkeypatch.setattr(app_module, "DAILY_REQUEST_LIMIT", 1)
    monkeypatch.setattr(
        app_module, "map_unstructured_to_schema", fake_extraction
    )

    request_body = {
        "body": encoded("Example document"),
        "isBase64Encoded": True,
    }

    first = http.post("/api/v1/process-task", json=request_body)
    second = http.post("/api/v1/process-task", json=request_body)

    assert first.status_code == 200
    assert second.status_code == 429
    assert get_daily_usage(db_path)["used"] == 1


def test_usage_endpoint(client):
    http, _ = client
    response = http.get("/api/v1/usage")

    assert response.status_code == 200
    assert response.json()["daily_used"] == 0


def test_plain_text_payload(client, monkeypatch):
    http, _ = client
    monkeypatch.setattr(
        app_module, "map_unstructured_to_schema", fake_extraction
    )

    response = http.post(
        "/api/v1/process-task",
        json={"body": "Supplier: Example", "isBase64Encoded": False},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "success"
