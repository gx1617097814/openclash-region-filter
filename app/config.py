from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .defaults import DEFAULT_CONFIG


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def merge_regions(saved: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    defaults = copy.deepcopy(DEFAULT_CONFIG["regions"])
    if not saved:
        return defaults

    saved_by_id = {
        str(region.get("id")): region
        for region in saved
        if isinstance(region, dict) and region.get("id")
    }
    merged = []
    seen = set()

    for default_region in defaults:
        region_id = str(default_region["id"])
        merged.append(deep_merge(default_region, saved_by_id.get(region_id, {})))
        seen.add(region_id)

    for region in saved:
        if isinstance(region, dict) and region.get("id") and str(region["id"]) not in seen:
            merged.append(region)

    return merged


class ConfigStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            self.save(copy.deepcopy(DEFAULT_CONFIG))
            return copy.deepcopy(DEFAULT_CONFIG)

        raw = json.loads(self.path.read_text(encoding="utf-8"))
        config = deep_merge(DEFAULT_CONFIG, raw)
        config["regions"] = merge_regions(raw.get("regions") if isinstance(raw, dict) else None)
        return config

    def save(self, config: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self.path)
