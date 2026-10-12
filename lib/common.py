"""공통 도메인 헬퍼.

Playground Core Manager 구조 대응:
    lib/common.py
    - read_json_body(): GET/POST 공용 핸들러용 body 파싱
    - merge_args(): query + body 병합 (body 우선)
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import Request


async def read_json_body(request: Request) -> Dict[str, Any]:
    """JSON body 읽기 (GET/POST 공용 라우터 지원). 없으면 {}."""
    try:
        body = await request.json()
        return body if isinstance(body, dict) else {}
    except Exception:
        return {}


def merge_args(query: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    """query 기본값 + body 우선 병합 (None 값은 무시)."""
    out = {k: v for k, v in query.items() if v is not None}
    for k, v in body.items():
        if v is not None:
            out[k] = v
    return out


def install_exception_handlers(app) -> None:
    """lib 도메인 함수가 던지는 KeyError/ValueError → 404/400 변환."""
    from fastapi import Request as _Request
    from fastapi.responses import JSONResponse as _JSONResponse

    @app.exception_handler(KeyError)
    async def _key_error(_request: _Request, exc: KeyError):
        return _JSONResponse(status_code=404, content={"detail": str(exc.args[0])})

    @app.exception_handler(ValueError)
    async def _value_error(_request: _Request, exc: ValueError):
        return _JSONResponse(status_code=400, content={"detail": str(exc.args[0])})
