"""轻量查询理解：规则优先，不依赖 LangGraph 或工具循环。"""
from __future__ import annotations

from dataclasses import dataclass
import re


_CHAT_PATTERNS = (
    "你好", "您好", "谢谢", "再见", "早上好", "晚上好",
)


@dataclass(frozen=True)
class QueryPlan:
    intent: str
    need_retrieval: bool
    normalized_question: str


def normalize_question(question: str) -> str:
    text = re.sub(r"\s+", " ", question.strip())
    return text


def analyze_question(question: str) -> QueryPlan:
    text = normalize_question(question)
    compact = re.sub(r"[\s，。！？!?.、]+", "", text)
    chat_forms = {re.sub(r"[\s，。！？!?.、]+", "", item) for item in _CHAT_PATTERNS}
    if compact in chat_forms:
        return QueryPlan(intent="chat", need_retrieval=False, normalized_question=text)
    if not compact:
        return QueryPlan(intent="rag", need_retrieval=True, normalized_question=text)
    return QueryPlan(intent="rag", need_retrieval=True, normalized_question=text)
