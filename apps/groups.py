"""그룹 라우터 — 그룹명 변경 / regex 일괄 지정 / 그룹 상세 / 그룹 합치기.

Playground Core Manager 구조 대응: apps/

  /groups               → db.distinct_groups()
  /group/rename         → lib/file_store.rename_group(db, group_name, new_group_name)
  /group/assign         → lib/file_store.assign_group_regex(db, regex, group_name, require_same_fields)
  /group/detail         → lib/file_store.get_group_detail(db, group)
  /group/base           → lib/file_store.set_group_base(db, group_name, base_file)
  /groups/merge-preview → lib/file_store.merge_preview(db, from_group, to_group)
  /groups/merge         → lib/file_store.merge_groups(db, from_group, to_group, base_file)
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from lib import file_store
from lib.common import merge_args, read_json_body
from lib.db import get_db

router = APIRouter()


@router.api_route("/groups", methods=["GET", "POST"])
async def groups(request: Request):
    """그룹 목록."""
    args = merge_args(dict(request.query_params), await read_json_body(request))
    if "group" in args:  # group_filter 로 필터링된 목록도 지원
        rows = await file_store.list_files(get_db(), args.get("group") or "")
        return JSONResponse({"groups": sorted({r["group_name"] for r in rows if r["group_name"]})})
    return JSONResponse({"groups": await get_db().distinct_groups()})


class GroupRename(BaseModel):
    group_name: str
    new_group_name: str


@router.api_route("/group/rename", methods=["GET", "POST"])
async def group_rename(request: Request):
    """그룹명 변경 (해당 그룹의 모든 파일 일괄)."""
    body = await read_json_body(request)
    payload = GroupRename(
        group_name=body.get("group_name") or request.query_params.get("group_name", ""),
        new_group_name=body.get("new_group_name") or request.query_params.get("new_group_name", ""),
    )
    if not payload.new_group_name.strip():
        return JSONResponse({"detail": "new group name required"}, status_code=400)
    try:
        renamed = await file_store.rename_group(
            get_db(), payload.group_name, payload.new_group_name
        )
    except KeyError:
        return JSONResponse({"detail": f"group not found: {payload.group_name}"}, status_code=404)
    return JSONResponse({"renamed": renamed, "ok": True})


class RegexAssign(BaseModel):
    regex: str
    group_name: str
    require_same_fields: bool = False


@router.api_route("/group/assign", methods=["GET", "POST"])
async def group_assign(request: Request):
    """Regex 기반 일괄 그룹 지정 (+ 동일 필드만 옵션)."""
    body = await read_json_body(request)
    payload = RegexAssign(
        regex=body.get("regex") or request.query_params.get("regex", ""),
        group_name=body.get("group_name") or request.query_params.get("group_name", ""),
        require_same_fields=bool(
            body.get("require_same_fields")
            or request.query_params.get("require_same_fields") == "1"
        ),
    )
    try:
        data = await file_store.assign_group_regex(
            get_db(), payload.regex, payload.group_name, payload.require_same_fields
        )
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)
    return JSONResponse(data)


@router.api_route("/group/detail", methods=["GET", "POST"])
async def group_detail(request: Request):
    """그룹 상세 (그룹 중심 상세보기 페이로드)."""
    args = merge_args(dict(request.query_params), await read_json_body(request))
    group = args.get("group") or args.get("group_name")
    if not group:
        return JSONResponse({"detail": "group required"}, status_code=400)
    try:
        data = await file_store.get_group_detail(get_db(), group)
    except KeyError:
        return JSONResponse({"detail": f"group not found: {group}"}, status_code=404)
    return JSONResponse(data)


class BaseSet(BaseModel):
    group_name: str
    base_file: Optional[str] = None


@router.api_route("/group/base", methods=["GET", "POST"])
async def group_base(request: Request):
    """그룹 기준 파일 지정."""
    body = await read_json_body(request)
    payload = BaseSet(
        group_name=body.get("group_name") or request.query_params.get("group_name", ""),
        base_file=body.get("base_file"),
    )
    try:
        await file_store.set_group_base(get_db(), payload.group_name, payload.base_file)
    except KeyError:
        return JSONResponse({"detail": f"group not found: {payload.group_name}"}, status_code=404)
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)
    return JSONResponse({"ok": True, "base_file": payload.base_file})


@router.api_route("/groups/merge-preview", methods=["GET", "POST"])
async def groups_merge_preview(request: Request):
    """그룹 합치기 미리보기 (필드 구조 비교 + 기준 파일 후보)."""
    args = merge_args(dict(request.query_params), await read_json_body(request))
    frm = args.get("frm") or args.get("from_group")
    to = args.get("to") or args.get("to_group")
    if not frm or not to:
        return JSONResponse({"detail": "frm and to required"}, status_code=400)
    try:
        data = await file_store.merge_preview(get_db(), frm, to)
    except KeyError as e:
        return JSONResponse({"detail": str(e.args[0])}, status_code=404)
    return JSONResponse(data)


class GroupMerge(BaseModel):
    from_group: str
    to_group: str
    base_file: str


@router.api_route("/groups/merge", methods=["GET", "POST"])
async def groups_merge(request: Request):
    """그룹 합치기 (기준 파일 지정)."""
    body = await read_json_body(request)
    payload = GroupMerge(
        from_group=body.get("from_group") or request.query_params.get("from_group", ""),
        to_group=body.get("to_group") or request.query_params.get("to_group", ""),
        base_file=body.get("base_file") or request.query_params.get("base_file", ""),
    )
    try:
        data = await file_store.merge_groups(
            get_db(), payload.from_group, payload.to_group, payload.base_file
        )
    except KeyError as e:
        return JSONResponse({"detail": str(e.args[0])}, status_code=404)
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)
    return JSONResponse(data)
