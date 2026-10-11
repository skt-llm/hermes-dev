"""FastAPI application: scanner web UI + REST API."""
from __future__ import annotations

import os
import re
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from config import load_config
from db import DB
import scanner

APP_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG = load_config()
DB_PATH = CONFIG.db_path
DB = DB(DB_PATH)
templates = Jinja2Templates(directory=os.path.join(APP_DIR, "templates"))
app = FastAPI(title="File Scanner")
app.state.config = CONFIG


@app.on_event("startup")
async def _startup():
    await DB.connect()


@app.on_event("shutdown")
async def _shutdown():
    await DB.close()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _abs(file_id: str) -> str:
    return os.path.join(CONFIG.initialdir, file_id)


async def _ensure_detail_recorded(file_id: str) -> dict:
    """On first detail entry, read the file and record type/fields/meta to db."""
    meta = await DB.get_meta(file_id)
    if meta is not None:
        return {}  # already recorded
    path = _abs(file_id)
    if not os.path.isfile(path):
        # file gone; still record a checked_at with no data
        await DB.set_meta(file_id, 0, DB.now())
        return {}
    try:
        parsed = scanner.parse_file(path)
    except Exception as e:
        return {"parse_error": str(e)}
    await _persist_parsed(file_id, parsed, keep_settings=True)
    return {}


async def _persist_parsed(file_id: str, parsed: dict, keep_settings: bool = True) -> None:
    """Write parsed type/fields/meta to db, preserving user field settings."""
    new_fields = parsed.get("fields", [])
    if keep_settings:
        existing = {f["field_name"]: f for f in await DB.get_fields(file_id)}
    else:
        existing = {}

    rows = []
    for name in new_fields:
        prev = existing.get(name)
        rows.append(
            {
                "field_name": name,
                "keep": bool(prev["keep"]) if prev else False,
                "rename_to": prev["rename_to"] if prev else None,
                "is_tag": bool(prev["is_tag"]) if prev else False,
            }
        )
    await DB.upsert_fields(file_id, rows)
    await DB.delete_fields_not_in(file_id, new_fields)
    await DB.set_file_type(file_id, parsed.get("type", "txt"))
    await DB.set_meta(file_id, parsed.get("total_records"), DB.now())


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "initialdir": CONFIG.initialdir,
            "db_path": DB_PATH,
            "parser_names": CONFIG.parser_names(),
        },
    )


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------
@app.post("/api/scan")
async def scan():
    root = CONFIG.initialdir
    if not os.path.isdir(root):
        raise HTTPException(400, f"initialdir not found: {root}")
    created, updated, skipped = 0, 0, 0
    seen_ids = set()
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            seen_ids.add(rel)
            try:
                import time

                first_seen = time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.gmtime(os.path.getctime(full))
                )
                last_mod = scanner.mtime_str(full)
                res = await DB.upsert_file(rel, first_seen, last_mod)
                if res == "created":
                    created += 1
                else:
                    updated += 1
            except Exception:
                skipped += 1
    return JSONResponse({"created": created, "updated": updated, "skipped": skipped})


# ---------------------------------------------------------------------------
# List files
# ---------------------------------------------------------------------------
@app.get("/api/files")
async def list_files(group: str = ""):
    rows = await DB.list_files(group)
    return JSONResponse({"files": rows})


@app.get("/api/groups")
async def groups():
    return JSONResponse({"groups": await DB.distinct_groups()})


@app.get("/api/parsers")
async def parsers():
    return JSONResponse({"parsers": CONFIG.parser_names()})


# ---------------------------------------------------------------------------
# File detail
# ---------------------------------------------------------------------------
@app.get("/api/file/detail")
async def file_detail(file_id: str):
    row = await DB.get_file(file_id)
    if not row:
        raise HTTPException(404, "file not found")
    await _ensure_detail_recorded(file_id)
    row = await DB.get_file(file_id)
    fields = await DB.get_fields(file_id)
    meta = await DB.get_meta(file_id)
    return JSONResponse(
        {
            "file": row,
            "fields": fields,
            "meta": meta,
            "parsers": CONFIG.parser_names(),
        }
    )


