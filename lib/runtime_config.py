"""런타임 설정 캐시.

Playground Core Manager 구조 대응:
    lib/runtime_config.py
    - get_config(): 캐시 없으면 load_config()로 초기화 후 반환
    - apply_override(): 런타임 config 부분 병합 (파일 변경 없음)
    - reload_config(): 베이스+로컬 override 재로딩 (변경 후 리스너 알림)

핸들러는 main.py의 모듈 전역 주입이 아니라 항상 get_config()를 사용한다.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, List, Optional

from .config_loader import AppConfig, build_config, dict_deep_merge, load_config

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_config: Optional[AppConfig] = None
_override: Dict[str, Any] = {}  # /config_override 로 누적된 런타임 오버라이드
_listeners: List[Callable[[AppConfig], None]] = []


def get_config() -> AppConfig:
    global _config
    with _lock:
        if _config is None:
            _config = load_config()
            if _override:
                _config = _rebuild_with_override(_config)
        assert _config is not None
        return _config


def _rebuild_with_override(cfg: AppConfig) -> AppConfig:
    raw = dict_deep_merge(cfg.raw, _override)
    return build_config(raw, cfg.config_path)


def apply_override(override: Dict[str, Any]) -> AppConfig:
    """런타임 config 부분 병합 (GET /config_override)."""
    global _config, _override
    with _lock:
        cfg = _config if _config is not None else load_config()
        _override = dict_deep_merge(_override, override or {})
    new_cfg = _rebuild_with_override(cfg)  # lock 밖에서 빌드 (재입력 방지)
    with _lock:
        _config = new_cfg
    _notify()
    return new_cfg


def reload_config() -> AppConfig:
    """베이스 설정 재로딩 (GET /reload). 런타임 오버라이드는 유지."""
    global _config
    with _lock:
        override_snapshot = dict(_override)
    base = load_config()  # lock 밖에서 파일 로딩
    new_cfg = (
        _rebuild_with_override(base) if override_snapshot else base
    )
    with _lock:
        _config = new_cfg
    _notify()
    return new_cfg


def on_reload(callback: Callable[[AppConfig], None]) -> None:
    _listeners.append(callback)


def _notify() -> None:
    cfg = get_config()
    import asyncio

    for cb in _listeners:
        try:
            res = cb(cfg)
            if asyncio.iscoroutine(res):
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None
                if loop is not None:
                    loop.create_task(res)
                else:
                    asyncio.run(res)
        except Exception:
            logger.exception("config reload listener failed")
