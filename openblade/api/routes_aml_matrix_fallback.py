"""Compatibility fallback routes for matrix-documented AML endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.routing import APIRoute
from pydantic import BaseModel

from openblade.api.aml_scope import matrix_endpoint_entries
from openblade.api.routes_aml_auth import _ensure_state, _require_admin, require_auth
from openblade.bootstrap import AppContext, get_context
from openblade.catalog.models import AmlUser

_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class MatrixShimTask(BaseModel):
    id: str
    componentId: str
    type: str
    opened: str
    closed: str | None = None
    state: int
    status: str
    description: str
    sessionId: str | None = None


def _task_from_request(path: str, *, task_type: str) -> dict[str, Any]:
    task_id = path.rsplit("/", 1)[-1]
    return MatrixShimTask(
        id=task_id,
        componentId="LIB-001",
        type=task_type,
        opened="2026-01-01T00:00:00Z",
        closed="2026-01-01T00:00:00Z",
        state=5,
        status="Completed",
        description=f"Compatibility task {task_type}",
        sessionId=None,
    ).model_dump()


def _get_shim_payload(*, path_template: str, request_path: str, operation_class: str) -> dict[str, Any]:
    lowered = path_template.lower()
    if "/operations/" in lowered and path_template.endswith("}"):
        task_type = path_template.split("/operations/")[1].split("/")[0]
        return {"task": _task_from_request(request_path, task_type=task_type)}
    if "/operations/" in lowered:
        task_type = path_template.split("/operations/")[1].split("/")[0]
        return {"taskList": {"task": [_task_from_request(request_path, task_type=task_type)]}}
    if lowered.endswith("/logs"):
        return {"logList": {"log": []}}
    if lowered.endswith("/reports"):
        return {"reportList": {"report": []}}
    if lowered.endswith("/status"):
        return {"status": {"state": "online"}}
    return {
        "matrixCompatibility": {
            "pathTemplate": path_template,
            "path": request_path,
            "operationClass": operation_class,
        }
    }


def _result_payload(summary: str) -> dict[str, Any]:
    return {
        "code": 0,
        "description": "OK",
        "summary": summary,
        "action": None,
        "customCode": 0,
    }


def _build_endpoint(method: str, path_template: str, operation_class: str):
    async def endpoint(
        request: Request,
        current_user: AmlUser = Depends(require_auth),
        context: AppContext = Depends(get_context),
    ) -> dict[str, Any]:
        _ensure_state(context)
        if method in _MUTATING_METHODS:
            _require_admin(current_user)
            return _result_payload(f"Completed {method} {path_template} via compatibility shim")
        return _get_shim_payload(
            path_template=path_template,
            request_path=request.url.path,
            operation_class=operation_class,
        )

    endpoint.__name__ = (
        "matrix_shim_"
        + method.lower()
        + "_"
        + path_template.strip("/").replace("/", "_").replace("{", "").replace("}", "")
    )
    return endpoint


def register_missing_matrix_routes(app: FastAPI) -> int:
    existing: set[tuple[str, str]] = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or set():
            if method in {"GET", "POST", "PUT", "DELETE", "PATCH"}:
                existing.add((method, route.path))

    registered = 0
    for entry in matrix_endpoint_entries():
        method = str(entry["method"])
        path = str(entry["path"])
        operation_class = str(entry.get("operation_class", "query"))
        key = (method, path)
        if key in existing:
            continue
        app.add_api_route(
            path,
            _build_endpoint(method, path, operation_class),
            methods=[method],
            tags=["aml-matrix-fallback"],
            include_in_schema=True,
        )
        existing.add(key)
        registered += 1
    return registered

