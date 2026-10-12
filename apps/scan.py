"""스캔 라우터 — 전체 트리 스캔 / 상세 재조회·변경기록.

Playground Core Manager 구조 대응: apps/ (파일 스캐너의 "prediction" 역할)

  /scan      → lib/file_store.scan_directory(db)
  /scan-all  → lib/file_store.scan_all(db)
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from lib import file_store
from lib.db import get_db

router = APIRouter()


@router.post("/scan")
async def scan():
    """스캔하기: initialdir 스캔 → files upsert (first_seen/last_modified)."""
    summary = await file_store.scan_directory(get_db())
    return JSONResponse(summary.__dict__)


@router.post("/scan-all")
async def scan_all(request: Request):
    """전체 스캔·변경기록: 전체 스캔 + 모든 파일 상세 재조회, 변경사항 기록."""
    result = await file_store.scan_all(get_db())
    return JSONResponse(
        {
            "created": result.created,
            "updated": result.updated,
            "parse_failed": result.parse_failed,
            "details_recorded": result.details_recorded,
            "details_changed": result.details_changed,
            "changes": [c.__dict__ for c in result.changes],
        }
    )
