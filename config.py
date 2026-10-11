"""Configuration loading from parser-config.yaml."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List

import yaml

APP_DIR = os.path.dirname(os.path.abspath(__file__))


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


def _find_config_path() -> str:
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


def load_config() -> AppConfig:
    path = _find_config_path()
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    base_dir = os.path.dirname(os.path.abspath(path))
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
        config_path=path,
    )
