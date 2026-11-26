"""Business exceptions and FastAPI error handlers."""
from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, status: int, code: int, message: str, detail: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.detail = detail


def not_found(resource: str, resource_id: str) -> ApiError:
    return ApiError(404, 40401, "资源不存在", {"resource": resource, "id": resource_id})


def unauthorized(message: str = "登录已失效，请重新登录") -> ApiError:
    return ApiError(401, 40101, message, None)


def validation_error(fields: list[dict[str, str]]) -> ApiError:
    return ApiError(422, 42201, "参数校验失败", {"fields": fields})


def state_error(document_id: str, status: str, action: str) -> ApiError:
    return ApiError(409, 40906, "当前状态不允许该操作", {"document_id": document_id, "status": status, "action": action})


def service_missing(service_type: str) -> ApiError:
    code = 50301 if service_type == "chat" else 50302
    return ApiError(503, code, f"{service_type} 服务未配置", {"type": service_type})


_VALIDATION_MESSAGES = {
    "missing": "必填",
    "string_too_short": "长度不足",
    "string_too_long": "长度超限",
    "string_pattern_mismatch": "格式不正确",
    "int_parsing": "必须是整数",
    "int_type": "必须是整数",
    "float_parsing": "必须是数字",
    "float_type": "必须是数字",
    "bool_parsing": "必须是布尔值",
    "bool_type": "必须是布尔值",
    "list_type": "必须是数组",
    "literal_error": "取值不合法",
    "json_invalid": "请求体不是合法 JSON",
    "model_attributes_type": "请求体格式不正确",
}


def _validation_message(item: dict) -> str:
    error_type = str(item.get("type", ""))
    ctx = item.get("ctx") or {}
    if error_type == "string_too_short" and "min_length" in ctx:
        return f"至少需要 {ctx['min_length']} 个字符"
    if error_type == "string_too_long" and "max_length" in ctx:
        return f"不能超过 {ctx['max_length']} 个字符"
    if error_type == "greater_than_equal" and "ge" in ctx:
        return f"不能小于 {ctx['ge']}"
    if error_type == "less_than_equal" and "le" in ctx:
        return f"不能大于 {ctx['le']}"
    message = str(item.get("msg", "参数错误"))
    return _VALIDATION_MESSAGES.get(error_type, message.removeprefix("Value error, "))


async def api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"code": exc.code, "message": exc.message, "detail": exc.detail})


async def validation_exception_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    fields: list[dict[str, str]] = []
    for item in exc.errors():
        loc = [part for part in item.get("loc", []) if part not in ("body", "query", "path")]
        fields.append({"field": ".".join(str(part) for part in loc) or "request", "message": _validation_message(item)})
    return JSONResponse(status_code=422, content={"code": 42201, "message": "参数校验失败", "detail": {"fields": fields}})


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "")
    print(f"[error] request_id={request_id} path={request.url.path} error={exc!r}")
    return JSONResponse(status_code=500, content={"code": 50001, "message": "服务内部错误", "detail": {"request_id": request_id}})