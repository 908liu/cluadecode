from __future__ import annotations

from tests.conftest import auth_headers


def test_open_registration_and_case_insensitive_duplicate(app):
    client = app
    headers = auth_headers(client, "Alice")
    assert headers["Authorization"].startswith("Bearer ")
    me = client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["data"]["username"] == "Alice"

    duplicate = client.post(
        "/api/v1/auth/register",
        json={"username": "alice", "password": "password123"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == 40908

    bob = auth_headers(client, "Bob")
    assert bob["Authorization"] != headers["Authorization"]
    assert client.get("/api/v1/settings/services", headers=bob).json()["data"]["items"] == []


def test_login_failure_message_and_unauthorized_json(app):
    client = app
    auth_headers(client, "alice")
    bad = client.post("/api/v1/auth/login", json={"username": "alice", "password": "wrong"})
    assert bad.status_code == 401
    assert bad.json()["code"] == 40101
    assert "用户名或密码错误" in bad.json()["message"]

    missing = client.get("/api/v1/documents")
    assert missing.status_code == 401
    body = missing.json()
    assert body["code"] == 40101 and "detail" in body


def test_status_shape_and_user_scope(app):
    client = app
    headers = auth_headers(client, "alice")
    status = client.get("/api/v1/settings/status", headers=headers).json()["data"]
    assert status["available_documents"] == 0
    assert status["types"]["chat"]["env"] == {"configured": False, "service": None}
    assert set(status["types"]) == {"chat", "embedding", "rerank"}

    other = auth_headers(client, "bob")
    assert client.get("/api/v1/settings/status", headers=other).json()["data"]["available_documents"] == 0