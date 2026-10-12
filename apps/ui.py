"""UI 라우터 — GET / 단일 HTML 페이지 (버튼 → 아래 API 호출).

Playground Core Manager 구조 대응: apps/ui.py
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import os

from lib.runtime_config import get_config

router = APIRouter()
templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")
)


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    cfg = get_config()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "initialdir": cfg.initialdir,
            "db_path": cfg.db_path,
            "parser_names": cfg.parser_names(),
        },
    )
