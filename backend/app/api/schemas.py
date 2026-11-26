"""Pydantic request models. Response shapes live in api/serializers.py."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

ServiceType = Literal["chat", "embedding", "rerank"]


class RegisterRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=32)
    invite_code: str | None = Field(default=None, max_length=128)

    @field_validator("username")
    @classmethod
    def _trim_username(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("用户名不能为空")
        return value

    @field_validator("display_name")
    @classmethod
    def _trim_display(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("username")
    @classmethod
    def _trim_username(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("用户名不能为空")
        return value


def _validate_base_url(value: str) -> str:
    value = value.strip()
    if not re.match(r"^https?://", value):
        raise ValueError("base_url 必须以 http:// 或 https:// 开头")
    if len(value) > 500:
        raise ValueError("base_url 不能超过 500 个字符")
    if any(char.isspace() for char in value):
        raise ValueError("base_url 不能包含空白字符")
    if value.endswith("/") or "#" in value:
        raise ValueError("base_url 不能以 / 结尾或包含 #")
    return value


class ServiceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    type: ServiceType
    base_url: str
    api_key: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=128)
    timeout_seconds: int = Field(default=20, ge=1, le=120)
    dimensions: int | None = Field(default=None, ge=1, le=8192)
    protocol: Literal["cohere", "dashscope"] | None = None

    @field_validator("name", "api_key", "model")
    @classmethod
    def _trim(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value

    @field_validator("base_url")
    @classmethod
    def _url(cls, value: str) -> str:
        return _validate_base_url(value)

    @model_validator(mode="after")
    def _dimensions_rule(self) -> "ServiceCreate":
        if self.type == "embedding" and self.dimensions is None:
            raise ValueError("Embedding 服务必须填写 dimensions")
        if self.type != "embedding" and self.dimensions is not None:
            raise ValueError("只有 Embedding 服务可以填写 dimensions")
        if self.type != "rerank" and self.protocol is not None:
            raise ValueError("只有 Rerank 服务可以填写 protocol")
        return self


class ServiceUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    type: ServiceType | None = None
    base_url: str
    api_key: str | None = Field(default=None, max_length=500)
    model: str = Field(min_length=1, max_length=128)
    timeout_seconds: int = Field(default=20, ge=1, le=120)
    dimensions: int | None = Field(default=None, ge=1, le=8192)
    protocol: Literal["cohere", "dashscope"] | None = None

    @field_validator("name", "model")
    @classmethod
    def _trim(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value

    @field_validator("api_key")
    @classmethod
    def _trim_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("base_url")
    @classmethod
    def _url(cls, value: str) -> str:
        return _validate_base_url(value)


class EnabledPayload(BaseModel):
    enabled: bool


class PreflightFile(BaseModel):
    client_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")


class PreflightRequest(BaseModel):
    files: list[PreflightFile] = Field(min_length=1, max_length=20)


class ReindexAllRequest(BaseModel):
    document_ids: list[str] | None = None


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)

    @field_validator("question")
    @classmethod
    def _trim(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("问题不能为空")
        return value


class HumanScoreRequest(BaseModel):
    score: int | None = Field(default=None, ge=1, le=5)
    note: str | None = Field(default=None, max_length=2000)


class EvalRunCreate(BaseModel):
    dataset_id: str = Field(min_length=1, max_length=64)
    top_k: int = Field(default=4, ge=1, le=20)
    threshold: float = Field(default=0.6, ge=0, le=1)
    judge_service_id: str = Field(min_length=1, max_length=64)
    use_rerank: bool = False
    rerank_top_n: int = Field(default=20, ge=1, le=100)
    retrieval_strategy: Literal["dense", "hybrid", "hybrid_rerank"] = "hybrid"


class SearchDebugRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=4, ge=1, le=20)
    use_rerank: bool = False
    rerank_top_n: int = Field(default=20, ge=1, le=100)


class SearchSettingsPayload(BaseModel):
    top_k: int = Field(default=4, ge=1, le=20)
    rrf_k: int = Field(default=60, ge=1, le=1000)
    use_rerank: bool = False
    rerank_top_n: int = Field(default=20, ge=1, le=100)