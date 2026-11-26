from __future__ import annotations

from tests.conftest import auth_headers, configure_models, ready_document, upload_fixture, wait_document


def _business_methods(client) -> set[tuple[str, str]]:
    spec = client.app.openapi()
    methods: set[tuple[str, str]] = set()
    for path, operations in spec["paths"].items():
        if not path.startswith("/api/v1"):
            continue
        if path == "/api/v1/health":
            continue
        for method in operations:
            if method.lower() in {"get", "post", "put", "patch", "delete"}:
                methods.add((method.upper(), path))
    return methods


def test_openapi_business_endpoint_count(app):
    methods = _business_methods(app)
    assert len(methods) == 44, sorted(methods)
    assert ("GET", "/api/v1/qa/ask") not in methods
    assert ("POST", "/api/v1/qa/ask") in methods
    assert ("POST", "/api/v1/qa/ask/stream") in methods
    assert ("POST", "/api/v1/agent/chat") not in methods
    assert ("POST", "/api/v1/agent/chat/stream") not in methods
    assert ("GET", "/api/v1/documents/status") in methods
    assert ("DELETE", "/api/v1/eval/runs/{run_id}/cases/{case_id}/human-score") not in methods
    assert ("PUT", "/api/v1/eval/runs/{run_id}/cases/{case_id}/human-score") in methods


def test_error_and_status_shapes(app):
    client = app
    headers = auth_headers(client, "alice")
    status = client.get("/api/v1/settings/status", headers=headers)
    data = status.json()["data"]
    assert set(data) == {"ready", "available_documents", "reindex_required", "types"}
    for key in ("chat", "embedding", "rerank"):
        assert set(data["types"][key]) == {"configured", "source", "service", "env"}

    default_service = client.post(
        "/api/v1/settings/services", headers=headers,
        json={"name": "Chat", "type": "chat", "base_url": "http://127.0.0.1:9000/v1",
              "api_key": "abcdefgh", "model": "m", "timeout_seconds": 5, "dimensions": None},
    )
    assert default_service.status_code == 200


def test_document_and_ask_contract(ready_document):
    client, headers, _, document_id = ready_document
    summary = client.get(f"/api/v1/documents/{document_id}", headers=headers).json()["data"]
    for key in ("id", "title", "filename", "format", "size_bytes", "uploader", "status", "chunks",
                "error", "error_code", "active_version", "reindex_required", "progress",
                "queue_position", "deleted_at", "purge_at", "created_at", "updated_at", "parse_meta"):
        assert key in summary
    answer = client.post("/api/v1/qa/ask", headers=headers, json={"question": "上传支持哪些格式？"}).json()["data"]
    assert set(answer) == {"request_id", "answer", "citations", "refused", "refusal_reason", "model", "latency_ms"}
    assert answer["citations"][0]["download_url"].startswith("/documents/")


def test_api_404_and_spa_fallback(app):
    client = app
    missing = client.get("/api/v1/unknown")
    assert missing.status_code == 404
    assert missing.json()["code"] == 40401
    root = client.get("/")
    assert root.status_code == 200
    assert root.headers["content-type"].startswith("text/html") or root.headers["content-type"].startswith("application/json")