from __future__ import annotations

import time

from app.services import upstream
from tests.conftest import configure_rerank, wait_document


def _upload_large_document(client, headers) -> str:
    sections = []
    for index in range(1, 5):
        body = f"第 {index} 章的正文内容。" + "这是用于检索重排测试的段落，包含章节编号。" * 18
        sections.append(f"## 第 {index} 章\n\n{body}")
    content = "\n\n".join(sections)
    response = client.post(
        "/api/v1/documents/upload",
        headers=headers,
        files={"file": ("rerank-guide.md", content.encode("utf-8"), "text/markdown")},
        data={"conflict_action": "none"},
    )
    assert response.status_code == 200, response.text
    document_id = response.json()["data"]["id"]
    item = wait_document(client, headers, document_id)
    assert item["status"] == "available", item
    return document_id


def _enable_rerank(client, headers) -> None:
    response = client.put(
        "/api/v1/search/settings",
        headers=headers,
        json={"top_k": 4, "rrf_k": 60, "use_rerank": True, "rerank_top_n": 20},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["use_rerank"] is True


def test_search_debug_applies_rerank_and_reorders(ready_document):
    client, headers, _, _ = ready_document
    configure_rerank(client, headers)
    _upload_large_document(client, headers)
    _enable_rerank(client, headers)

    debug = client.post(
        "/api/v1/search/debug",
        headers=headers,
        json={"query": "第 2 章 的正文内容", "top_k": 4, "use_rerank": True, "rerank_top_n": 20},
    )
    assert debug.status_code == 200, debug.text
    data = debug.json()["data"]

    assert data["rerank"]["enabled"] is True
    assert data["rerank"]["applied"] is True
    assert data["rerank"]["candidates"] >= 2
    assert len(data["fused"]) >= 2
    assert data["reranked"]
    assert data["reranked"][0]["chunk_id"] != data["fused"][0]["chunk_id"]
    assert data["final"][0]["chunk_id"] == data["reranked"][0]["chunk_id"]
    assert data["final"][0]["rerank_score"] is not None


def test_rerank_failure_falls_back_to_rrf(ready_document, monkeypatch):
    client, headers, _, _ = ready_document
    configure_rerank(client, headers)
    _upload_large_document(client, headers)
    _enable_rerank(client, headers)

    def boom(*_args, **_kwargs):
        raise upstream.UpstreamError("timeout", "rerank timeout")

    monkeypatch.setattr(upstream, "rerank_texts", boom)
    debug = client.post(
        "/api/v1/search/debug",
        headers=headers,
        json={"query": "第 2 章", "top_k": 4, "use_rerank": True},
    )
    data = debug.json()["data"]
    assert data["rerank"]["applied"] is False
    assert data["rerank"]["error"] == "timeout"
    assert [item["chunk_id"] for item in data["final"]] == [
        item["chunk_id"] for item in data["fused"]
    ]


def test_rerank_not_configured_does_not_fail(ready_document):
    client, headers, _, _ = ready_document
    _enable_rerank(client, headers)

    debug = client.post(
        "/api/v1/search/debug",
        headers=headers,
        json={"query": "上传格式", "top_k": 4, "use_rerank": True},
    )
    assert debug.status_code == 200
    data = debug.json()["data"]
    assert data["rerank"]["enabled"] is True
    assert data["rerank"]["applied"] is False
    assert data["rerank"]["error"] == "not_configured"
    assert data["final"]


def test_qa_uses_user_rerank_setting(ready_document, monkeypatch):
    client, headers, _, _ = ready_document
    configure_rerank(client, headers)
    _enable_rerank(client, headers)

    called = {"count": 0}
    original = upstream.rerank_texts

    def wrapper(*args, **kwargs):
        called["count"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(upstream, "rerank_texts", wrapper)
    answer = client.post(
        "/api/v1/qa/ask", headers=headers, json={"question": "上传文档支持哪些格式？"}
    )
    assert answer.status_code == 200, answer.text
    assert called["count"] == 1
    assert answer.json()["data"]["refused"] is False


def test_eval_run_with_rerank_and_ndcg(ready_document):
    client, headers, chat_id, _ = ready_document
    configure_rerank(client, headers)
    created = client.post(
        "/api/v1/eval/runs",
        headers=headers,
        json={
            "dataset_id": "std", "top_k": 4, "threshold": 0.3,
            "judge_service_id": chat_id, "use_rerank": True, "rerank_top_n": 20,
        },
    )
    assert created.status_code == 200, created.text
    run_id = created.json()["data"]["run_id"]
    for _ in range(200):
        run = client.get(f"/api/v1/eval/runs/{run_id}", headers=headers).json()["data"]
        if run["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.1)
    assert run["status"] == "succeeded", run
    assert run["config_snapshot"]["use_rerank"] is True
    assert run["config_snapshot"]["rerank_service_id"]

    metrics = client.get(f"/api/v1/eval/runs/{run_id}/metrics", headers=headers).json()["data"]
    assert "ndcg_at_k" in metrics
    assert metrics["ndcg_scored"] > 0
    cases = client.get(
        f"/api/v1/eval/runs/{run_id}/cases", headers=headers, params={"mode": "model"}
    ).json()["data"]["items"]
    assert any(case["ndcg_score"] is not None for case in cases)