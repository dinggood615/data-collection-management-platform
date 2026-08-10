from __future__ import annotations

import importlib.util
import os
from pathlib import Path


def plugin_directory() -> Path:
    return Path(os.getenv("COLLECTOR_PLUGIN_DIR", "/data/collector-plugins"))


def collect_plugins(target_date: str, enabled_codes: set[str], keywords: list[str], exclusions: list[str]) -> tuple[list[dict], list[str]]:
    """Run trusted, administrator-installed collector plugins outside the Git tree."""
    directory = plugin_directory()
    if not directory.is_dir():
        return [], []
    items: list[dict] = []
    notices: list[str] = []
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_") or path.is_symlink():
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"platform_collector_{path.stem}", path)
            if spec is None or spec.loader is None:
                raise ImportError("plugin loader unavailable")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            collector = getattr(module, "collect", None)
            if not callable(collector):
                raise TypeError("missing collect function")
            batch, warning = collector(target_date, enabled_codes, keywords, exclusions)
            if not isinstance(batch, list):
                raise TypeError("collector result must be a list")
            items.extend(batch)
            if warning:
                notices.append(str(warning))
        except Exception as exc:
            notices.append(f"外部采集器 {path.stem} 运行失败：{type(exc).__name__}")
    return items, notices
