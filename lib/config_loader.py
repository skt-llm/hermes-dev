"""설정 로딩/병합 도메인 로직.

Playground Core Manager 구조 대응:
    lib/config_loader.py
    - load_config(): 베이스 설정(parser-config.yaml) 읽기
    - local override yaml 존재 시 dict_deep_merge 로 병합
    - reload_config(): 런타임 재로딩
"""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List

import yaml

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def dict_deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """재귀 딕 병합 (override 우선). list는 교체."""
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = dict_deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


@dataclass
class ParserConfig:
    name: str
    description: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AppConfig:
    initialdir: str = "."
    db_path: str = ""
    parsers: List[ParserConfig] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)
    config_path: str = ""

    def parser_names(self) -> List[str]:
        return [p.name for p in self.parsers]


def find_config_path() -> str:
    candidates = []
    env = os.environ.get("PARSER_CONFIG")
    if env:
        candidates.append(env)
    candidates.append(os.path.join(os.getcwd(), "parser-config.yaml"))
    candidates.append(os.path.join(APP_DIR, "parser-config.yaml"))
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    raise FileNotFoundError(
        "parser-config.yaml not found. Place it next to the app or set PARSER_CONFIG."
    )


def _find_override_path() -> str | None:
    env = os.environ.get("PARSER_CONFIG_OVERRIDE")
    if env and os.path.isfile(env):
        return env
    local = os.path.join(APP_DIR, "parser-config.local.yaml")
    return local if os.path.isfile(local) else None


def build_config(raw: Dict[str, Any], config_path: str) -> AppConfig:
    base_dir = os.path.dirname(os.path.abspath(config_path))
    files_cfg = raw.get("files") or {}
    db_cfg = raw.get("db") or {}

    initialdir = files_cfg.get("initialdir", ".")
    if not os.path.isabs(initialdir):
        initialdir = os.path.abspath(os.path.join(base_dir, initialdir))

    db_path = db_cfg.get("path") or files_cfg.get("db_path") or os.path.join(base_dir, "scan.db")
    if not os.path.isabs(db_path):
        db_path = os.path.abspath(os.path.join(base_dir, db_path))

    parsers: List[ParserConfig] = []
    for p in (raw.get("parsers") or []):
        if isinstance(p, dict):
            parsers.append(
                ParserConfig(
                    name=str(p.get("name", "")).strip(),
                    description=str(p.get("description", "") or ""),
                    extra=p,
                )
            )
        elif isinstance(p, str) and p.strip():
            parsers.append(ParserConfig(name=p.strip()))

    return AppConfig(
        initialdir=initialdir,
        db_path=db_path,
        parsers=parsers,
        raw=raw,
        config_path=config_path,
    )


def load_config() -> AppConfig:
    """베이스 설정 + 로컬 override 를 deep-merge 한 AppConfig."""
    config_path = find_config_path()
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    override_path = _find_override_path()
    if override_path:
        with open(override_path, "r", encoding="utf-8") as f:
            override = yaml.safe_load(f) or {}
        raw = dict_deep_merge(raw, override)

    return build_config(raw, config_path)
