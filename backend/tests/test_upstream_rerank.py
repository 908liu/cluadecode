from __future__ import annotations

import pytest

from app.services import upstream


def _service(protocol: str = "cohere", base_url: str = "http://127.0.0.1:9000/v1") -> upstream.ServiceInfo:
    return upstream.ServiceInfo(
        id="s_rerank", name="rerank", type="rerank", base_url=base_url,
        api_key="sk-test", model="rerank-model", timeout_seconds=20,
        dimensions=None, enabled=True, source="default", protocol=protocol,
    )


def test_cohere_rerank_request_and_parse(monkeypatch):
    captured = {}

    def fake_request(method, url, headers, payload, timeout, **_kwargs):
        captured.update(method=method, url=url, headers=headers, payload=payload, timeout=timeout)
        return {"results": [
            {"index": 1, "relevance_score": 0.2},
            {"index": 0, "relevance_score": 0.9},
        ]}

    monkeypatch.setattr(upstream, "_request", fake_request)
    result = upstream.rerank_texts(_service(), "问题", ["文档一", "文档二"], 2, timeout_cap=5)

    assert result == [(0, 0.9), (1, 0.2)]
    assert captured["url"] == "http://127.0.0.1:9000/v1/rerank"
    assert captured["payload"]["query"] == "问题"
    assert captured["payload"]["top_n"] == 2
    assert captured["payload"]["return_documents"] is False
    assert captured["timeout"] == 5
    assert captured["headers"]["Authorization"] == "Bearer sk-test"


def test_dashscope_rerank_request_and_parse(monkeypatch):
    captured = {}

    def fake_request(method, url, headers, payload, timeout, **_kwargs):
        captured.update(method=method, url=url, headers=headers, payload=payload, timeout=timeout)
        return {"output": {"results": [{"index": 0, "relevance_score": 0.77}]}}

    monkeypatch.setattr(upstream, "_request", fake_request)
    service = _service("dashscope", "https://dashscope.aliyuncs.com")
    result = upstream.rerank_texts(service, "问题", ["文档一"], 1)

    assert result == [(0, 0.77)]
    assert captured["url"] == (
        "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
    )
    assert captured["payload"]["input"] == {"query": "问题", "documents": ["文档一"]}
    assert captured["payload"]["parameters"]["top_n"] == 1


def test_rerank_rejects_malformed_response(monkeypatch):
    monkeypatch.setattr(upstream, "_request", lambda *args, **kwargs: {"results": []})
    with pytest.raises(upstream.UpstreamError) as exc:
        upstream.rerank_texts(_service(), "q", ["a"], 1)
    assert exc.value.error_type == "server_error"


def test_rerank_test_service(monkeypatch):
    monkeypatch.setattr(
        upstream, "_request",
        lambda *args, **kwargs: {"results": [{"index": 0, "relevance_score": 0.8}]},
    )
    result = upstream.test_service(_service())
    assert result["success"] is True
    assert result["status_code"] == 200


def test_rerank_test_service_failure(monkeypatch):
    monkeypatch.setattr(
        upstream, "_request",
        lambda *args, **kwargs: {"results": [{"index": "bad"}]},
    )
    result = upstream.test_service(_service())
    assert result["success"] is False
    assert result["error_type"] == "server_error"