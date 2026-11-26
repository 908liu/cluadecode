"""Shared helpers: IDs, times, JSON, filenames, text normalization."""
from __future__ import annotations

import json
import re
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def utcnow_iso() -> str:
    return utcnow().isoformat()


def plus_days_iso(days: int, start: datetime | None = None) -> str:
    return ((start or utcnow()) + timedelta(days=days)).isoformat()


def dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def load_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def basename(value: str) -> str:
    return Path((value or "").replace("\\", "/")).name.strip()


def safe_ext(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def format_of(filename: str) -> str | None:
    ext = safe_ext(filename)
    if ext == "pdf":
        return "PDF"
    if ext == "docx":
        return "Word"
    if ext == "md":
        return "Markdown"
    return None


def normalize_name(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    return re.sub(r"[\s\W_]+", "", text).casefold()


def clamp_text(value: str, limit: int) -> str:
    value = (value or "").strip()
    return value if len(value) <= limit else value[:limit].rstrip() + "…"


def extract_json_object(text: str) -> dict[str, Any] | None:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
        return value if isinstance(value, dict) else None
    except ValueError:
        pass
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start >= 0 and end > start:
        try:
            value = json.loads(cleaned[start : end + 1])
            return value if isinstance(value, dict) else None
        except ValueError:
            return None
    return None