@app.post("/api/file/refresh")
async def file_refresh(file_id: str):
    row = await DB.get_file(file_id)
    if not row:
        raise HTTPException(404, "file not found")
    path = _abs(file_id)
    if not os.path.isfile(path):
        raise HTTPException(404, "file no longer on disk")
    parsed = scanner.parse_file(path)
    await _persist_parsed(file_id, parsed, keep_settings=True)
    row = await DB.get_file(file_id)
    fields = await DB.get_fields(file_id)
    meta = await DB.get_meta(file_id)
    return JSONResponse({"file": row, "fields": fields, "meta": meta})


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


@app.post("/api/file/save")
async def file_save(payload: DetailSave):
    row = await DB.get_file(payload.file_id)
    if not row:
        raise HTTPException(404, "file not found")
    if payload.file_type is not None:
        await DB.set_file_type(payload.file_id, payload.file_type)
    if payload.group_name is not None:
        g = payload.group_name.strip() or payload.file_id
        await DB.set_group(payload.file_id, g)
    if payload.parser is not None:
        await DB.set_parser(payload.file_id, payload.parser or None)
    # persist field settings (names from db, not re-parsed)
    names = []
    rows = []
    for f in payload.fields:
        if not f.field_name:
            continue
        names.append(f.field_name)
        rows.append(
            {
                "field_name": f.field_name,
                "keep": f.keep,
                "rename_to": (f.rename_to or None),
                "is_tag": f.is_tag,
            }
        )
    await DB.upsert_fields(payload.file_id, rows)
    row = await DB.get_file(payload.file_id)
    return JSONResponse({"file": row, "ok": True})


# ---------------------------------------------------------------------------
# Group rename
# ---------------------------------------------------------------------------
class GroupRename(BaseModel):
    group_name: str
    new_group_name: str


@app.post("/api/group/rename")
async def group_rename(payload: GroupRename):
    new = payload.new_group_name.strip()
    if not new:
        raise HTTPException(400, "new group name required")
    # rename every file currently in this group
    cur = await DB.conn.execute(
        "SELECT id FROM files WHERE group_name = ?", (payload.group_name,)
    )
    ids = [r[0] for r in await cur.fetchall()]
    now = DB.now()
    for fid in ids:
        await DB.conn.execute(
            "UPDATE files SET group_name = ?, updated_at = ? WHERE id = ?",
            (new, now, fid),
        )
    await DB.conn.commit()
    return JSONResponse({"renamed": len(ids), "ok": True})


# ---------------------------------------------------------------------------
# Regex bulk group assignment
# ---------------------------------------------------------------------------
class RegexAssign(BaseModel):
    regex: str
    group_name: str
    require_same_fields: bool = False


@app.post("/api/group/assign")
async def group_assign(payload: RegexAssign):
    try:
        rx = re.compile(payload.regex)
    except re.error as e:
        raise HTTPException(400, f"invalid regex: {e}")
    rows = await DB.list_files("")
    targets = [r for r in rows if rx.search(r["id"]) or rx.search(r["group_name"] or "")]

    if payload.require_same_fields:
        # only keep targets that share the exact same field-name set as the
        # first target that actually has fields (never-opened files have none)
        if targets:
            ref = None
            for t in targets:
                fs = {f["field_name"] for f in await DB.get_fields(t["id"])}
                if fs:
                    ref = fs
                    break
            if ref is not None:
                filtered = []
                for t in targets:
                    fs = {f["field_name"] for f in await DB.get_fields(t["id"])}
                    if fs == ref:
                        filtered.append(t)
                targets = filtered

    now = DB.now()
    new = payload.group_name.strip() or payload.regex
    for t in targets:
        await DB.conn.execute(
            "UPDATE files SET group_name = ?, updated_at = ? WHERE id = ?",
            (new, now, t["id"]),
        )
    await DB.conn.commit()
    return JSONResponse(
        {"assigned": len(targets), "ids": [t["id"] for t in targets], "ok": True}
    )


