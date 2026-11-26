from __future__ import annotations

from app.services import upstream


def _service(base_url: str) -> upstream.ServiceInfo:
    return upstream.ServiceInfo(
        id="s_1", name="test", type="chat", base_url=base_url, api_key="sk-test",
        model="model-1", timeout_seconds=5, dimensions=None, enabled=True, source="default",
    )


def test_anthropic_chat_uses_messages_api(monkeypatch):
    captured = {}

    def fake_request(method, url, headers, payload, timeout, **_kwargs):
        captured.update(method=method, url=url, headers=headers, payload=payload, timeout=timeout)
        return {"content": [{"type": "text", "text": "pong"}]}

    monkeypatch.setattr(upstream, "_request", fake_request)
    result = upstream.chat_completion(
        _service("https://example.com/apps/anthropic"),
        [{"role": "system", "content": "rules"}, {"role": "user", "content": "ping"}],
        max_tokens=1,
    )
    assert result == "pong"
    assert captured["url"].endswith("/apps/anthropic/v1/messages")
    assert captured["headers"]["x-api-key"] == "sk-test"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert captured["payload"]["system"] == "rules"
    assert captured["payload"]["max_tokens"] == 1


def test_openai_stream_parses_deltas(monkeypatch):
    def fake_stream(*_args, **_kwargs):
        yield {"choices": [{"delta": {"content": "你"}}]}
        yield {"choices": [{"delta": {"content": "好"}}]}

    monkeypatch.setattr(upstream, "_stream_sse", fake_stream)
    tokens = list(upstream.chat_completion_stream(
        _service("https://example.com/v1"), [{"role": "user", "content": "ping"}]
    ))
    assert tokens == ["你", "好"]


def test_anthropic_stream_parses_text_delta(monkeypatch):
    def fake_stream(*_args, **_kwargs):
        yield {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "pong"}}

    monkeypatch.setattr(upstream, "_stream_sse", fake_stream)
    tokens = list(upstream.chat_completion_stream(
        _service("https://example.com/apps/anthropic"), [{"role": "user", "content": "ping"}]
    ))
    assert tokens == ["pong"]


def test_anthropic_embedding_is_unsupported():
    service = _service("https://example.com/apps/anthropic")
    service.type = "embedding"
    try:
        upstream.embed_texts(service, ["x"])
    except upstream.UpstreamError as exc:
        assert exc.error_type == "unsupported"
    else:
        raise AssertionError("expected unsupported error")