"""SQLite persistence layer (async via aiosqlite)."""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

import aiosqlite

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY,
    first_seen TEXT NOT NULL,
    last_modified TEXT NOT NULL,
    file_type TEXT,
    group_name TEXT,
    parser TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS file_fields (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    keep INTEGER DEFAULT 0,
    rename_to TEXT,
    is_tag INTEGER DEFAULT 0,
    UNIQUE(file_id, field_name)
);
CREATE INDEX IF NOT EXISTS idx_file_fields_file ON file_fields(file_id);

CREATE TABLE IF NOT EXISTS file_meta (
    file_id TEXT PRIMARY KEY,
    total_records INTEGER,
    detail_checked_at TEXT
);

CREATE TABLE IF NOT EXISTS groups (
    name TEXT PRIMARY KEY,      -- group_name value in files
    base_file TEXT,             -- file whose field structure defines the group
    updated_at TEXT
);
"""


class DB:
    def __init__(self, path: str):
        self.path = path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "DB not connected"
        return self._conn

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # ---------- helpers ----------
    @staticmethod
    def now() -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())

    @staticmethod
    def _row_to_file(row: aiosqlite.Row) -> Dict[str, Any]:
        d = dict(row)
        return d

    # ---------- scan / upsert ----------
    async def upsert_file(
        self,
        file_id: str,
        first_seen: str,
        last_modified: str,
    ) -> str:
        """Insert or update. Returns 'created' or 'updated'."""
        cur = await self.conn.execute(
            "SELECT id FROM files WHERE id = ?", (file_id,)
        )
        exists = await cur.fetchone()
        now = self.now()
        if exists:
            await self.conn.execute(
                "UPDATE files SET last_modified = ?, updated_at = ? WHERE id = ?",
                (last_modified, now, file_id),
            )
            await self.conn.commit()
            return "updated"
        await self.conn.execute(
            "INSERT INTO files (id, first_seen, last_modified, group_name, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (file_id, first_seen, last_modified, file_id, now),
        )
        await self.conn.commit()
        return "created"

    async def delete_file(self, file_id: str) -> None:
        await self.conn.execute(
            "DELETE FROM file_fields WHERE file_id = ?", (file_id,)
        )
        await self.conn.execute(
            "DELETE FROM file_meta WHERE file_id = ?", (file_id,)
        )
        await self.conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
        await self.conn.commit()

    # ---------- file detail ----------
    async def set_file_type(self, file_id: str, file_type: str) -> None:
        await self.conn.execute(
            "UPDATE files SET file_type = ?, updated_at = ? WHERE id = ?",
            (file_type, self.now(), file_id),
        )
        await self.conn.commit()

    async def set_group(self, file_id: str, group_name: str) -> None:
        await self.conn.execute(
            "UPDATE files SET group_name = ?, updated_at = ? WHERE id = ?",
            (group_name, self.now(), file_id),
        )
        await self.conn.commit()

    async def set_parser(self, file_id: str, parser: Optional[str]) -> None:
        await self.conn.execute(
            "UPDATE files SET parser = ?, updated_at = ? WHERE id = ?",
            (parser, self.now(), file_id),
        )
        await self.conn.commit()

    # ---------- fields ----------
    async def get_fields(self, file_id: str) -> List[Dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT field_name, keep, rename_to, is_tag FROM file_fields "
            "WHERE file_id = ? ORDER BY id",
            (file_id,),
        )
        rows = await cur.fetchall()
        out = []
        for r in rows:
            out.append(
                {
                    "field_name": r["field_name"],
                    "keep": bool(r["keep"]),
                    "rename_to": r["rename_to"],
                    "is_tag": bool(r["is_tag"]),
                }
            )
        return out

    async def upsert_fields(
        self, file_id: str, fields: List[Dict[str, Any]]
    ) -> None:
        now = self.now()
        for f in fields:
            name = f.get("field_name")
            if not name:
                continue
            await self.conn.execute(
                """
                INSERT INTO file_fields (file_id, field_name, keep, rename_to, is_tag)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(file_id, field_name) DO UPDATE SET
                    keep = excluded.keep,
                    rename_to = excluded.rename_to,
                    is_tag = excluded.is_tag
                """,
                (
                    file_id,
                    name,
                    1 if f.get("keep") else 0,
                    f.get("rename_to") or None,
                    1 if f.get("is_tag") else 0,
                ),
            )
        await self.conn.commit()

    async def delete_fields_not_in(self, file_id: str, names: List[str]) -> None:
        # remove fields no longer present in the file
        cur = await self.conn.execute(
            "SELECT field_name FROM file_fields WHERE file_id = ?", (file_id,)
        )
        existing = {r["field_name"] for r in await cur.fetchall()}
        keep_set = set(names)
        for name in existing - keep_set:
            await self.conn.execute(
                "DELETE FROM file_fields WHERE file_id = ? AND field_name = ?",
                (file_id, name),
            )
        await self.conn.commit()

    # ---------- meta ----------
    async def get_meta(self, file_id: str) -> Optional[Dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT total_records, detail_checked_at FROM file_meta WHERE file_id = ?",
            (file_id,),
        )
        row = await cur.fetchone()
        if not row:
            return None
        return {"total_records": row["total_records"], "detail_checked_at": row["detail_checked_at"]}

    async def set_meta(
        self, file_id: str, total_records: Optional[int], checked_at: str
    ) -> None:
        await self.conn.execute(
            """
            INSERT INTO file_meta (file_id, total_records, detail_checked_at)
            VALUES (?, ?, ?)
            ON CONFLICT(file_id) DO UPDATE SET
                total_records = excluded.total_records,
                detail_checked_at = excluded.detail_checked_at
            """,
            (file_id, total_records, checked_at),
        )
        await self.conn.commit()

    # ---------- listing ----------
    async def list_files(self, group_filter: str = "") -> List[Dict[str, Any]]:
        q = """
        SELECT f.id, f.first_seen, f.last_modified, f.file_type, f.group_name,
               f.parser,
               (SELECT COUNT(*) FROM file_fields ff WHERE ff.file_id = f.id) AS field_count,
               (SELECT COUNT(*) FROM file_fields ff WHERE ff.file_id = f.id AND ff.keep = 1) AS keep_count,
               (SELECT GROUP_CONCAT(
                     CASE WHEN ff.rename_to IS NOT NULL AND ff.rename_to != ''
                          THEN ff.rename_to ELSE ff.field_name END, ',')
                   FROM file_fields ff WHERE ff.file_id = f.id AND ff.is_tag = 1) AS tags
        FROM files f
        """
        params: List[Any] = []
        if group_filter:
            q += " WHERE f.group_name LIKE ?"
            params.append(f"%{group_filter}%")
        q += " ORDER BY f.group_name, f.id"
        cur = await self.conn.execute(q, params)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_file(self, file_id: str) -> Optional[Dict[str, Any]]:
        cur = await self.conn.execute("SELECT * FROM files WHERE id = ?", (file_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def distinct_groups(self) -> List[str]:
        cur = await self.conn.execute(
            "SELECT DISTINCT group_name FROM files WHERE group_name IS NOT NULL "
            "ORDER BY group_name"
        )
        return [r[0] for r in await cur.fetchall()]

    async def files_in_group(self, group_name: str) -> List[Dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT id FROM files WHERE group_name = ? ORDER BY id", (group_name,)
        )
        return [r[0] for r in await cur.fetchall()]

    async def get_group_base(self, group_name: str) -> Optional[str]:
        cur = await self.conn.execute(
            "SELECT base_file FROM groups WHERE name = ?", (group_name,)
        )
        row = await cur.fetchone()
        return row[0] if row else None

    async def set_group_base(self, group_name: str, base_file: Optional[str]) -> None:
        await self.conn.execute(
            """
            INSERT INTO groups (name, base_file, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                base_file = excluded.base_file,
                updated_at = excluded.updated_at
            """,
            (group_name, base_file, self.now()),
        )
        await self.conn.commit()

    async def field_map(self) -> Dict[str, List[str]]:
        """file_id -> ordered list of field names (for union/intersection math)."""
        cur = await self.conn.execute(
            "SELECT file_id, field_name FROM file_fields ORDER BY file_id, id"
        )
        rows = await cur.fetchall()
        out: Dict[str, List[str]] = {}
        for r in rows:
            out.setdefault(r["file_id"], []).append(r["field_name"])
        return out

    async def all_file_ids(self) -> List[str]:
        cur = await self.conn.execute("SELECT id FROM files")
        return [r[0] for r in await cur.fetchall()]
