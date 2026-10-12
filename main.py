"""Playground File Scanner — 애플리케이션 부팅 엔트리포인트.

부팅 흐름 (Playground Core Manager 구조 대응):
  main.py 실행
    │
    ├─ 환경변수 로드: REVISION → env.{revision} → load_dotenv()
    ├─ 로깅 설정 (LOG_LEVEL / LOG_FORMAT)
    │
    ├─ load_config()                              # lib/config_loader.py
    │     ├─ parser-config.yaml 읽기
    │     └─ 로컬 override(parser-config.local.yaml) deep-merge
    │
    ├─ config 를 각 라우터 모듈 전역에 주입
    │     ui.config / configuration.config / scan.config / files.config / groups.config = config
    │     (참고: 실제 핸들러는 lib/runtime_config.get_config() 런타임 캐시를 사용)
    │
    ├─ FastAPI 앱 생성 (docs_url, redoc_url, openapi_url 에 CONTEXT_PATH 접두)
    │
    └─ include_router(prefix=context_path) 로 라우터 등록
          ui / configuration / scan / files / groups

실행:
    uvicorn main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import logging
import os
import sys

# ---------------------------------------------------------------------------
# 1. 환경변수 로드: REVISION → env.{revision} → load_dotenv()
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv  # noqa: E402

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
REVISION = os.environ.get("REVISION", "")
if REVISION:
    env_file = os.path.join(_APP_DIR, f"env.{REVISION}")
    if os.path.isfile(env_file):
        load_dotenv(env_file, override=False)
load_dotenv(os.path.join(_APP_DIR, ".env"), override=False)

# ---------------------------------------------------------------------------
# 2. 로깅 설정 (LOG_LEVEL / LOG_FORMAT)
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format=os.environ.get("LOG_FORMAT", "%(asctime)s %(levelname)s %(name)s - %(message)s"),
)
logger = logging.getLogger("main")

from fastapi import FastAPI  # noqa: E402

from lib import config_loader, runtime_config  # noqa: E402
from lib.common import install_exception_handlers  # noqa: E402
from lib.db import init_db  # noqa: E402

# ---------------------------------------------------------------------------
# 3. 설정 로딩 (lib/config_loader.load_config)
# ---------------------------------------------------------------------------
config = config_loader.load_config()
logger.info(
    "config loaded: %s (initialdir=%s, db=%s)",
    config.config_path, config.initialdir, config.db_path,
)

# ---------------------------------------------------------------------------
# 4. config 를 각 라우터 모듈 전역에 주입
#    (참고: 실제 핸들러는 runtime_config.get_config() 런타임 캐시를 사용한다)
# ---------------------------------------------------------------------------
import apps.configuration  # noqa: E402
import apps.files  # noqa: E402
import apps.groups  # noqa: E402
import apps.scan  # noqa: E402
import apps.ui  # noqa: E402

for m in (apps.ui, apps.configuration, apps.scan, apps.files, apps.groups):
    m.config = config

# ---------------------------------------------------------------------------
# 5. FastAPI 앱 생성 (CONTEXT_PATH 접두)
# ---------------------------------------------------------------------------
CONTEXT_PATH = os.environ.get("CONTEXT_PATH", "")
prefix = CONTEXT_PATH.rstrip("/")

app = FastAPI(
    title="File Scanner",
    docs_url=f"{prefix}/docs",
    redoc_url=f"{prefix}/redoc",
    openapi_url=f"{prefix}/openapi.json",
)

install_exception_handlers(app)


@app.get(f"{prefix}/api/health")
async def health():
    from lib.db import get_db

    return {"ok": True, "db": get_db().path}


# ---------------------------------------------------------------------------
# 6. 라우터 등록 (include_router(prefix=context_path))
#    UI 엔드포인트는 /api 접두 아래에 등록된다 (문서의 라우터별 접두 대응).
# ---------------------------------------------------------------------------
app.include_router(apps.ui.router, prefix=prefix)
app.include_router(apps.configuration.router, prefix=f"{prefix}/api")
app.include_router(apps.scan.router, prefix=f"{prefix}/api")
app.include_router(apps.files.router, prefix=f"{prefix}/api")
app.include_router(apps.groups.router, prefix=f"{prefix}/api")


# ---------------------------------------------------------------------------
# 7. 기동 시 DB 초기화 + config reload 리스너 (db 경로 변경 시 재접속)
# ---------------------------------------------------------------------------
async def _on_config_reload(cfg) -> None:
    await init_db(cfg.db_path)


@app.on_event("startup")
async def _startup():
    runtime_config.on_reload(_on_config_reload)
    await init_db(config.db_path)


@app.on_event("shutdown")
async def _shutdown():
    from lib.db import get_db

    await get_db().close()
