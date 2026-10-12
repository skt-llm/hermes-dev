"""File reading: type detection + field/record extraction for csv/json/xml/txt.

The scan target may contain any of csv / json / xml / txt. We:
  * detect the actual type from content (fall back to 'txt' when unclear),
  * derive the list of field names (column names) from the first record,
  * count total records,
  * return sample records.
Field lists are normalized across all types so the UI can render a single table.
"""
from __future__ import annotations

import csv
import json
import os
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

# How much of a file to read for sniffing (bytes)
_SNIFF_BYTES = 128 * 1024
_MAX_RECORDS_SAMPLE = 100


def _read_text(path: str) -> str:
    # try utf-8, fall back to latin-1
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            with open(path, "r", encoding=enc, errors="strict") as f:
                return f.read()
        except (UnicodeDecodeError, ValueError):
            continue
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _looks_like_json(text: str) -> bool:
    t = text.lstrip()
    if not t:
        return False
    if t[0] in "{[":
        try:
            json.loads(text)
            return True
        except Exception:
            return False
    return False


def _looks_like_xml(text: str) -> bool:
    t = text.lstrip()
    if not t:
        return False
    # an xml declaration or a root element tag
    if t.startswith("<?xml") or (t.startswith("<") and not t.startswith("<!DOCTYPE")):
        # crude: must contain a closing '>' and look tag-like
        try:
            ET.fromstring(text)
            return True
        except Exception:
            return False
    return False


def _looks_like_csv(text: str) -> bool:
    t = text.lstrip()
    if not t:
        return False
    if "," not in t and "\t" not in t:
        return False
    # first line must be comma/tab separated
    first = t.splitlines()[0] if t.splitlines() else ""
    return ("," in first) or ("\t" in first)


def detect_type(path: str, text: str) -> str:
    # 1) try json (most structured, must fully parse)
    if _looks_like_json(text):
        return "json"
    # 2) try xml
    if _looks_like_xml(text):
        return "xml"
    # 3) csv heuristic on first non-empty line
    if _looks_like_csv(text):
        return "csv"
    # 4) fallback
    return "txt"


def _extract_fields_from_first_row_csv(text: str) -> List[str]:
    lines = [ln for ln in text.splitlines() if ln.strip() != ""]
    if not lines:
        return []
    try:
        # handle both comma and tab
        sample = lines[0]
        delimiter = "\t" if ("\t" in sample and "," not in sample) else ","
        reader = csv.reader([sample], delimiter=delimiter)
        header = next(reader)
        return [h.strip() for h in header]
    except Exception:
        return []


def _extract_fields_json(data: Any) -> List[str]:
    """Return ordered unique keys across records."""
    if isinstance(data, dict):
        # a single object -> its keys
        return list(data.keys())
    if isinstance(data, list):
        seen: List[str] = []
        seen_set = set()
        for item in data:
            if isinstance(item, dict):
                for k in item.keys():
                    if k not in seen_set:
                        seen_set.add(k)
                        seen.append(k)
        return seen
    return []


def _extract_fields_xml(text: str) -> List[str]:
    """Field names = union of child element names of the repeated item."""
    try:
        root = ET.fromstring(text)
    except Exception:
        return []
    # Determine the 'record' element: if root has many children of the same
    # tag, treat those as records and union their attribute+child names.
    children = list(root)
    field_names: List[str] = []
    seen = set()

    def add(name: str) -> None:
        if name and name not in seen:
            seen.add(name)
            field_names.append(name)

    # Case A: root itself is a list of repeated item elements
    if children:
        tag_counts: Dict[str, int] = {}
        for c in children:
            tag_counts[c.tag] = tag_counts.get(c.tag, 0) + 1
        # pick the most common repeated child tag if it appears > 1 time
        repeated = [t for t, n in tag_counts.items() if n > 1]
        if repeated:
            record_tag = max(repeated, key=lambda t: tag_counts[t])
            for c in children:
                if c.tag == record_tag:
                    for a in c.attrib.keys():
                        add(a)
                    for sub in c:
                        add(sub.tag)
            return field_names
        # Case B: single record under root -> use root's children as fields
        for c in children:
            for a in c.attrib.keys():
                add(a)
            for sub in c:
                add(sub.tag)
        return field_names
    # No children: use root's attributes
    for a in root.attrib.keys():
        add(a)
    return field_names


