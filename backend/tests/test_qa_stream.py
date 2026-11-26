from __future__ import annotations

import json

from app.services import upstream


def _events(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in body.strip().split("\n\n"):
        name = ""
        data = None
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
        if name and data is not None:
            events.append((name, data))
    return events


def test_ask_stream_deltas_final_and_record(ready_document):
    client, headers, _, _ = ready_document
    response = client.post(
        "/api/v1/qa/ask/stream",
        headers=headers,
        json={"question": "上传文档支持哪些格式？"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response.text)
    names = [name for name, _ in events]
    assert names[0] == "meta"
    assert names[-1] == "final"
    assert names.count("delta") > 0
    deltas = "".join(data["text"] for name, data in events if name == "delta")
    final = events[-1][1]
    assert deltas == final["answer"]
    assert final["refused"] is False
    assert len(final["citations"]) == 1
    assert final["citations"][0]["download_url"].startswith("/documents/")

    records = client.get("/api/v1/qa/records", headers=headers).json()["data"]
    assert records["total"] == 1
    assert records["items"][0]["question"] == "上传文档支持哪些格式？"


def test_ask_stream_refusal(ready_document):
    client, headers, _, _ = ready_document
    response = client.post(
        "/api/v1/qa/ask/stream",
        headers=headers,
        json={"question": "公司年会在哪一天举办？"},
    )
    events = _events(response.text)
    final = events[-1][1]
    assert events[-1][0] == "final"
    assert final["refused"] is True
    assert final["refusal_reason"] == "insufficient_evidence"
    assert final["citations"] == []


def test_ask_stream_upstream_error_event(ready_document, monkeypatch):
    client, headers, _, _ = ready_document

    def boom(*_args, **_kwargs):
        raise upstream.UpstreamError("server_error", "upstream boom", 500)
        yield ""  # pragma: no cover

    monkeypatch.setattr(upstream, "chat_completion_stream", boom)
    response = client.post(
        "/api/v1/qa/ask/stream",
        headers=headers,
        json={"question": "上传文档支持哪些格式？"},
    )
    events = _events(response.text)
    assert events[-1][0] == "error"
    assert events[-1][1]["code"] == 50201