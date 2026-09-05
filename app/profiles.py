from __future__ import annotations

import copy
import re
import time
import uuid
from pathlib import Path
from typing import Any

from .config import merge_regions
from .defaults import DEFAULT_CONFIG


def new_profile(name: str = "新订阅", source_url: str = "") -> dict[str, Any]:
    profile_id = uuid.uuid4().hex[:12]
    return {
        "id": profile_id,
        "name": name,
        "source_url": source_url,
        "refresh_interval_seconds": 3600,
        "last_refresh_at": None,
        "filter": copy.deepcopy(DEFAULT_CONFIG["filter"]),
        "regions": copy.deepcopy(DEFAULT_CONFIG["regions"]),
        "quota": {},
        "latency": {
            "interval_seconds": 14400,
            "last_test_at": None,
            "results": {},
        },
        "selected_node": "",
    }


def safe_profile_id(value: Any) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "", str(value or ""))
    return cleaned[:48] or uuid.uuid4().hex[:12]


def normalize_profiles(config: dict[str, Any]) -> dict[str, Any]:
    profiles = config.get("subscriptions")
    if not isinstance(profiles, list) or not profiles:
        legacy = config.get("subscription", {})
        profile = new_profile("当前订阅", str(legacy.get("source_url", "")))
        profile["id"] = "current"
        profile["refresh_interval_seconds"] = int(legacy.get("refresh_interval_seconds", 3600))
        profile["filter"] = copy.deepcopy(config.get("filter", DEFAULT_CONFIG["filter"]))
        profile["regions"] = merge_regions(config.get("regions"))
        profiles = [profile]

    normalized = []
    used = set()
    for index, raw in enumerate(profiles):
        if not isinstance(raw, dict):
            continue
        profile = new_profile()
        profile.update(copy.deepcopy(raw))
        profile_id = safe_profile_id(profile.get("id"))
        while profile_id in used:
            profile_id = uuid.uuid4().hex[:12]
        used.add(profile_id)
        profile["id"] = profile_id
        profile["name"] = str(profile.get("name") or f"订阅 {index + 1}")[:80]
        profile["source_url"] = str(profile.get("source_url") or "").strip()
        profile["refresh_interval_seconds"] = max(300, int(profile.get("refresh_interval_seconds", 3600)))
        profile["filter"] = {**copy.deepcopy(DEFAULT_CONFIG["filter"]), **(profile.get("filter") or {})}
        profile["filter"]["allow_unknown"] = True
        profile["filter"]["keep_disabled_proxies_for_latency"] = True
        profile["regions"] = merge_regions(profile.get("regions"))
        latency = profile.get("latency") if isinstance(profile.get("latency"), dict) else {}
        profile["latency"] = {
            "interval_seconds": max(0, int(latency.get("interval_seconds", 14400))),
            "last_test_at": latency.get("last_test_at"),
            "results": latency.get("results") if isinstance(latency.get("results"), dict) else {},
        }
        normalized.append(profile)

    if not normalized:
        normalized = [new_profile("当前订阅")]
    active_id = str(config.get("active_subscription_id") or "")
    if active_id not in {profile["id"] for profile in normalized}:
        active_id = normalized[0]["id"]
    config["subscriptions"] = normalized
    config["active_subscription_id"] = active_id
    return config


def get_profile(config: dict[str, Any], profile_id: str | None = None) -> dict[str, Any]:
    wanted = profile_id or str(config.get("active_subscription_id") or "")
    for profile in config.get("subscriptions", []):
        if profile.get("id") == wanted:
            return profile
    raise KeyError("subscription profile not found")


def runtime_config(config: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(config)
    profile_id = safe_profile_id(profile["id"])
    root = Path("/data/subscriptions") / profile_id
    subscription = copy.deepcopy(config.get("subscription", DEFAULT_CONFIG["subscription"]))
    subscription.update({
        "source_url": profile.get("source_url", ""),
        "refresh_interval_seconds": profile.get("refresh_interval_seconds", 3600),
        "cache_path": str(root / "filtered.yaml"),
        "last_source_path": str(root / "source.yaml"),
    })
    result["subscription"] = subscription
    result["filter"] = copy.deepcopy(profile["filter"])
    result["regions"] = copy.deepcopy(profile["regions"])
    return result


def now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
