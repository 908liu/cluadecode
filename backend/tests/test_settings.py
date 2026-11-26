from __future__ import annotations

from tests.conftest import auth_headers, configure_models


def test_default_service_protection_and_changes(app):
    client = app
    headers = auth_headers(client, "alice")
    chat_id = configure_models(client, headers)
    services = client.get("/api/v1/settings/services", headers=headers).json()["data"]["items"]
    embedding = next(item for item in services if item["type"] == "embedding")
    chat = next(item for item in services if item["type"] == "chat")

    assert client.delete(f"/api/v1/settings/services/{chat['id']}", headers=headers).status_code == 409
    disabled = client.patch(f"/api/v1/settings/services/{chat['id']}/enabled", headers=headers, json={"enabled": False})
    assert disabled.status_code == 409
    assert client.delete(f"/api/v1/settings/services/{embedding['id']}", headers=headers).status_code == 409

    extra = client.post(
        "/api/v1/settings/services", headers=headers,
        json={"name": "Chat 2", "type": "chat", "base_url": "http://127.0.0.1:9000/v1",
              "api_key": "abcdefgh", "model": "m", "timeout_seconds": 5, "dimensions": None},
    )
    assert extra.status_code == 200
    assert client.delete(f"/api/v1/settings/services/{extra.json()['data']['id']}", headers=headers).status_code == 200
    assert client.get("/api/v1/settings/changes", headers=headers).json()["data"]["items"]
    other = auth_headers(client, "bob")
    assert client.get("/api/v1/settings/changes", headers=other).json()["data"]["items"] == []
    assert client.get("/api/v1/settings/services", headers=other).json()["data"]["items"] == []