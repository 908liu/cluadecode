from __future__ import annotations

import hashlib
import time
from pathlib import Path

from tests.conftest import auth_headers, configure_models, upload_fixture, wait_document


def _fixture_bytes() -> bytes:
    return (Path(__file__).parent / "fixtures" / "eval-kb" / "product-guide.md").read_bytes()


def _upload(client, headers, data, action="none", existing=None, name="product-guide.md"):
    form = {"conflict_action": action}
    if existing:
        form["existing_document_id"] = existing
    return client.post(
        "/api/v1/documents/upload", headers=headers,
        files={"file": (name, data, "text/markdown")}, data=form,
    )


def test_document_read_and_download(ready_document):
    client, headers, _, document_id = ready_document
    listing = client.get("/api/v1/documents", headers=headers).json()["data"]
    assert listing["total"] == 1
    assert listing["items"][0]["status"] == "available"
    detail = client.get(f"/api/v1/documents/{document_id}", headers=headers).json()["data"]
    assert detail["parse_meta"]["char_count"] > 0
    assert detail["parse_meta"]["parser"] == "markdown"
    chunks = client.get(f"/api/v1/documents/{document_id}/chunks", headers=headers).json()["data"]
    assert chunks["total"] >= 1
    download = client.get(f"/api/v1/documents/{document_id}/download", headers=headers)
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]


def test_conflicts_and_versions(ready_document):
    client, headers, _, document_id = ready_document
    data = _fixture_bytes()
    sha = hashlib.sha256(data).hexdigest()
    preflight = client.post(
        "/api/v1/documents/preflight", headers=headers,
        json={"files": [{"client_id": "f1", "name": "product-guide.md", "size_bytes": len(data), "sha256": sha}]},
    ).json()["data"]["results"][0]
    assert preflight["status"] == "duplicate_and_same_name"
    assert preflight["allowed_actions"] == ["keep_both", "cancel"]

    conflict = _upload(client, headers, data, "none")
    assert conflict.status_code == 409 and conflict.json()["code"] == 40901

    kept = _upload(client, headers, data, "keep_both")
    assert kept.status_code == 200
    assert kept.json()["data"]["filename"] == "product-guide (2).md"
    second = kept.json()["data"]["id"]
    assert wait_document(client, headers, second)["status"] == "available"

    overwritten = _upload(client, headers, data, "overwrite", existing=document_id)
    assert overwritten.status_code == 200
    final = wait_document(client, headers, document_id)
    assert final["status"] == "available"
    detail = client.get(f"/api/v1/documents/{document_id}", headers=headers).json()["data"]
    assert detail["active_version"] == 2

    delete = client.delete(f"/api/v1/documents/{second}", headers=headers)
    assert delete.status_code == 200 and delete.json()["data"]["status"] == "deleted"
    restore = client.post(f"/api/v1/documents/{second}/restore", headers=headers)
    assert restore.status_code == 200
    assert wait_document(client, headers, second)["status"] == "available"
    assert client.delete(f"/api/v1/documents/{second}", headers=headers).status_code == 200
    purge = client.delete(f"/api/v1/documents/{second}/purge", headers=headers)
    assert purge.status_code == 200
    assert client.get(f"/api/v1/documents/{second}", headers=headers).status_code == 404


def test_reindex_and_status(ready_document):
    client, headers, _, document_id = ready_document
    assert client.post(f"/api/v1/documents/{document_id}/reindex", headers=headers).status_code == 200
    assert wait_document(client, headers, document_id)["status"] == "available"
    started = client.post("/api/v1/documents/reindex-all", headers=headers, json={}).json()["data"]
    task_id = started["task_id"]
    for _ in range(100):
        status = client.get(
            "/api/v1/documents/reindex/status", headers=headers, params={"task_id": task_id}
        ).json()["data"]
        if status["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.1)
    assert status["status"] == "succeeded", status
    assert status["status"] in ("queued", "running", "succeeded", "failed")
    invalid = client.post("/api/v1/documents/reindex-all", headers=headers, json={"document_ids": []})
    assert invalid.status_code == 422


def test_cross_user_isolation(ready_document):
    client, headers, _, document_id = ready_document
    other = auth_headers(client, "bob")
    assert client.get(f"/api/v1/documents/{document_id}", headers=other).status_code == 404
    assert client.get(f"/api/v1/documents/{document_id}/download", headers=other).status_code == 404
    assert client.get("/api/v1/documents", headers=other).json()["data"]["items"] == []
    assert client.get("/api/v1/documents/status", headers=other, params={"ids": document_id}).json()["data"]["items"][0]["status"] == "missing"


def test_missing_status_placeholder(ready_document):
    client, headers, _, _ = ready_document
    item = client.get("/api/v1/documents/status", headers=headers, params={"ids": "nope"}).json()["data"]["items"][0]
    assert item["status"] == "missing"
    assert item["chunks"] == 0 and item["progress"] == 0
    assert isinstance(item["updated_at"], str)


def test_upload_empty_and_bad_format(ready_document):
    client, headers, _, _ = ready_document
    empty = client.post(
        "/api/v1/documents/upload", headers=headers,
        files={"file": ("empty.md", b"", "text/markdown")}, data={"conflict_action": "none"},
    )
    assert empty.status_code == 400 and empty.json()["code"] == 40001
    bad = client.post(
        "/api/v1/documents/upload", headers=headers,
        files={"file": ("bad.exe", b"MZ", "application/octet-stream")}, data={"conflict_action": "none"},
    )
    assert bad.status_code == 415 and bad.json()["code"] == 41501


def test_status_cap_and_list_filters(ready_document):
    client, headers, _, _ = ready_document
    too_many = client.get("/api/v1/documents/status", headers=headers,
                          params={"ids": ",".join(f"x{i}" for i in range(51))})
    assert too_many.status_code == 422
    listing = client.get("/api/v1/documents", headers=headers,
                         params={"q": "product", "status": "available", "sort": "title"}).json()["data"]
    assert listing["total"] == 1
    empty = client.get("/api/v1/documents", headers=headers, params={"page": 99}).json()["data"]
    assert empty["items"] == [] and empty["total"] == 1
    assert client.get("/api/v1/documents", headers=headers, params={"sort": "-bad"}).status_code == 422