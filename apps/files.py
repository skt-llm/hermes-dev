"""파일 목록/상세 라우터.

Playground Core Manager 구조 대응: apps/

  /files         → lib/file_store.list_files(db, group)
  /parsers       → lib/runtime_config.get_config().parser_names()
  /file/detail   → lib/file_store.get_file_detail(db, file_id)
  /file/refresh  → lib/file_store.refresh_file(db, file_id)
  /file/save     → lib/file_store.save_file_settings(db, file_id, ...)
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from lib import file_store
from lib.common import merge_args, read_json_body
from lib.db import get_db
from lib.runtime_config import get_config

router = APIRouter()


@router.api_route("/files", methods=["GET", "POST"])
async def files(request: Request):
    """파일 목록 (그룹 필터)."""
    args = merge_args(dict(request.query_params), await read_json_body(request))
    group = args.get("group") or ""
    rows = await file_store.list_files(get_db(), group)
    return JSONResponse({"files": rows})


@router.api_route("/parsers", methods=["GET", "POST"])
async def parsers(request: Request):
    return JSONResponse({"parsers": get_config().parser_names()})


@router.api_route("/file/detail", methods=["GET", "POST"])
async def file_detail(request: Request):
    """상세보기 (최초 진입 시 파일 실접근 → db 기록)."""
    args = merge_args(dict(request.query_params), await read_json_body(request))
    file_id = args.get("file_id")
    if not file_id:
        return JSONResponse({"detail": "file_id required"}, status_code=400)
    try:
        data = await file_store.get_file_detail(get_db(), file_id)
    except KeyError:
        return JSONResponse({"detail": f"file not found: {file_id}"}, status_code=404)
    return JSONResponse(data)


@router.api_route("/file/refresh", methods=["GET", "POST"])
async def file_refresh(request: Request):
    """갱신하기: 파일 재읽기 → type/fields/meta 업데이트 (설정 보존)."""
    args = merge_args(dict(request.query_params), await read_json_body(request))
    file_id = args.get("file_id")
    if not file_id:
        return JSONResponse({"detail": "file_id required"}, status_code=400)
    try:
        await file_store.refresh_file(get_db(), file_id)
    except KeyError:
        return JSONResponse({"detail": f"file not found: {file_id}"}, status_code=404)
    except FileNotFoundError:
        return JSONResponse({"detail": "file no longer on disk"}, status_code=404)
    data = await file_store.get_file_detail(get_db(), file_id)
    return JSONResponse(
        {"file": data["file"], "fields": data["fields"], "meta": data["meta"]}
    )


class FieldIn(BaseModel):
    field_name: str
    keep: bool = False
    rename_to: Optional[str] = None
    is_tag: bool = False


class DetailSave(BaseModel):
    file_id: str
    file_type: Optional[str] = None
    group_name: Optional[str] = None
    parser: Optional[str] = None
    fields: List[FieldIn] = Field(default_factory=list)


@router.api_route("/file/save", methods=["GET", "POST"])
async def file_save(request: Request):
    """상세보기 저장: 변경된 항목만 db 반영."""
    body = await read_json_body(request)
    payload = DetailSave(
        file_id=body.get("file_id", "")
        or request.query_params.get("file_id", ""),
        file_type=body.get("file_type"),
        group_name=body.get("group_name"),
        parser=body.get("parser"),
        fields=body.get("fields") or [],
    )
    try:
        data = await file_store.save_file_settings(
            get_db(),
            payload.file_id,
            file_type=payload.file_type,
            group_name=payload.group_name,
            parser=payload.parser,
            fields=[f.model_dump() for f in payload.fields],
        )
    except KeyError:
        return JSONResponse({"detail": f"file not found: {payload.file_id}"}, status_code=404)
    return JSONResponse(data)
