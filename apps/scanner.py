"""scanner/refresh 라우터 — 클라이언트가 올린 원시 레코드 파일을 Postgres 에 저장.

  POST /scanner/refresh   raw_file_scan.py 의 스캔 결과(JSON 배열) 수신
  GET  /scanner/health    Postgres 연결/저장 파일 수 확인

페이로드 항목 (파일 1개):
    {file_path, file_type, last_update, first_record | records}
    - first_record : 첫 레코드(샘플)
    - records      : -F 모드 전체 레코드
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from lib import records_db

router = APIRouter()


class FilePayload(BaseModel):
    file_path: str
    file_type: str
    last_update: str
    first_record: Optional[Dict[str, Any]] = None
    records: Optional[List[Dict[str, Any]]] = None


@router.post("/scanner/refresh")
async def scanner_refresh(items: List[FilePayload]):
    """클라이언트 스캔 결과를 Postgres 에 저장 (files 업서트 + records 교체)."""
    if not items:
        raise HTTPException(status_code=400, detail="빈 페이로드입니다")
    try:
        result = await run_in_threadpool(records_db.ingest, [i.model_dump() for i in items])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"저장 실패: {exc}")
    return JSONResponse(result)


@router.get("/scanner/health")
async def scanner_health():
    """Postgres 연결 상태 + 저장된 파일 수."""
    try:
        return await run_in_threadpool(records_db.health)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"db unavailable: {exc}")


@router.get("/scanner/list")
async def scanner_list(file: str = ""):
    """저장된 파일 목록. file 문구를 주면 그 문구를 file_path 에 포함한 파일만,
    없으면 전체를 반환 (대소문자 무시)."""
    try:
        return await run_in_threadpool(records_db.list_files, file.strip())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"조회 실패: {exc}")
