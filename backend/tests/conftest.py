from __future__ import annotations

import dataclasses
import hashlib
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.services import upstream


def _vector(text: str) -> list[float]:
    import math

    values = [0.0] * 64
    for char in text:
        values[hashlib.md5(char.encode("utf-8")).digest()[0] % 64] += 1.0
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return [value / norm for value in values]


def fake_embed(_service, texts):
    return [_vector(text) for text in texts]


def fake_chat(_service, messages, *, temperature=0.2, max_tokens=None):
    question = ""
    for message in reversed(messages):
        if message.get("role") == "user":
            question = str(message.get("content", ""))
            break
    if "标准答案" in question:
        return '{"score": 4, "reason": "回答覆盖主要要点"}'
    if "年会" in question or "合同金额" in question:
        return "知识库中没有找到相关依据，无法回答。"
    return "根据资料，上传文档支持 PDF、Word、Markdown 三种格式[1]。"


def fake_chat_stream(_service, messages, *, temperature=0.2, max_tokens=None):
    text = fake_chat(_service, messages, temperature=temperature, max_tokens=max_tokens)
    for start in range(0, len(text), 8):
        yield text[start : start + 8]


def fake_rerank(_service, _query, documents, _top_n, *, timeout_cap=None):
    """Reverse the candidate order so tests can prove rerank was applied."""
    return sorted(
        ((index, float(index + 1)) for index in range(len(documents))),
        key=lambda item: item[1],
        reverse=True,
    )


@pytest.fixture()
def app(tmp_path: Path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    base = Settings.from_env()
    settings = dataclasses.replace(
        base,
        data_dir=data_dir,
        database_path=data_dir / "app.db",
        chroma_dir=data_dir / "chroma",
        encryption_key_path=data_dir / ".encryption_key",
        bcrypt_rounds=4,
        rerank_enabled=True,
    )
    monkeypatch.setattr(upstream, "chat_completion", fake_chat)
    monkeypatch.setattr(upstream, "chat_completion_stream", fake_chat_stream)
    monkeypatch.setattr(upstream, "embed_texts", fake_embed)
    monkeypatch.setattr(upstream, "rerank_texts", fake_rerank)
    application = create_app(settings)
    with TestClient(application) as client:
        yield client


def auth_headers(client: TestClient, username: str = "alice") -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": "password123", "display_name": username.title()},
    )
    assert response.status_code == 200, response.text
    token = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def configure_models(client: TestClient, headers: dict[str, str]) -> str:
    embedding = client.post(
        "/api/v1/settings/services",
        headers=headers,
        json={
            "name": "Fake Embedding", "type": "embedding", "base_url": "http://127.0.0.1:9000/v1",
            "api_key": "fake-key-1234", "model": "fake-embed", "timeout_seconds": 5, "dimensions": 64,
        },
    )
    assert embedding.status_code == 200, embedding.text
    chat = client.post(
        "/api/v1/settings/services",
        headers=headers,
        json={
            "name": "Fake Chat", "type": "chat", "base_url": "http://127.0.0.1:9000/v1",
            "api_key": "fake-key-5678", "model": "fake-chat", "timeout_seconds": 5, "dimensions": None,
        },
    )
    assert chat.status_code == 200, chat.text
    embedding_id = embedding.json()["data"]["id"]
    chat_id = chat.json()["data"]["id"]
    assert client.post(f"/api/v1/settings/services/{embedding_id}/default", headers=headers).status_code == 200
    assert client.post(f"/api/v1/settings/services/{chat_id}/default", headers=headers).status_code == 200
    return chat_id


def configure_rerank(client: TestClient, headers: dict[str, str]) -> str:
    response = client.post(
        "/api/v1/settings/services",
        headers=headers,
        json={
            "name": "Fake Rerank", "type": "rerank", "protocol": "cohere",
            "base_url": "http://127.0.0.1:9000/v1",
            "api_key": "fake-key-rerank", "model": "fake-rerank",
            "timeout_seconds": 5, "dimensions": None,
        },
    )
    assert response.status_code == 200, response.text
    service_id = response.json()["data"]["id"]
    assert client.post(f"/api/v1/settings/services/{service_id}/default", headers=headers).status_code == 200
    return service_id


def upload_fixture(client: TestClient, headers: dict[str, str], filename: str = "product-guide.md") -> str:
    path = Path(__file__).parent / "fixtures" / "eval-kb" / "product-guide.md"
    with open(path, "rb") as handle:
        response = client.post(
            "/api/v1/documents/upload", headers=headers,
            files={"file": (filename, handle, "text/markdown")},
            data={"conflict_action": "none"},
        )
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def wait_document(client: TestClient, headers: dict[str, str], document_id: str, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    item = {}
    while time.time() < deadline:
        item = client.get(
            "/api/v1/documents/status", headers=headers, params={"ids": document_id}
        ).json()["data"]["items"][0]
        if item["status"] in ("available", "failed"):
            return item
        time.sleep(0.1)
    return item


@pytest.fixture()
def ready_document(app, ):
    client = app
    headers = auth_headers(client)
    chat_id = configure_models(client, headers)
    document_id = upload_fixture(client, headers)
    item = wait_document(client, headers, document_id)
    assert item["status"] == "available", item
    return client, headers, chat_id, document_id