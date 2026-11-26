"""Evaluation seeding, runs, judging, rescoring and metrics."""
from __future__ import annotations

import json
import math
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import Settings
from ..core.errors import ApiError, service_missing
from ..core.utils import dump_json, extract_json_object, load_json, new_id, normalize_text, utcnow_iso
from ..db.models import (
    AppSetting,
    EvalCase,
    EvalDataset,
    EvalRun,
    EvalRunCase,
    Task,
)
from . import qa_service, upstream
from .index_cache import IndexCacheRegistry, IndexSnapshot

SEED_DIR = Path(__file__).resolve().parent.parent / "seeds"
SEED_PATH = SEED_DIR / "std_eval_dataset.json"
SEED_PATHS = (
    SEED_PATH,
    SEED_DIR / "company_eval_dataset.json",
    SEED_DIR / "hard_eval_dataset.json",
)


def seed_defaults(db: Session, settings: Settings) -> None:
    defaults = {
        "search.top_k": str(settings.default_top_k),
        "search.rrf_k": str(settings.rrf_k),
        "search.use_rerank": "false",
        "search.rerank_top_n": "20",
        "search.threshold": str(settings.score_threshold),
        "trash.retention_days": str(settings.trash_retention_days),
        "qa.record_retention_days": str(settings.qa_record_retention_days),
    }
    for key, value in defaults.items():
        if db.get(AppSetting, key) is None:
            db.add(AppSetting(key=key, value=value))
    db.commit()


def seed_eval_dataset(db: Session) -> None:
    for path in SEED_PATHS:
        data = json.loads(path.read_text(encoding="utf-8"))
        dataset_id = data["id"]
        if db.get(EvalDataset, dataset_id) is not None:
            continue
        cases = data["cases"]
        answerable = sum(1 for case in cases if not case["expected_refusal"])
        dataset = EvalDataset(
            id=dataset_id, name=data["name"], description=data["description"],
            version=data["version"], is_builtin=True, case_count=len(cases),
            answerable_count=answerable, refusal_count=len(cases) - answerable,
        )
        db.add(dataset)
        db.flush()
        for item in cases:
            case_id = (
                f"case_{item['seq']}"
                if dataset_id == "std"
                else f"{dataset_id}_case_{item['seq']}"
            )
            db.add(EvalCase(
                id=case_id, dataset_id=dataset.id, seq=item["seq"],
                question=item["question"], reference_answer=item["reference_answer"],
                gold_source=item["gold_source"], gold_refs_json=dump_json(item.get("gold_refs", [])),
                expected_refusal=bool(item["expected_refusal"]), tags_json=dump_json(item.get("tags", [])),
                difficulty=item.get("difficulty", "易"),
            ))
        db.commit()


def build_snapshot(
    db: Session, settings: Settings, registry: IndexCacheRegistry, user_id: str,
    dataset: EvalDataset, top_k: int, threshold: float, judge_service_id: str,
    use_rerank: bool = False, rerank_top_n: int = 20,
    retrieval_strategy: str = "hybrid",
) -> dict:
    generation = upstream.effective_service(db, settings, user_id, "chat")
    embedding = upstream.effective_service(db, settings, user_id, "embedding")
    judge = upstream.service_by_id(db, settings, user_id, judge_service_id)
    snapshot = registry.get(db, user_id)
    rerank = None
    if use_rerank:
        if not settings.rerank_enabled:
            raise ApiError(409, 40903, "重排功能未启用", {"type": "rerank"})
        rerank = upstream.effective_service(db, settings, user_id, "rerank")
        if rerank is None or not rerank.enabled:
            raise ApiError(409, 40903, "重排服务不可用", {"type": "rerank"})
    if generation is None:
        raise ApiError(409, 40903, "默认对话服务未配置", {"type": "chat"})
    if embedding is None:
        raise ApiError(503, 50302, "Embedding 服务未配置", {"type": "embedding"})
    if judge is None or judge.type != "chat" or not judge.enabled:
        raise ApiError(409, 40903, "评分服务不可用", {"type": "judge"})
    if not snapshot.has_index:
        raise ApiError(503, 50302, "索引尚未就绪，请先上传并处理文档", {"type": "embedding"})
    if snapshot.model and embedding.model != snapshot.model:
        raise ApiError(503, 50302, "当前默认 Embedding 与索引不一致，请先重建索引", {"type": "embedding"})
    if snapshot.dimensions and embedding.dimensions and embedding.dimensions != snapshot.dimensions:
        raise ApiError(503, 50302, "当前默认 Embedding 维度与索引不一致，请先重建索引", {"type": "embedding"})
    data = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    return {
        "dataset_version": dataset.version, "rubric_version": data.get("rubric_version", "v1"),
        "prompt_version": data.get("prompt_version", "v1"), "top_k": top_k, "threshold": threshold,
        "judge_service_id": judge.id or "", "judge_model": judge.model,
        "generation_service_id": generation.id or f"env:{generation.type}",
        "generation_model": generation.model,
        "embedding_service_id": embedding.id or f"env:{embedding.type}",
        "embedding_model": embedding.model,
        "embedding_dimensions": embedding.dimensions or 0,
        "use_rerank": bool(use_rerank),
        "rerank_top_n": int(rerank_top_n),
        "rerank_service_id": (rerank.id or f"env:{rerank.type}") if rerank else "",
        "rerank_model": rerank.model if rerank else "",
        "retrieval_strategy": retrieval_strategy if retrieval_strategy in ("dense", "hybrid", "hybrid_rerank") else ("hybrid_rerank" if use_rerank else "hybrid"),
        "index_version_id": snapshot.version_id or "",
    }


