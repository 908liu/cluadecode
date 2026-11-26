from __future__ import annotations

import time

from tests.conftest import auth_headers


def _run_eval(client, headers, chat_id, threshold=0.3):
    created = client.post(
        "/api/v1/eval/runs", headers=headers,
        json={"dataset_id": "std", "top_k": 4, "threshold": threshold, "judge_service_id": chat_id},
    )
    assert created.status_code == 200, created.text
    run_id = created.json()["data"]["run_id"]
    for _ in range(200):
        run = client.get(f"/api/v1/eval/runs/{run_id}", headers=headers).json()["data"]
        if run["status"] in ("succeeded", "failed"):
            return run_id, run
        time.sleep(0.1)
    raise AssertionError("eval run timeout")


def test_company_dataset_is_seeded(app):
    headers = auth_headers(app)
    items = app.get("/api/v1/eval/datasets", headers=headers).json()["data"]["items"]
    datasets = {item["id"]: item for item in items}
    assert datasets["std"]["case_count"] == 8
    assert datasets["company"]["case_count"] == 12
    assert datasets["company"]["answerable_count"] == 10
    assert datasets["company"]["refusal_count"] == 2


def test_ask_citations_refusal_and_records(ready_document):
    client, headers, _, _ = ready_document
    answer = client.post(
        "/api/v1/qa/ask", headers=headers, json={"question": "上传文档支持哪些格式？"}
    ).json()["data"]
    assert answer["refused"] is False
    assert len(answer["citations"]) == 1
    citation = answer["citations"][0]
    assert citation["download_url"].startswith("/documents/")
    assert "/api/v1" not in citation["download_url"]
    assert "[1]" in answer["answer"]

    refused = client.post(
        "/api/v1/qa/ask", headers=headers, json={"question": "公司年会在哪一天举办？"}
    ).json()["data"]
    assert refused["refused"] is True
    assert refused["citations"] == []
    assert refused["refusal_reason"] == "insufficient_evidence"

    records = client.get("/api/v1/qa/records", headers=headers).json()["data"]
    assert records["total"] == 2
    detail = client.get(f"/api/v1/qa/records/{records['items'][0]['id']}", headers=headers)
    assert detail.status_code == 200
    assert client.delete(f"/api/v1/qa/records/{records['items'][0]['id']}", headers=headers).status_code == 200


def test_eval_human_blind_and_rescore(ready_document):
    client, headers, chat_id, _ = ready_document
    run_id, run = _run_eval(client, headers, chat_id)
    assert run["status"] == "succeeded"
    config = run["config_snapshot"]
    for key in ("judge_service_id", "generation_service_id", "embedding_service_id", "index_version_id"):
        assert isinstance(config[key], str) and config[key] != "null"

    model_cases = client.get(
        f"/api/v1/eval/runs/{run_id}/cases", headers=headers, params={"mode": "model"}
    ).json()["data"]["items"]
    assert model_cases and "model_score" in model_cases[0]
    human_cases = client.get(
        f"/api/v1/eval/runs/{run_id}/cases", headers=headers, params={"mode": "human"}
    ).json()["data"]["items"]
    assert human_cases and "model_score" not in human_cases[0] and "model_reason" not in human_cases[0]
    assert human_cases[0]["human_note"] == ""

    saved = client.put(
        f"/api/v1/eval/runs/{run_id}/cases/{human_cases[0]['case_id']}/human-score",
        headers=headers, json={"score": 5, "note": "ok"},
    )
    assert saved.status_code == 200 and saved.json()["data"]["human_score"] == 5
    metrics = client.get(f"/api/v1/eval/runs/{run_id}/metrics", headers=headers).json()["data"]
    for key in ("total_cases", "recall_at_k", "mrr", "ndcg_at_k", "ndcg_scored", "citation_valid_rate",
                "refusal_accuracy", "model_score_avg", "model_scored", "coverage", "cases_failed",
                "avg_latency_ms", "human_scored", "human_score_avg", "human_model_agreement"):
        assert key in metrics
    assert metrics["human_scored"] == 1
    assert metrics["ndcg_scored"] > 0

    rescored = client.post(
        f"/api/v1/eval/runs/{run_id}/rescore", headers=headers, json={"judge_service_id": chat_id}
    )
    assert rescored.status_code == 200
    for _ in range(200):
        run = client.get(f"/api/v1/eval/runs/{run_id}", headers=headers).json()["data"]
        if run["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.1)
    metrics = client.get(f"/api/v1/eval/runs/{run_id}/metrics", headers=headers).json()["data"]
    assert metrics["human_scored"] == 1
    assert run["rescore_count"] == 1


def test_cross_user_qa_eval_and_search(ready_document):
    client, headers, chat_id, _ = ready_document
    run_id, _ = _run_eval(client, headers, chat_id)
    other = auth_headers(client, "bob")
    assert client.get(f"/api/v1/eval/runs/{run_id}", headers=other).status_code == 404
    assert client.get("/api/v1/qa/records", headers=other).json()["data"]["items"] == []
    debug = client.post("/api/v1/search/debug", headers=other,
                        json={"query": "x", "top_k": 4, "use_rerank": False})
    assert debug.status_code == 503 and debug.json()["code"] == 50302


def test_search_debug_settings_and_rerank(ready_document):
    client, headers, _, _ = ready_document
    debug = client.post(
        "/api/v1/search/debug", headers=headers,
        json={"query": "上传支持哪些格式", "top_k": 4, "use_rerank": False},
    )
    assert debug.status_code == 200
    body = debug.json()["data"]
    assert set(body) == {"keyword", "vector", "fused", "reranked", "final", "rerank"}
    without_service = client.post(
        "/api/v1/search/debug", headers=headers,
        json={"query": "上传支持哪些格式", "top_k": 4, "use_rerank": True},
    )
    assert without_service.status_code == 200
    assert without_service.json()["data"]["rerank"]["error"] == "not_configured"
    settings = client.get("/api/v1/search/settings", headers=headers).json()["data"]
    assert settings["use_rerank"] is False
    updated = client.put(
        "/api/v1/search/settings", headers=headers,
        json={"top_k": 5, "rrf_k": 60, "use_rerank": False, "rerank_top_n": 20},
    ).json()["data"]
    assert updated["top_k"] == 5