# ---------------------------------------------------------------------------
# Group detail (group-centric view)
# ---------------------------------------------------------------------------
async def _group_field_order(group_name: str) -> List[str]:
    """Field order for a group: base file's fields, then other fields appended."""
    base = await DB.get_group_base(group_name)
    fids = await DB.files_in_group(group_name)
    fmap = await DB.field_map()
    if base and base in fmap and fmap[base]:
        order = list(fmap[base])
    else:
        # default: first file (sorted) that has recorded fields
        order = []
        for fid in sorted(fids):
            if fmap.get(fid):
                order = list(fmap[fid])
                break
    # append any other fields in the group not in order, alphabetically
    seen = set(order)
    extra = sorted(
        {f for fid in fids for f in fmap.get(fid, []) if f not in seen}
    )
    return order + extra


@app.get("/api/group/detail")
async def group_detail(group: str):
    fids = await DB.files_in_group(group)
    if not fids:
        raise HTTPException(404, "group not found")
    fmap = await DB.field_map()
    base = await DB.get_group_base(group)
    if base not in fids:
        base = None
    order = await _group_field_order(group)

    # per-file info
    files = []
    for fid in sorted(fids):
        row = await DB.get_file(fid)
        files.append(
            {
                "id": row["id"],
                "first_seen": row["first_seen"],
                "last_modified": row["last_modified"],
                "file_type": row["file_type"],
                "field_count": len(fmap.get(fid, [])),
                "fields": fmap.get(fid, []),
            }
        )

    # base file's settings for the field table
    base_rows = {}
    if base:
        for f in await DB.get_fields(base):
            base_rows[f["field_name"]] = f

    field_table = []
    for fn in order:
        present = [fid for fid in sorted(fids) if fn in fmap.get(fid, [])]
        br = base_rows.get(fn)
        field_table.append(
            {
                "field": fn,
                "is_base": base is not None and fn in base_rows,
                "present": len(present),
                "missing": len(fids) - len(present),
                "present_in": present,
                "keep": bool(br["keep"]) if br else None,
                "rename_to": br["rename_to"] if br else None,
                "is_tag": bool(br["is_tag"]) if br else None,
            }
        )

    return JSONResponse(
        {
            "group_name": group,
            "base_file": base,
            "file_count": len(fids),
            "files": files,
            "field_order": order,
            "field_table": field_table,
        }
    )


class BaseSet(BaseModel):
    group_name: str
    base_file: Optional[str]


@app.post("/api/group/base")
async def group_base(payload: BaseSet):
    fids = await DB.files_in_group(payload.group_name)
    if not fids:
        raise HTTPException(404, "group not found")
    if payload.base_file is not None and payload.base_file not in fids:
        raise HTTPException(400, "base_file is not in this group")
    await DB.set_group_base(payload.group_name, payload.base_file)
    return JSONResponse({"ok": True, "base_file": payload.base_file})


# ---------------------------------------------------------------------------
# Group merge (with base-file selection)
# ---------------------------------------------------------------------------
def _fields_settings(fid: str, fmap: Dict[str, List[str]], rows: Dict[str, dict]) -> Dict[str, dict]:
    return {f["field_name"]: f for f in rows.get(fid, [])}


async def _group_ref_file(group_name: str) -> Optional[str]:
    """File whose field rows define the group's structure (base or first with fields)."""
    base = await DB.get_group_base(group_name)
    fids = await DB.files_in_group(group_name)
    fmap = await DB.field_map()
    if base in fids and fmap.get(base):
        return base
    for fid in sorted(fids):
        if fmap.get(fid):
            return fid
    return None


