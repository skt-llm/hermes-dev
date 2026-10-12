"""파일 스캔/상세/그룹 도메인 서비스 (router가 호출하는 핵심 함수들).

Playground Core Manager 구조 대응:
    각 엔드포인트가 "어떤 함수를 어떤 인자와 함께 호출하는지"를 드러내기 위해
    router(apps/)는 이 모듈의 함수를 호출만 한다.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import scanner
from .db import DB
from .runtime_config import get_config


# ---------------------------------------------------------------------------
# result types (엔드포인트 응답 직결)
# ---------------------------------------------------------------------------
@dataclass
class ScanSummary:
    created: int = 0
    updated: int = 0
    skipped: int = 0


@dataclass
class FileChange:
    id: str
    changes: List[str] = field(default_factory=list)


@dataclass
class ScanAllSummary:
    created: int = 0
    updated: int = 0
    parse_failed: int = 0
    details_recorded: int = 0
    details_changed: int = 0
    changes: List[FileChange] = field(default_factory=list)


# ---------------------------------------------------------------------------
# listing
# ---------------------------------------------------------------------------
async def list_files(db: DB, group_filter: str = "") -> List[Dict[str, Any]]:
    """파일 목록 (그룹 포함 필터)."""
    return await db.list_files(group_filter)


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------
def _root() -> str:
    return get_config().initialdir


def _iter_files() -> List[Tuple[str, str]]:
    """(relative_id, absolute_path) 목록 (정렬)."""
    root = _root()
    out: List[Tuple[str, str]] = []
    for dirpath, _dirs, files in os.walk(root):
        for fn in sorted(files):
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            out.append((rel, full))
    return out


def _ctime_str(path: str) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(os.path.getctime(path)))


async def scan_directory(db: DB) -> ScanSummary:
    """전체 트리 스캔: files 테이블 upsert (first_seen/last_modified)."""
    summary = ScanSummary()
    for rel, full in _iter_files():
        try:
            res = await db.upsert_file(rel, _ctime_str(full), scanner.mtime_str(full))
            if res == "created":
                summary.created += 1
            else:
                summary.updated += 1
        except Exception:
            summary.skipped += 1
    return summary


async def scan_all(db: DB) -> ScanAllSummary:
    """전체 트리 스캔 + 모든 파일 상세 재조회, 변경사항 기록.

    파일마다:
      1) files upsert (first_seen/last_modified)
      2) parse_file() → persist_parsed() (유저 필드 설정 보존)
      3) 변경 감지: type / 필드 추가·제거 / 레코드 수
    """
    result = ScanAllSummary()
    for rel, full in _iter_files():
        try:
            res = await db.upsert_file(rel, _ctime_str(full), scanner.mtime_str(full))
            if res == "created":
                result.created += 1
            else:
                result.updated += 1
        except Exception:
            result.parse_failed += 1
            continue

        try:
            before = await db.get_file(rel)
            before_type = before["file_type"] if before else None
            before_fields = {f["field_name"] for f in await db.get_fields(rel)}
            before_meta = await db.get_meta(rel)

            parsed = scanner.parse_file(full)
            await persist_parsed(db, rel, parsed, keep_settings=True)

            after_type = parsed.get("type", "txt")
            after_fields = set(parsed.get("fields", []))
            after_records = parsed.get("total_records")
            ch: List[str] = []
            if before_meta is None:
                result.details_recorded += 1
                ch.append(
                    f"상세 최초 기록 ({after_type}, 필드 {len(after_fields)}, 레코드 {after_records})"
                )
            else:
                if before_type != after_type:
                    ch.append(f"type {before_type or 'N/A'}→{after_type}")
                added = after_fields - before_fields
                removed = before_fields - after_fields
                if added:
                    ch.append("필드 추가: " + ", ".join(sorted(added)))
                if removed:
                    ch.append("필드 제거: " + ", ".join(sorted(removed)))
                if before_meta.get("total_records") != after_records:
                    ch.append(f"레코드 {before_meta.get('total_records')}→{after_records}")
            if ch:
                result.changes.append(FileChange(id=rel, changes=ch))
        except Exception:
            result.parse_failed += 1

    result.details_changed = sum(
        1 for c in result.changes if not any("최초 기록" in x for x in c.changes)
    )
    return result


# ---------------------------------------------------------------------------
# file detail
# ---------------------------------------------------------------------------
def _abs(file_id: str) -> str:
    return os.path.join(_root(), file_id)


async def persist_parsed(db: DB, file_id: str, parsed: Dict[str, Any], keep_settings: bool = True) -> None:
    """parse 결과를 db에 기록 (유저 필드 설정 보존)."""
    new_fields = parsed.get("fields", [])
    existing = (
        {f["field_name"]: f for f in await db.get_fields(file_id)}
        if keep_settings
        else {}
    )
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
    await db.upsert_fields(file_id, rows)
    await db.delete_fields_not_in(file_id, new_fields)
    await db.set_file_type(file_id, parsed.get("type", "txt"))
    await db.set_meta(file_id, parsed.get("total_records"), db.now())


async def ensure_detail_recorded(db: DB, file_id: str) -> None:
    """최초 상세 진입 시 파일 실접근으로 기록 (이미 기록됐으면 no-op)."""
    if await db.get_meta(file_id) is not None:
        return
    path = _abs(file_id)
    if not os.path.isfile(path):
        await db.set_meta(file_id, 0, db.now())
        return
    parsed = scanner.parse_file(path)
    await persist_parsed(db, file_id, parsed, keep_settings=True)


async def refresh_file(db: DB, file_id: str) -> None:
    """갱신하기: 파일 재읽기 → type/fields/meta 업데이트 (설정 보존)."""
    path = _abs(file_id)
    parsed = scanner.parse_file(path)
    await persist_parsed(db, file_id, parsed, keep_settings=True)


async def get_file_detail(db: DB, file_id: str) -> Dict[str, Any]:
    """상세보기 페이로드: file + fields + meta + parsers."""
    if await db.get_file(file_id) is None:
        raise KeyError(file_id)
    await ensure_detail_recorded(db, file_id)
    return {
        "file": await db.get_file(file_id),
        "fields": await db.get_fields(file_id),
        "meta": await db.get_meta(file_id),
        "parsers": get_config().parser_names(),
    }


# ---------------------------------------------------------------------------
# file settings
# ---------------------------------------------------------------------------
async def save_file_settings(
    db: DB,
    file_id: str,
    file_type: Optional[str] = None,
    group_name: Optional[str] = None,
    parser: Optional[str] = None,
    fields: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """상세보기 저장: 변경된 항목만 db 반영."""
    if await db.get_file(file_id) is None:
        raise KeyError(file_id)
    if file_type is not None:
        await db.set_file_type(file_id, file_type)
    if group_name is not None:
        await db.set_group(file_id, group_name.strip() or file_id)
    if parser is not None:
        await db.set_parser(file_id, parser or None)
    if fields is not None:
        rows = [
            {
                "field_name": f["field_name"],
                "keep": bool(f.get("keep")),
                "rename_to": f.get("rename_to") or None,
                "is_tag": bool(f.get("is_tag")),
            }
            for f in fields
            if f.get("field_name")
        ]
        await db.upsert_fields(file_id, rows)
    return {"file": await db.get_file(file_id), "ok": True}


# ---------------------------------------------------------------------------
# groups
# ---------------------------------------------------------------------------
async def group_field_order(db: DB, group_name: str) -> List[str]:
    """그룹 필드 순서: 기준 파일 필드 우선, 나머지 알파벳 추가."""
    base = await db.get_group_base(group_name)
    fids = await db.files_in_group(group_name)
    fmap = await db.field_map()
    if base and base in fmap and fmap[base]:
        order = list(fmap[base])
    else:
        order = []
        for fid in sorted(fids):
            if fmap.get(fid):
                order = list(fmap[fid])
                break
    seen = set(order)
    order += sorted({f for fid in fids for f in fmap.get(fid, []) if f not in seen})
    return order


async def get_group_detail(db: DB, group_name: str) -> Dict[str, Any]:
    """그룹 상세 페이로드: files + 필드 합집합 테이블 + 기준 파일."""
    fids = await db.files_in_group(group_name)
    if not fids:
        raise KeyError(group_name)
    fmap = await db.field_map()
    base = await db.get_group_base(group_name)
    if base not in fids:
        base = None
    order = await group_field_order(db, group_name)

    files = []
    for fid in sorted(fids):
        row = await db.get_file(fid)
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

    base_rows = {}
    if base:
        for f in await db.get_fields(base):
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

    return {
        "group_name": group_name,
        "base_file": base,
        "file_count": len(fids),
        "files": files,
        "field_order": order,
        "field_table": field_table,
    }


async def set_group_base(db: DB, group_name: str, base_file: Optional[str]) -> None:
    fids = await db.files_in_group(group_name)
    if not fids:
        raise KeyError(group_name)
    if base_file is not None and base_file not in fids:
        raise ValueError("base_file is not in this group")
    await db.set_group_base(group_name, base_file)


async def rename_group(db: DB, group_name: str, new_group_name: str) -> int:
    """그룹명 변경: 해당 그룹의 모든 파일 group_name 교체."""
    new = new_group_name.strip()
    if not new:
        raise ValueError("new group name required")
    fids = await db.files_in_group(group_name)
    if not fids:
        raise KeyError(group_name)
    now = db.now()
    for fid in fids:
        await db.conn.execute(
            "UPDATE files SET group_name = ?, updated_at = ? WHERE id = ?",
            (new, now, fid),
        )
    await db.conn.commit()
    await db.set_group_base(new, await db.get_group_base(group_name))
    await db.conn.execute("DELETE FROM groups WHERE name = ?", (group_name,))
    await db.conn.commit()
    return len(fids)


async def assign_group_regex(
    db: DB, regex: str, group_name: str, require_same_fields: bool = False
) -> Dict[str, Any]:
    """Regex 기반 일괄 그룹 지정."""
    try:
        rx = re.compile(regex)
    except re.error as e:
        raise ValueError(f"invalid regex: {e}")
    rows = await db.list_files("")
    targets = [r for r in rows if rx.search(r["id"]) or rx.search(r["group_name"] or "")]

    if require_same_fields:
        ref = None
        for t in targets:
            fs = {f["field_name"] for f in await db.get_fields(t["id"])}
            if fs:
                ref = fs
                break
        if ref is not None:
            targets = [
                t
                for t in targets
                if {f["field_name"] for f in await db.get_fields(t["id"])} == ref
            ]

    now = db.now()
    new = group_name.strip() or regex
    for t in targets:
        await db.conn.execute(
            "UPDATE files SET group_name = ?, updated_at = ? WHERE id = ?",
            (new, now, t["id"]),
        )
    await db.conn.commit()
    return {"assigned": len(targets), "ids": [t["id"] for t in targets], "ok": True}


async def _group_ref_file(db: DB, group_name: str) -> Optional[str]:
    base = await db.get_group_base(group_name)
    fids = await db.files_in_group(group_name)
    fmap = await db.field_map()
    if base in fids and fmap.get(base):
        return base
    for fid in sorted(fids):
        if fmap.get(fid):
            return fid
    return None


async def merge_preview(db: DB, from_group: str, to_group: str) -> Dict[str, Any]:
    """그룹 합치기 미리보기: 필드 구조 비교 + 기준 파일 후보(설정 충돌 포함)."""
    if not await db.files_in_group(from_group):
        raise KeyError(f"source group not found: {from_group}")
    if not await db.files_in_group(to_group):
        raise KeyError(f"target group not found: {to_group}")
    fmap = await db.field_map()

    frm_fids = await db.files_in_group(from_group)
    to_fids = await db.files_in_group(to_group)
    frm_ref = await _group_ref_file(db, from_group)
    to_ref = await _group_ref_file(db, to_group)
    frm_fields = fmap.get(frm_ref, []) if frm_ref else []
    to_fields = fmap.get(to_ref, []) if to_ref else []

    frm_set, to_set = set(frm_fields), set(to_fields)
    common = [f for f in frm_fields if f in to_set]
    only_from = [f for f in frm_fields if f not in to_set]
    only_to = [f for f in to_fields if f not in frm_set]

    to_rows = {f["field_name"]: f for f in (await db.get_fields(to_ref) if to_ref else [])}

    candidates = []
    all_fids = sorted(set(frm_fids) | set(to_fids))
    for fid in all_fids:
        fl = fmap.get(fid, [])
        if not fl:
            continue
        cand_rows = {f["field_name"]: f for f in await db.get_fields(fid)}
        conflicts = [
            f
            for f in common
            if f in cand_rows and f in to_rows
            and (
                bool(cand_rows[f]["keep"]) != bool(to_rows[f]["keep"])
                or (cand_rows[f]["rename_to"] or None) != (to_rows[f]["rename_to"] or None)
                or bool(cand_rows[f]["is_tag"]) != bool(to_rows[f]["is_tag"])
            )
        ]
        candidates.append(
            {
                "file": fid,
                "group": from_group if fid in frm_fids else to_group,
                "field_count": len(fl),
                "conflict_count": len(conflicts),
                "conflicts": conflicts,
            }
        )
    candidates.sort(
        key=lambda c: (0 if c["file"] == to_ref else 1, -c["field_count"], c["conflict_count"])
    )

    return {
        "from": {"group": from_group, "ref_file": frm_ref, "fields": frm_fields},
        "to": {"group": to_group, "ref_file": to_ref, "fields": to_fields},
        "common": common,
        "only_from": only_from,
        "only_to": only_to,
        "conflict": bool(only_from) or bool(only_to),
        "candidates": candidates,
    }


async def merge_groups(
    db: DB, from_group: str, to_group: str, base_file: str
) -> Dict[str, Any]:
    """그룹 합치기: 소스 그룹의 파일들을 대상으로 이동 + 기준 파일 지정."""
    if from_group == to_group:
        raise ValueError("source and target group are the same")
    frm_fids = await db.files_in_group(from_group)
    if not frm_fids:
        raise KeyError("source group not found")
    to_fids = await db.files_in_group(to_group)
    if not to_fids and base_file not in frm_fids:
        raise ValueError("target group does not exist and base_file not in source")
    if base_file not in set(to_fids) | set(frm_fids):
        raise ValueError("base_file is not in either group")

    now = db.now()
    await db.conn.execute(
        "UPDATE files SET group_name = ?, updated_at = ? WHERE group_name = ?",
        (to_group, now, from_group),
    )
    await db.set_group_base(to_group, base_file)
    await db.conn.execute("DELETE FROM groups WHERE name = ?", (from_group,))
    await db.conn.commit()
    return {"merged": len(frm_fids), "to_group": to_group, "base_file": base_file, "ok": True}