def _normalized_title(value: str) -> str:
    value = value.rsplit(".", 1)[0] if "." in value else value
    return normalize_text(value)


def _is_relevant(case: EvalCase, chunk: dict) -> bool:
    refs = load_json(case.gold_refs_json, [])
    if not refs and case.gold_source:
        refs = [{"title": case.gold_source}]
    title = _normalized_title(str(chunk.get("document_title", "")))
    page = chunk.get("page")
    paragraph = chunk.get("paragraph")
    text = normalize_text(str(chunk.get("text", "")))
    for ref in refs:
        ref_title = _normalized_title(str(ref.get("title", "")))
        if ref_title and ref_title != title:
            continue
        if ref.get("page") is not None and page is not None and int(ref["page"]) != int(page):
            continue
        if ref.get("paragraph") is not None and paragraph != ref.get("paragraph"):
            continue
        if ref.get("chunk_id") is not None and chunk.get("chunk_id") != ref.get("chunk_id"):
            continue
        if ref.get("contains") is not None and normalize_text(str(ref["contains"])) not in text:
            continue
        return True
    return False


def _recall(case: EvalCase, chunks: list[dict]) -> tuple[bool | None, int | None]:
    if case.expected_refusal:
        return None, None
    for rank, chunk in enumerate(chunks, start=1):
        if _is_relevant(case, chunk):
            return True, rank
    return False, None


def _relevance(case: EvalCase, chunks: list[dict]) -> list[int]:
    if case.expected_refusal:
        return []
    return [1 if _is_relevant(case, chunk) else 0 for chunk in chunks]


def _ndcg(relevances: list[int], k: int) -> float | None:
    if not relevances:
        return None
    gains = relevances[:k]
    if not any(gains):
        return 0.0
    dcg = sum((2**rel - 1) / math.log2(index + 2) for index, rel in enumerate(gains))
    ideal = sorted(relevances, reverse=True)[:k]
    idcg = sum((2**rel - 1) / math.log2(index + 2) for index, rel in enumerate(ideal))
    return dcg / idcg if idcg else 0.0


