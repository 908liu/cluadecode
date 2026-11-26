"""Standard success envelope."""
from __future__ import annotations

from typing import Any


def ok(data: Any = None) -> dict:
    return {"code": 0, "message": "ok", "data": data}