@app.get("/api/groups/merge-preview")
async def merge_preview(frm: str, to: str):
    if not await DB.files_in_group(frm):
        raise HTTPException(404, f"source group not found: {frm}")
    if not await DB.files_in_group(to):
        raise HTTPException(404, f"target group not found: {to}")
    fmap = await DB.field_map()

    frm_fids = await DB.files_in_group(frm)
    to_fids = await DB.files_in_group(to)

    frm_ref = await _group_ref_file(frm)
    to_ref = await _group_ref_file(to)

    frm_fields = fmap.get(frm_ref, []) if frm_ref else []
    to_fields = fmap.get(to_ref, []) if to_ref else []

    frm_set, to_set = set(frm_fields), set(to_fields)
    common = [f for f in frm_fields if f in to_set]
    only_from = [f for f in frm_fields if f not in to_set]
    only_to = [f for f in to_fields if f not in frm_set]

    # settings for conflict comparison: target group's reference file rows
    to_rows = {}
    if to_ref:
        for f in await DB.get_fields(to_ref):
            to_rows[f["field_name"]] = f

    # candidates = files in the merged group that have recorded fields
    candidates = []
    all_fids = sorted(set(frm_fids) | set(to_fids))
    for fid in all_fids:
        fl = fmap.get(fid, [])
        if not fl:
            continue
        # conflicts: fields in common where candidate's setting differs from target's
        cand_rows = {f["field_name"]: f for f in await DB.get_fields(fid)}
        conflicts = []
        for f in common:
            c = cand_rows.get(f)
            t = to_rows.get(f)
            if c and t:
                if (
                    bool(c["keep"]) != bool(t["keep"])
                    or (c["rename_to"] or None) != (t["rename_to"] or None)
                    or bool(c["is_tag"]) != bool(t["is_tag"])
                ):
                    conflicts.append(f)
        candidates.append(
            {
                "file": fid,
                "group": frm if fid in frm_fids else to,
                "field_count": len(fl),
                "conflict_count": len(conflicts),
                "conflicts": conflicts,
            }
        )
    # recommend: target's ref first, then most fields, then fewest conflicts
    candidates.sort(
        key=lambda c: (0 if c["file"] == to_ref else 1, -c["field_count"], c["conflict_count"])
    )

    return JSONResponse(
        {
            "from": {"group": frm, "ref_file": frm_ref, "fields": frm_fields},
            "to": {"group": to, "ref_file": to_ref, "fields": to_fields},
            "common": common,
            "only_from": only_from,
            "only_to": only_to,
            "conflict": bool(only_from) or bool(only_to),
            "candidates": candidates,
        }
    )


class GroupMerge(BaseModel):
    from_group: str
    to_group: str
    base_file: str


@app.post("/api/groups/merge")
async def group_merge(payload: GroupMerge):
    frm, to = payload.from_group, payload.to_group
    if frm == to:
        raise HTTPException(400, "source and target group are the same")
    frm_fids = await DB.files_in_group(frm)
    if not frm_fids:
        raise HTTPException(404, "source group not found")
    to_fids = await DB.files_in_group(to)
    if not to_fids and payload.base_file not in frm_fids:
        raise HTTPException(400, "target group does not exist and base_file not in source")
    # after merge, base_file must be in the target group
    if payload.base_file not in set(to_fids) | set(frm_fids):
        raise HTTPException(400, "base_file is not in either group")

    now = DB.now()
    await DB.conn.execute(
        "UPDATE files SET group_name = ?, updated_at = ? WHERE group_name = ?",
        (to, now, frm),
    )
    await DB.set_group_base(to, payload.base_file)
    await DB.conn.execute("DELETE FROM groups WHERE name = ?", (frm,))
    await DB.conn.commit()
    return JSONResponse(
        {"merged": len(frm_fids), "to_group": to, "base_file": payload.base_file, "ok": True}
    )


@app.get("/api/health")
async def health():
    return JSONResponse({"ok": True, "db": DB_PATH})
