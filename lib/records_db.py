"""Postgres 저장 레이어 — 클라이언트(raw_file_scan.py)가 올린 원시 레코드 저장.

앱 UI(스캔/그룹)는 SQLite(lib/db.py)를 쓰지만, scanner/refresh API 로 들어오는
원시 레코드는 Postgres (raw_files / raw_records 테이블) 에 저장한다.

설정 (환경변수, main.py 의 load_dotenv 로 .env 에서 로드):
    DATABASE_URL      (우선 사용)
    POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB / POSTGRES_HOST / POSTGRES_PORT
                      (기본: postgres / (비밀번호 없음) / scanner / localhost / 5432)
                      localhost 일 때 소켓(/var/run/postgresql) → tcp 순서로 시도
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
from typing import Any, Dict, List

import psycopg2
import psycopg2.extras

_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_files (
    id            BIGSERIAL PRIMARY KEY,
    file_path     TEXT        NOT NULL UNIQUE,
    file_type     TEXT        NOT NULL,
    last_update   TIMESTAMPTZ NOT NULL,
    first_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_refresh  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS raw_records (
    id            BIGSERIAL PRIMARY KEY,
    file_id       BIGINT NOT NULL REFERENCES raw_files(id) ON DELETE CASCADE,
    record_index  INT    NOT NULL,
    data          JSONB  NOT NULL,
    UNIQUE (file_id, record_index)
);

CREATE INDEX IF NOT EXISTS idx_raw_records_file ON raw_records (file_id);
"""

_schema_lock = threading.Lock()
_schema_ready = False


def _dsn_candidates() -> List[str]:
    url = os.environ.get("DATABASE_URL")
    if url:
        return [url]
    user = os.environ.get("POSTGRES_USER", "postgres")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ.get("POSTGRES_DB", "scanner")
    pw = f"password={password} " if password else ""
    if host in ("localhost", "127.0.0.1"):
        return [
            f"dbname={db} user={user} {pw}port={port} host=/var/run/postgresql",
            f"dbname={db} user={user} {pw}port={port} host={host}",
        ]
    return [f"dbname={db} user={user} password={password} port={port} host={host}"]


def _connect() -> psycopg2.extensions.connection:
    last_exc = None
    for dsn in _dsn_candidates():
        try:
            return psycopg2.connect(dsn)
        except psycopg2.OperationalError as exc:
            last_exc = exc
    raise last_exc  # type: ignore[misc]


def init_schema() -> None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def ensure_schema() -> None:
    """스키마 확인 (1회만, 실패 시 다음 호출에서 재시도)."""
    global _schema_ready
    with _schema_lock:
        if not _schema_ready:
            init_schema()
            _schema_ready = True


def parse_ts(value: str) -> dt.datetime:
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    return dt.datetime.fromisoformat(v)


def _ingest_file(cur: psycopg2.extensions.cursor, item: Dict[str, Any]) -> Dict[str, Any]:
    """files 업서트 + records 교체. 트랜잭션은 호출자가 관리."""
    last_update = parse_ts(item["last_update"])
    cur.execute(
        """
        INSERT INTO raw_files (file_path, file_type, last_update)
        VALUES (%s, %s, %s)
        ON CONFLICT (file_path) DO UPDATE SET
            file_type    = EXCLUDED.file_type,
            last_update  = EXCLUDED.last_update,
            last_refresh = now()
        RETURNING id
        """,
        (item["file_path"], item["file_type"], last_update),
    )
    row = cur.fetchone()
    assert row is not None, "RETURNING id did not produce a row"
    file_id = row[0]

    if item.get("records") is not None:
        records = item["records"]
    elif item.get("first_record"):
        records = [item["first_record"]]
    else:
        records = []

    cur.execute("DELETE FROM raw_records WHERE file_id = %s", (file_id,))
    if records:
        psycopg2.extras.execute_batch(
            cur,
            "INSERT INTO raw_records (file_id, record_index, data) VALUES (%s, %s, %s)",
            [(file_id, i, json.dumps(r, ensure_ascii=False)) for i, r in enumerate(records)],
            page_size=500,
        )
    return {
        "file_path": item["file_path"],
        "file_type": item["file_type"],
        "last_update": item["last_update"],
        "record_count": len(records),
    }


def ingest(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """파일 페이로드 목록을 저장. 1개 파일 실패는 스킵 후 나머지 커밋."""
    ensure_schema()
    stored = skipped = 0
    file_infos: List[Dict[str, Any]] = []
    conn = _connect()
    try:
        with conn.cursor() as cur:
            for item in items:
                try:
                    info = _ingest_file(cur, item)
                except Exception as exc:  # 파일 1개 파손 → 스킵
                    conn.rollback()
                    skipped += 1
                    print(f"[warn] records_db: skip {item.get('file_path')}: {exc}", flush=True)
                    continue
                stored += 1
                file_infos.append(info)
        conn.commit()
    finally:
        conn.close()
    return {"received": len(items), "stored": stored, "skipped": skipped, "files": file_infos}


def health() -> Dict[str, Any]:
    ensure_schema()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM raw_files")
            row = cur.fetchone()
            n = row[0] if row is not None else 0
    finally:
        conn.close()
    return {"ok": True, "files": n}


def list_files(pattern: str = "") -> Dict[str, Any]:
    """저장된 파일 목록. pattern 이 있으면 file_path 에 포함된 것만 (대소문자 무시)."""
    ensure_schema()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            q = (
                "SELECT f.file_path, f.file_type, f.last_update, f.last_refresh, "
                "(SELECT count(*) FROM raw_records r WHERE r.file_id = f.id) AS record_count, "
                "(SELECT r.data FROM raw_records r WHERE r.file_id = f.id "
                " ORDER BY r.record_index LIMIT 1) AS sample_record "
                "FROM raw_files f"
            )
            params: List[Any] = []
            if pattern:
                q += " WHERE f.file_path ILIKE %s"
                params.append(f"%{pattern}%")
            q += " ORDER BY f.file_path"
            cur.execute(q, params)
            rows = cur.fetchall()
    finally:
        conn.close()
    files = [
        {
            "file_path": r[0],
            "file_type": r[1],
            "last_update": r[2].isoformat() if r[2] is not None else None,
            "last_refresh": r[3].isoformat() if r[3] is not None else None,
            "record_count": r[4],
            "sample_record": r[5],  # 첫 레코드(샘플), 없으면 None
        }
        for r in rows
    ]
    return {"count": len(files), "files": files}