def _count_records(data_type: str, data: Any, text: str) -> int:
    if data_type == "json":
        if isinstance(data, list):
            return len(data)
        if isinstance(data, dict):
            return 1
        return 0
    if data_type == "xml":
        try:
            root = ET.fromstring(text)
            children = list(root)
            if children:
                tag_counts: Dict[str, int] = {}
                for c in children:
                    tag_counts[c.tag] = tag_counts.get(c.tag, 0) + 1
                repeated = [t for t, n in tag_counts.items() if n > 1]
                if repeated:
                    rec = max(repeated, key=lambda t: tag_counts[t])
                    return tag_counts[rec]
                return len(children)
            return 1
        except Exception:
            return 0
    if data_type == "csv":
        try:
            lines = [ln for ln in text.splitlines() if ln.strip() != ""]
            # header + data rows
            return max(0, len(lines) - 1)
        except Exception:
            return 0
    # txt
    lines = [ln for ln in text.splitlines() if ln.strip() != ""]
    return len(lines)


def _sample_records(data_type: str, data: Any, text: str) -> List[Dict[str, Any]]:
    if data_type == "json":
        if isinstance(data, list):
            return [d for d in data[:_MAX_RECORDS_SAMPLE] if isinstance(d, dict)]
        if isinstance(data, dict):
            return [data]
        return []
    if data_type == "xml":
        try:
            root = ET.fromstring(text)
        except Exception:
            return []
        children = list(root)
        records: List[Dict[str, Any]] = []
        if children:
            tag_counts: Dict[str, int] = {}
            for c in children:
                tag_counts[c.tag] = tag_counts.get(c.tag, 0) + 1
            repeated = [t for t, n in tag_counts.items() if n > 1]
            record_tag = max(repeated, key=lambda t: tag_counts[t]) if repeated else None
            for c in children:
                if record_tag is None or c.tag == record_tag:
                    rec: Dict[str, Any] = dict(c.attrib)
                    for sub in c:
                        rec[sub.tag] = (sub.text or "").strip()
                    records.append(rec)
        return records[:_MAX_RECORDS_SAMPLE]
    if data_type == "csv":
        lines = [ln for ln in text.splitlines() if ln.strip() != ""]
        if len(lines) < 1:
            return []
        try:
            first = lines[0]
            delimiter = "\t" if ("\t" in first and "," not in first) else ","
            reader = csv.reader((ln for ln in lines[: _MAX_RECORDS_SAMPLE + 1]), delimiter=delimiter)
            header = next(reader)
            records = []
            for row in reader:
                rec = {}
                for i, h in enumerate(header):
                    rec[h.strip()] = row[i].strip() if i < len(row) else ""
                records.append(rec)
            return records
        except Exception:
            return []
    # txt: no structured records
    return []


def parse_file(path: str) -> Dict[str, Any]:
    """Parse a file. Returns:
        {type, fields:[...], total_records:int, records:[...]}
    Raises on unreadable file.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    text = _read_text(path)
    ftype = detect_type(path, text)

    data: Any = None
    if ftype == "json":
        try:
            data = json.loads(text)
        except Exception:
            ftype = "txt"

    fields: List[str] = []
    total = 0
    records: List[Dict[str, Any]] = []

    if ftype == "json":
        fields = _extract_fields_json(data)
        total = _count_records("json", data, text)
        records = _sample_records("json", data, text)
    elif ftype == "xml":
        fields = _extract_fields_xml(text)
        total = _count_records("xml", None, text)
        records = _sample_records("xml", None, text)
    elif ftype == "csv":
        fields = _extract_fields_from_first_row_csv(text)
        total = _count_records("csv", None, text)
        records = _sample_records("csv", None, text)
    else:
        # txt
        total = _count_records("txt", None, text)

    return {
        "type": ftype,
        "fields": fields,
        "total_records": total,
        "records": records,
    }


def mtime_str(path: str) -> str:
    import time

    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(os.path.getmtime(path)))
