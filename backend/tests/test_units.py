from __future__ import annotations

from app.core.config import Settings
from app.services.pipeline import chunk_blocks
from app.services.qa_service import _citations, _explicit_refusal


def _chunk(chunk_id: str, text: str, document_id: str = "d_1", version: int = 1) -> dict:
    return {
        "chunk_id": chunk_id, "document_id": document_id, "document_title": "doc.md",
        "version": version, "page": None, "paragraph": None, "text": text,
        "uploader": "alice", "snippet": text[:200],
    }


def test_citation_renumbering_and_invalid_removal():
    contexts = [_chunk("c1", "第一段很长的资料内容"), _chunk("c2", "第二段很长的资料内容"), _chunk("c3", "第三段很长的资料内容")]
    rewritten, citations, had_marker, valid = _citations("结论甲[3]，结论乙[1]，重复[3]，越界[9]", contexts)
    assert had_marker and valid
    assert [item["chunk_id"] for item in citations] == ["c3", "c1"]
    assert rewritten.count("[1]") == 2
    assert rewritten.count("[2]") == 1
    assert "[3]" not in rewritten and "[9]" not in rewritten


def test_citation_short_chunk_is_invalid():
    contexts = [_chunk("c1", "短")]
    rewritten, citations, had_marker, valid = _citations("答案[1]", contexts)
    assert had_marker and not valid and citations == []
    assert rewritten.strip() == "答案"


def test_explicit_refusal_with_citations_is_detected():
    assert _explicit_refusal("资料中没有提供任何客户的合同金额信息。[1][2]")
    assert _explicit_refusal("资料中没有关于年会日期的信息，因此依据不足，无法回答。[1]")
    assert not _explicit_refusal(
        "根据资料，文档必须完成解析、切分和索引后才能问答[1]。资料没有进一步说明失败场景。"
    )


def test_chunk_window_and_overlap():
    settings = Settings.from_env()
    settings = settings.__class__(**{**settings.__dict__, "chunk_size": 500, "chunk_overlap": 50})
    text = "甲" * 1200
    chunks = chunk_blocks([{"text": text, "page": 1, "paragraph": None}], settings)
    assert len(chunks) == 3
    assert len(chunks[0]["text"]) == 500
    assert chunks[1]["text"][:50] == chunks[0]["text"][-50:]