def _judge(
    judge: upstream.ServiceInfo, case: EvalCase, answer: str, refused: bool, citations: list[dict]
) -> tuple[int | None, str | None, str | None]:
    rubric = (
        "你是严格的评测员。按 1~5 分评价模型回答：5=完全正确且引用充分；4=基本正确、有小遗漏；"
        "3=部分正确；2=明显不足；1=错误、答非所问或编造。"
    )
    if case.expected_refusal:
        rubric += "本题属于不可回答问题：正确拒答=5 分，尝试回答=1 分。"
    system = rubric + ' 只输出 JSON：{"score": 1-5 的整数, "reason": "简短理由"}。'
    snippets = "\n".join(f"[{i}] {c.get('snippet', '')[:300]}" for i, c in enumerate(citations[:8], start=1)) or "（无引用）"
    user = (
        f"问题：{case.question}\n标准答案：{case.reference_answer or '（应当拒答）'}\n"
        f"模型回答：{answer or '（空）'}\n模型是否拒答：{'是' if refused else '否'}\n引用片段：\n{snippets}"
    )
    try:
        reply = upstream.chat_completion(
            judge, [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.0, max_tokens=512,
        )
    except upstream.UpstreamError as exc:
        return None, None, exc.message
    data = extract_json_object(reply)
    if not data:
        return None, None, "评分模型返回格式无法解析"
    try:
        score = int(data.get("score"))
    except (TypeError, ValueError):
        return None, None, "评分模型未返回 1~5 的整数分数"
    if not 1 <= score <= 5:
        return None, None, "评分超出 1~5 范围"
    return score, str(data.get("reason") or ""), None


def run_eval(db: Session, settings: Settings, registry: IndexCacheRegistry, task: Task) -> None:
    payload = load_json(task.payload_json, {})
    run = db.get(EvalRun, payload.get("run_id", ""))
    if run is None or run.user_id != task.user_id:
        return
    run.status = "running"
    run.updated_at = utcnow_iso()
    db.commit()
    snapshot_config = load_json(run.config_snapshot_json, {})
    judge = upstream.service_by_id(db, settings, run.user_id, snapshot_config.get("judge_service_id", ""))
    if judge is None or judge.type != "chat" or not judge.enabled:
        _fail_run(db, run, task, "judge_unavailable", "评分服务不可用", 503)
        return
    generation = upstream.resolve_snapshot_service(
        db, settings, run.user_id, snapshot_config.get("generation_service_id"), "chat"
    ) or upstream.effective_service(db, settings, run.user_id, "chat")
    if generation is None:
        _fail_run(db, run, task, "chat_unavailable", "对话服务不可用", 503)
        return
    index_snapshot = registry.get(db, run.user_id)
    if not index_snapshot.has_index:
        _fail_run(db, run, task, "embedding_unavailable", "索引尚未就绪", 503)
        return
    if run.index_version_id != index_snapshot.version_id:
        run.index_version_id = index_snapshot.version_id
        snapshot_config["index_version_id"] = index_snapshot.version_id or ""
        run.config_snapshot_json = dump_json(snapshot_config)
    cases = db.scalars(
        select(EvalCase).where(EvalCase.dataset_id == run.dataset_id).order_by(EvalCase.seq)
    ).all()
    run.progress_total = len(cases)
    run.progress_done = 0
    db.commit()
    for position, case in enumerate(cases, start=1):
        row = db.get(EvalRunCase, (run.id, case.id))
        if row is None:
            row = EvalRunCase(run_id=run.id, case_id=case.id)
            db.add(row)
        row.question_snapshot = case.question
        try:
            outcome = qa_service.ask(
                db, settings, registry, run.user_id, case.question,
                new_id("req"), top_k=int(snapshot_config.get("top_k", 4)),
                threshold=float(snapshot_config.get("threshold", 0.6)), persist=False,
                chat_service=generation, index_snapshot=index_snapshot,
                use_rerank=bool(snapshot_config.get("use_rerank", False)),
                rerank_top_n=int(snapshot_config.get("rerank_top_n", 20)),
                retrieval_strategy=str(snapshot_config.get("retrieval_strategy", "hybrid")),
            )
            response = outcome.response
            row.answer = response["answer"]
            row.citations_json = dump_json(response["citations"])
            row.refused = bool(response["refused"])
            row.refusal_reason = response["refusal_reason"]
            row.latency_ms = response["latency_ms"]
            row.error = None
            hit, rank = _recall(case, outcome.chunks)
            row.recall_hit = hit
            row.recall_rank = rank
            row.ndcg_score = _ndcg(
                _relevance(case, outcome.chunks), int(snapshot_config.get("top_k", 4))
            )
            if case.expected_refusal:
                row.citation_valid = bool(response["refused"])
                row.citation_total = 0
                row.citation_valid_count = 0
            else:
                row.citation_valid = bool(response["citations"])
                row.citation_total = len(response["citations"])
                row.citation_valid_count = len(response["citations"]) if outcome.citation_valid else 0
            score, reason, error = _judge(
                judge, case, response["answer"], bool(response["refused"]), response["citations"]
            )
            row.model_score = score
            row.model_reason = reason
            row.model_error = error
        except ApiError as exc:
            row.error = exc.message
            row.model_error = exc.message
        except Exception as exc:
            row.error = f"评测执行失败：{exc}"
            row.model_error = row.error
        run.progress_done = position
        run.updated_at = utcnow_iso()
        db.commit()
    run.status = "succeeded"
    run.finished_at = utcnow_iso()
    run.updated_at = utcnow_iso()
    task.status = "succeeded"
    task.progress_done = run.progress_done
    task.progress_total = run.progress_total
    task.finished_at = utcnow_iso()
    db.commit()


def _fail_run(db: Session, run: EvalRun, task: Task, code: str, message: str, status: int) -> None:
    run.status = "failed"
    run.error_code = code
    run.error_message = message
    run.finished_at = utcnow_iso()
    task.status = "failed"
    task.error_code = code
    task.error_message = message
    task.finished_at = utcnow_iso()
    db.commit()


def run_rescore(db: Session, settings: Settings, registry: IndexCacheRegistry, task: Task) -> None:
    payload = load_json(task.payload_json, {})
    run = db.get(EvalRun, payload.get("run_id", ""))
    if run is None or run.user_id != task.user_id:
        return
    snapshot_config = load_json(run.config_snapshot_json, {})
    judge = upstream.service_by_id(db, settings, run.user_id, snapshot_config.get("judge_service_id", ""))
    if judge is None or judge.type != "chat" or not judge.enabled:
        _fail_run(db, run, task, "judge_unavailable", "评分服务不可用", 503)
        return
    run.status = "running"
    run.progress_done = 0
    run.updated_at = utcnow_iso()
    db.commit()
    cases = db.scalars(
        select(EvalCase).where(EvalCase.dataset_id == run.dataset_id).order_by(EvalCase.seq)
    ).all()
    run.progress_total = len(cases)
    db.commit()
    for position, case in enumerate(cases, start=1):
        row = db.get(EvalRunCase, (run.id, case.id))
        if row is not None:
            citations = load_json(row.citations_json, [])
            score, reason, error = _judge(judge, case, row.answer, bool(row.refused), citations)
            row.model_score = score
            row.model_reason = reason
            row.model_error = error
        run.progress_done = position
        run.updated_at = utcnow_iso()
        db.commit()
    run.status = "succeeded"
    run.finished_at = utcnow_iso()
    run.updated_at = utcnow_iso()
    task.status = "succeeded"
    task.progress_done = run.progress_done
    task.progress_total = run.progress_total
    task.finished_at = utcnow_iso()
    db.commit()


def compute_metrics(db: Session, run: EvalRun) -> dict:
    cases = db.scalars(
        select(EvalCase).where(EvalCase.dataset_id == run.dataset_id).order_by(EvalCase.seq)
    ).all()
    rows = {row.case_id: row for row in db.scalars(select(EvalRunCase).where(EvalRunCase.run_id == run.id)).all()}
    total = len(cases)
    recall_denominator = recall_hits = 0
    mrr_sum = 0.0
    citation_total = citation_valid_count = 0
    refusal_correct = 0
    model_scores: list[int] = []
    ndcg_values: list[float] = []
    human_pairs: list[tuple[int | None, int | None]] = []
    latencies: list[int] = []
    cases_failed = 0
    for case in cases:
        row = rows.get(case.id)
        if row is None:
            cases_failed += 1
            continue
        if row.error:
            cases_failed += 1
        if row.latency_ms is not None:
            latencies.append(row.latency_ms)
        if row.model_score is not None:
            model_scores.append(row.model_score)
        if row.ndcg_score is not None:
            ndcg_values.append(row.ndcg_score)
        if row.human_score is not None:
            human_pairs.append((row.human_score, row.model_score))
        if case.expected_refusal:
            if row.refused:
                refusal_correct += 1
        else:
            if row.refused is False:
                refusal_correct += 1
            if row.recall_hit is not None:
                recall_denominator += 1
                if row.recall_hit:
                    recall_hits += 1
                    if row.recall_rank:
                        mrr_sum += 1.0 / row.recall_rank
        citation_total += row.citation_total or 0
        citation_valid_count += row.citation_valid_count or 0
    human_scored = sum(1 for human, _ in human_pairs if human is not None)
    paired = [(human, model) for human, model in human_pairs if model is not None]
    snapshot = load_json(run.config_snapshot_json, {})
    return {
        "total_cases": total,
        "recall_at_k": (recall_hits / recall_denominator) if recall_denominator else None,
        "mrr": (mrr_sum / recall_denominator) if recall_denominator else None,
        "ndcg_at_k": (sum(ndcg_values) / len(ndcg_values)) if ndcg_values else None,
        "ndcg_k": int(snapshot.get("top_k", 4)),
        "ndcg_scored": len(ndcg_values),
        "citation_valid_rate": (citation_valid_count / citation_total) if citation_total else None,
        "refusal_accuracy": (refusal_correct / total) if total else None,
        "model_score_avg": (sum(model_scores) / len(model_scores)) if model_scores else None,
        "model_scored": len(model_scores),
        "coverage": (len(model_scores) / total) if total else 0,
        "cases_failed": cases_failed,
        "avg_latency_ms": (sum(latencies) / len(latencies)) if latencies else None,
        "human_scored": human_scored,
        "human_score_avg": (sum(h for h, _ in human_pairs if h is not None) / human_scored) if human_scored else None,
        "human_model_agreement": (sum(1 for h, m in paired if h == m) / len(paired)) if paired else None,
    }