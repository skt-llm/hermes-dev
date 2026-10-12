"""설정 라우터 — config reload/override/조회.

Playground Core Manager 구조 대응: apps/configuration.py

  /reload           → lib/runtime_config.reload_config()   (베이스+로컬 override 재로딩)
  /config_override  → lib/runtime_config.apply_override(override)  (런타임 부분 병합)
  /config           → lib/runtime_config.get_config()       (현재 런타임 config 조회)

모든 엔드포인트는 GET/POST 양쪽을 동시에 지원한다.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from lib.common import merge_args, read_json_body
from lib.runtime_config import apply_override, get_config, reload_config

router = APIRouter()


def _config_payload() -> Dict[str, Any]:
    cfg = get_config()
    return {
        "config_path": cfg.config_path,
        "initialdir": cfg.initialdir,
        "db_path": cfg.db_path,
        "parsers": [
            {"name": p.name, "description": p.description} for p in cfg.parsers
        ],
        "raw": cfg.raw,
    }


@router.api_route("/reload", methods=["GET", "POST"])
async def reload(request: Request):
    """GitLab(=파레서 설정 파일)+로컬 override 재로딩."""
    reload_config()
    return JSONResponse({"ok": True, "config": _config_payload()})


@router.api_route("/config_override", methods=["GET", "POST"])
async def config_override(request: Request):
    """런타임 config 부분 병합.
    body가 있으면 body를, 없으면 query 파라미터를 override 로 사용.
    (예: {"files": {"initialdir": "/new/path"}})
    """
    override = await read_json_body(request)
    if not override:
        override = dict(request.query_params)
    apply_override(override)
    return JSONResponse({"ok": True, "config": _config_payload()})


@router.api_route("/config", methods=["GET", "POST"])
async def config(request: Request):
    """현재 런타임 config 조회."""
    return JSONResponse(_config_payload())
