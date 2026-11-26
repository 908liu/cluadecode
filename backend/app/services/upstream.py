"""OpenAI-compatible service resolution and HTTP calls."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from collections.abc import Iterator

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import Settings
from ..core.net import OutboundUrlError, check_outbound_url
from ..core.security import decrypt_key
from ..db.models import ApiService


@dataclass
class ServiceInfo:
    id: str | None
    name: str
    type: str
    base_url: str
    api_key: str
    model: str
    timeout_seconds: int
    dimensions: int | None
    enabled: bool
    source: str  # "default" | "env"
    protocol: str = "cohere"  # only meaningful for rerank
    allow_private_hosts: bool = True
    allowed_hosts: tuple[str, ...] = ()


class UpstreamError(Exception):
    def __init__(self, error_type: str, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.status_code = status_code


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def env_service(settings: Settings, service_type: str) -> ServiceInfo | None:
    prefix = {"chat": "CHAT", "embedding": "EMBEDDING", "rerank": "RERANK"}[service_type]
    base_url = _env(f"{prefix}_BASE_URL").rstrip("/")
    api_key = _env(f"{prefix}_API_KEY")
    model = _env(f"{prefix}_MODEL")
    if not base_url or not api_key or not model:
        return None
    dimensions = None
    if service_type == "embedding":
        raw = _env("EMBEDDING_DIM")
        if not raw.isdigit():
            return None
        dimensions = int(raw)
    timeout = int(_env(f"{prefix}_TIMEOUT") or (30 if service_type == "embedding" else 20))
    if timeout < 1:
        timeout = 20
    protocol = (_env("RERANK_PROTOCOL") or "cohere") if service_type == "rerank" else "cohere"
    return ServiceInfo(
        None, f"环境变量 · {service_type}", service_type, base_url, api_key, model,
        timeout, dimensions, True, "env", protocol,
        allow_private_hosts=not settings.block_private_upstreams,
        allowed_hosts=settings.model_host_allowlist,
    )


def _db_service(settings: Settings, row: ApiService) -> ServiceInfo:
    return ServiceInfo(
        row.id, row.name, row.type, row.base_url.rstrip("/"), decrypt_key(settings, row.api_key_cipher),
        row.model, row.timeout_seconds, row.dimensions, bool(row.enabled), "default",
        getattr(row, "protocol", "cohere") or "cohere",
        allow_private_hosts=not settings.block_private_upstreams,
        allowed_hosts=settings.model_host_allowlist,
    )


def db_default_service(db: Session, settings: Settings, user_id: str, service_type: str) -> ServiceInfo | None:
    row = db.scalar(
        select(ApiService).where(
            ApiService.user_id == user_id,
            ApiService.type == service_type,
            ApiService.is_default.is_(True),
            ApiService.enabled.is_(True),
        )
    )
    return _db_service(settings, row) if row else None


def effective_service(db: Session, settings: Settings, user_id: str, service_type: str) -> ServiceInfo | None:
    return db_default_service(db, settings, user_id, service_type) or env_service(settings, service_type)


def service_by_id(db: Session, settings: Settings, user_id: str, service_id: str) -> ServiceInfo | None:
    row = db.get(ApiService, service_id)
    if row is None or row.user_id != user_id:
        return None
    return _db_service(settings, row)


def snapshot_id(service: ServiceInfo) -> str:
    return service.id if service.id else f"env:{service.type}"


def resolve_snapshot_service(
    db: Session, settings: Settings, user_id: str, snapshot: str | None, service_type: str
) -> ServiceInfo | None:
    if not snapshot:
        return None
    if snapshot.startswith("env:"):
        return env_service(settings, service_type)
    return service_by_id(db, settings, user_id, snapshot)


def _map_status(status: int) -> str:
    if status in (401, 403):
        return "auth"
    if status == 404:
        return "not_found"
    if status == 429:
        return "rate_limit"
    if status >= 500:
        return "server_error"
    return "unknown"


def _guard_url(url: str, allow_private: bool, allowed_hosts: tuple[str, ...]) -> None:
    try:
        check_outbound_url(url, allow_private=allow_private, allowed_hosts=allowed_hosts)
    except OutboundUrlError as exc:
        raise UpstreamError("blocked", str(exc)) from exc


def _request(
    method: str,
    url: str,
    headers: dict,
    payload: dict,
    timeout: int,
    *,
    allow_private: bool = True,
    allowed_hosts: tuple[str, ...] = (),
) -> dict:
    _guard_url(url, allow_private, allowed_hosts)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            response = client.request(method, url, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        raise UpstreamError("timeout", "模型服务响应超时") from exc
    except httpx.HTTPError as exc:
        raise UpstreamError("network", f"模型服务不可达：{exc}") from exc
    if response.status_code >= 400:
        raise UpstreamError(
            _map_status(response.status_code),
            f"上游服务返回 {response.status_code}: {response.text[:300]}",
            response.status_code,
        )
    try:
        return response.json()
    except ValueError as exc:
        raise UpstreamError("server_error", "上游返回的结果不是 JSON") from exc


def _is_anthropic(service: ServiceInfo) -> bool:
    return "/anthropic" in service.base_url.lower()


def _anthropic_messages(messages: list[dict]) -> tuple[str | None, list[dict]]:
    system_parts = [str(item.get("content", "")) for item in messages if item.get("role") == "system"]
    converted = [
        {"role": item.get("role", "user"), "content": str(item.get("content", ""))}
        for item in messages
        if item.get("role") in ("user", "assistant")
    ]
    if not converted:
        converted = [{"role": "user", "content": "ping"}]
    return ("\n".join(system_parts) if system_parts else None), converted


def chat_completion(
    service: ServiceInfo,
    messages: list[dict],
    *,
    temperature: float = 0.2,
    max_tokens: int | None = None,
) -> str:
    if _is_anthropic(service):
        system, converted = _anthropic_messages(messages)
        payload: dict = {
            "model": service.model,
            "max_tokens": max_tokens or 1024,
            "messages": converted,
            "temperature": temperature,
        }
        if system:
            payload["system"] = system
        url = f"{service.base_url}/messages" if service.base_url.endswith("/v1") else f"{service.base_url}/v1/messages"
        headers = {
            "x-api-key": service.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        data = _request(
            "POST", url, headers, payload, service.timeout_seconds,
            allow_private=service.allow_private_hosts, allowed_hosts=service.allowed_hosts,
        )
        try:
            blocks = data.get("content") or []
            text = "".join(str(block.get("text", "")) for block in blocks if block.get("type") == "text")
        except (AttributeError, TypeError) as exc:
            raise UpstreamError("server_error", "上游返回结构不符合 Anthropic 兼容格式") from exc
        if not text:
            raise UpstreamError("server_error", "上游没有返回文本内容")
        return text.strip()
    payload = {"model": service.model, "messages": messages, "temperature": temperature, "stream": False}
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    headers = {"Authorization": f"Bearer {service.api_key}", "Content-Type": "application/json"}
    data = _request(
        "POST", f"{service.base_url}/chat/completions", headers, payload, service.timeout_seconds,
        allow_private=service.allow_private_hosts, allowed_hosts=service.allowed_hosts,
    )
    try:
        return str(data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise UpstreamError("server_error", "上游返回结构不符合 OpenAI 兼容格式") from exc


def _anthropic_stream(
    service: ServiceInfo, messages: list[dict], temperature: float = 0.2, max_tokens: int | None = None
) -> Iterator[str]:
    system, converted = _anthropic_messages(messages)
    payload: dict = {
        "model": service.model,
        "max_tokens": max_tokens or 1024,
        "messages": converted,
        "temperature": temperature,
    }
    if system:
        payload["system"] = system
    url = f"{service.base_url}/messages" if service.base_url.endswith("/v1") else f"{service.base_url}/v1/messages"
    headers = {
        "x-api-key": service.api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    for item in _stream_sse(
        "POST", url, headers, payload, service.timeout_seconds,
        allow_private=service.allow_private_hosts, allowed_hosts=service.allowed_hosts,
    ):
        if isinstance(item, str):
            yield item
            continue
        if isinstance(item, dict):
            delta = item.get("delta") or {}
            text = delta.get("text")
            if isinstance(text, str) and text:
                yield text


def chat_completion_stream(
    service: ServiceInfo,
    messages: list[dict],
    *,
    temperature: float = 0.2,
    max_tokens: int | None = None,
) -> Iterator[str]:
    if _is_anthropic(service):
        yield from _anthropic_stream(service, messages, temperature, max_tokens)
    else:
        yield from _openai_stream(service, messages, temperature, max_tokens)


def embed_texts(service: ServiceInfo, texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    if _is_anthropic(service):
        raise UpstreamError(
            "unsupported",
            "Anthropic 兼容端点不提供 Embedding，请单独配置 OpenAI 兼容的 Embedding 服务",
        )
    headers = {"Authorization": f"Bearer {service.api_key}", "Content-Type": "application/json"}
    data = _request(
        "POST", f"{service.base_url}/embeddings", headers,
        {"model": service.model, "input": texts}, service.timeout_seconds,
        allow_private=service.allow_private_hosts, allowed_hosts=service.allowed_hosts,
    )
    try:
        items = sorted(data["data"], key=lambda item: item.get("index", 0))
        vectors = [list(map(float, item["embedding"])) for item in items]
    except (KeyError, TypeError, ValueError) as exc:
        raise UpstreamError("server_error", "上游返回的 Embedding 结构不正确") from exc
    if service.dimensions is not None:
        for vector in vectors:
            if len(vector) != service.dimensions:
                raise UpstreamError(
                    "dimension_mismatch",
                    f"Embedding 维度不一致：配置 {service.dimensions}，实际 {len(vector)}",
                )
    if len(vectors) != len(texts):
        raise UpstreamError("server_error", "Embedding 返回数量不一致")
    return vectors


def rerank_texts(
    service: ServiceInfo,
    query: str,
    documents: list[str],
    top_n: int,
    *,
    timeout_cap: int | None = None,
) -> list[tuple[int, float]]:
    """Call a rerank endpoint and return (document_index, score) sorted by score."""
    if not documents:
        return []
    top_n = max(1, min(top_n, len(documents)))
    base = service.base_url.rstrip("/")
    if service.protocol == "dashscope":
        suffix = "/api/v1/services/rerank/text-rerank/text-rerank"
        url = base if base.endswith(suffix) else f"{base}{suffix}"
        payload = {
            "model": service.model,
            "input": {"query": query, "documents": documents},
            "parameters": {"return_documents": False, "top_n": top_n},
        }
    else:
        url = f"{base}/rerank"
        payload = {
            "model": service.model,
            "query": query,
            "documents": documents,
            "top_n": top_n,
            "return_documents": False,
        }
    headers = {"Authorization": f"Bearer {service.api_key}", "Content-Type": "application/json"}
    timeout = service.timeout_seconds
    if timeout_cap is not None:
        timeout = min(timeout, max(1, timeout_cap))
    data = _request(
        "POST", url, headers, payload, timeout,
        allow_private=service.allow_private_hosts, allowed_hosts=service.allowed_hosts,
    )
    return _parse_rerank_results(data)


def _parse_rerank_results(data: dict) -> list[tuple[int, float]]:
    results = data.get("results") if isinstance(data, dict) else None
    if results is None and isinstance(data, dict):
        output = data.get("output")
        results = output.get("results") if isinstance(output, dict) else None
    if not isinstance(results, list) or not results:
        raise UpstreamError("server_error", "重排服务返回结构不符合预期")
    best: dict[int, float] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item["index"])
            score = float(item.get("relevance_score", item.get("score")))
        except (KeyError, TypeError, ValueError):
            continue
        if index not in best or score > best[index]:
            best[index] = score
    if not best:
        raise UpstreamError("server_error", "重排服务没有返回有效结果")
    return sorted(best.items(), key=lambda pair: pair[1], reverse=True)


def test_service(service: ServiceInfo) -> dict:
    started = time.perf_counter()
    try:
        if service.type == "chat":
            chat_completion(service, [{"role": "user", "content": "ping"}], max_tokens=1)
        elif service.type == "rerank":
            rerank_texts(service, "ping", ["ping", "pong"], 1)
        else:
            embed_texts(service, ["ping"])
        return {"success": True, "latency_ms": int((time.perf_counter() - started) * 1000),
                "status_code": 200, "error_type": None, "message": "连接成功"}
    except UpstreamError as exc:
        return {"success": False, "latency_ms": int((time.perf_counter() - started) * 1000),
                "status_code": exc.status_code, "error_type": exc.error_type, "message": exc.message}


def _openai_stream(
    service: ServiceInfo, messages: list[dict], temperature: float, max_tokens: int | None
) -> Iterator[str]:
    payload: dict = {
        "model": service.model,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    headers = {"Authorization": f"Bearer {service.api_key}", "Content-Type": "application/json"}
    url = f"{service.base_url}/chat/completions"
    for item in _stream_sse(
        "POST", url, headers, payload, service.timeout_seconds,
        allow_private=service.allow_private_hosts, allowed_hosts=service.allowed_hosts,
    ):
        if isinstance(item, str):
            yield item
            continue
        if isinstance(item, dict):
            choices = item.get("choices") or []
            if choices:
                delta = choices[0].get("delta") or {}
                text = delta.get("content")
                if isinstance(text, str) and text:
                    yield text


def _stream_sse(
    method: str,
    url: str,
    headers: dict,
    payload: dict,
    timeout: int,
    *,
    allow_private: bool = True,
    allowed_hosts: tuple[str, ...] = (),
) -> Iterator[str]:
    _guard_url(url, allow_private, allowed_hosts)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            with client.stream(method, url, headers=headers, json=payload) as response:
                if response.status_code != 200:
                    raise UpstreamError(
                        _map_status(response.status_code),
                        f"上游服务返回 {response.status_code}: {response.text[:300]}",
                        response.status_code,
                    )
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data_text = line[5:].strip()
                    if data_text == "[DONE]":
                        break
                    try:
                        data = json.loads(data_text)
                    except json.JSONDecodeError:
                        continue
                    choices = data.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    text = delta.get("content")
                    if isinstance(text, str) and text:
                        yield text
    except UpstreamError:
        raise
    except httpx.TimeoutException as exc:
        raise UpstreamError("timeout", "模型服务响应超时") from exc
    except httpx.HTTPError as exc:
        raise UpstreamError("server_error", f"上游连接失败：{exc}") from exc