from __future__ import annotations

import dataclasses
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml


FLAG_REGION_LABELS = {
    "AC": "阿森松岛",
    "AD": "安道尔",
    "AE": "阿联酋",
    "AF": "阿富汗",
    "AG": "安提瓜和巴布达",
    "AI": "安圭拉",
    "AL": "阿尔巴尼亚",
    "AM": "亚美尼亚",
    "AO": "安哥拉",
    "AR": "阿根廷",
    "AT": "奥地利",
    "AU": "澳大利亚",
    "AZ": "阿塞拜疆",
    "BA": "波黑",
    "BD": "孟加拉",
    "BE": "比利时",
    "BG": "保加利亚",
    "BH": "巴林",
    "BR": "巴西",
    "CA": "加拿大",
    "CH": "瑞士",
    "CL": "智利",
    "CN": "大陆",
    "CO": "哥伦比亚",
    "CZ": "捷克",
    "DE": "德国",
    "DK": "丹麦",
    "EE": "爱沙尼亚",
    "EG": "埃及",
    "ES": "西班牙",
    "FI": "芬兰",
    "FR": "法国",
    "GB": "英国",
    "GR": "希腊",
    "HK": "香港",
    "HR": "克罗地亚",
    "HU": "匈牙利",
    "ID": "印尼",
    "IE": "爱尔兰",
    "IL": "以色列",
    "IN": "印度",
    "IR": "伊朗",
    "IS": "冰岛",
    "IT": "意大利",
    "JP": "日本",
    "KH": "柬埔寨",
    "KR": "韩国",
    "KZ": "哈萨克斯坦",
    "LA": "老挝",
    "LK": "斯里兰卡",
    "LT": "立陶宛",
    "LU": "卢森堡",
    "LV": "拉脱维亚",
    "MM": "缅甸",
    "MN": "蒙古",
    "MO": "澳门",
    "MX": "墨西哥",
    "MY": "马来西亚",
    "NL": "荷兰",
    "NO": "挪威",
    "NZ": "新西兰",
    "PH": "菲律宾",
    "PK": "巴基斯坦",
    "PL": "波兰",
    "PT": "葡萄牙",
    "RO": "罗马尼亚",
    "RU": "俄罗斯",
    "SA": "沙特",
    "SE": "瑞典",
    "SG": "新加坡",
    "TH": "泰国",
    "TR": "土耳其",
    "TW": "台湾",
    "UA": "乌克兰",
    "US": "美国",
    "VN": "越南",
    "ZA": "南非",
}

REGIONAL_INDICATOR_A = 0x1F1E6


BUILTIN_GROUP_ITEMS = {
    "DIRECT",
    "REJECT",
    "REJECT-DROP",
    "REJECT-DROP",
    "REJECT-DROP",
    "PASS",
}


def flag_country_code(flag: str) -> str | None:
    if len(flag) != 2:
        return None
    code = ""
    for char in flag:
        value = ord(char) - REGIONAL_INDICATOR_A
        if value < 0 or value > 25:
            return None
        code += chr(ord("A") + value)
    return code


def first_flag(name: str) -> str | None:
    chars = list(name)
    for index in range(len(chars) - 1):
        candidate = chars[index] + chars[index + 1]
        if flag_country_code(candidate):
            return candidate
    return None


def dynamic_region_for_name(name: str) -> dict[str, Any]:
    flag = first_flag(name)
    if flag:
        country_code = flag_country_code(flag) or "xx"
        label = FLAG_REGION_LABELS.get(country_code, country_code)
        return {
            "id": f"auto_{country_code.lower()}",
            "label": label,
            "patterns": [re.escape(flag)],
            "dynamic": True,
            "default_enabled": True,
        }

    return {
        "id": "other_unknown",
        "label": "其他地区",
        "patterns": [re.escape(name)],
        "dynamic": True,
        "default_enabled": True,
    }


def regions_with_dynamic_nodes(regions: list[dict[str, Any]], names: list[str]) -> list[dict[str, Any]]:
    merged = [dict(region) for region in regions]
    matcher = RegionMatcher(merged)
    existing = {str(region.get("id")) for region in merged}

    for name in names:
        region_id, _ = matcher.classify(name)
        if region_id != "unknown":
            continue

        dynamic = dynamic_region_for_name(name)
        dynamic_id = str(dynamic["id"])
        if dynamic_id in existing:
            if dynamic_id == "other_unknown":
                for region in merged:
                    if region.get("id") == dynamic_id:
                        patterns = region.setdefault("patterns", [])
                        for pattern in dynamic.get("patterns", []):
                            if pattern not in patterns:
                                patterns.append(pattern)
                        break
                matcher = RegionMatcher(merged)
            continue

        merged.append(dynamic)
        existing.add(dynamic_id)
        matcher = RegionMatcher(merged)

    return merged


@dataclasses.dataclass
class NodeInfo:
    name: str
    region_id: str
    region_label: str
    kept: bool
    reason: str


@dataclasses.dataclass
class FilterResult:
    ok: bool
    changed: bool
    config_path: str
    backup_path: str | None
    kept_nodes: list[str]
    removed_nodes: list[str]
    region_counts: dict[str, int]
    unknown_nodes: list[str]
    excluded_nodes: list[str]
    group_summaries: list[dict[str, Any]]
    warnings: list[str]
    verified: bool | None
    verification: dict[str, Any] | None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


class RegionMatcher:
    def __init__(self, regions: list[dict[str, Any]]):
        self.regions = regions
        self._default_enabled = {
            str(region.get("id"))
            for region in regions
            if region.get("default_enabled")
        }
        self._compiled: list[tuple[str, str, list[re.Pattern[str]]]] = []
        for region in regions:
            patterns = [
                re.compile(pattern, re.IGNORECASE)
                for pattern in region.get("patterns", [])
                if pattern
            ]
            self._compiled.append((region["id"], region["label"], patterns))

    def classify(self, name: str) -> tuple[str, str]:
        for region_id, label, patterns in self._compiled:
            if any(pattern.search(name) for pattern in patterns):
                return region_id, label
        return "unknown", "未知"

    def is_default_enabled(self, region_id: str) -> bool:
        return region_id in self._default_enabled

    def matches_desired_name(
        self,
        name: str,
        enabled_regions: set[str],
        excluded_regions: set[str],
        allow_unknown: bool,
    ) -> bool:
        region_id, _ = self.classify(name)
        if region_id in excluded_regions:
            return False
        if region_id == "unknown":
            return allow_unknown
        return region_id in enabled_regions or (
            allow_unknown and self.is_default_enabled(region_id)
        )


def load_yaml(path: str | Path) -> dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("YAML root must be a mapping")
    return data


def dump_yaml(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=4096)


def scan_regions(path: str | Path, config: dict[str, Any]) -> dict[str, Any]:
    data = load_yaml(path)
    proxies = data.get("proxies") or []
    if not isinstance(proxies, list):
        proxies = []

    names = [
        str(proxy["name"])
        for proxy in proxies
        if isinstance(proxy, dict) and proxy.get("name")
    ]
    regions = regions_with_dynamic_nodes(config["regions"], names)
    matcher = RegionMatcher(regions)

    nodes: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for proxy in proxies:
        if not isinstance(proxy, dict) or not proxy.get("name"):
            continue
        name = str(proxy["name"])
        region_id, region_label = matcher.classify(name)
        counts[region_id] = counts.get(region_id, 0) + 1
        nodes.append({"name": name, "region_id": region_id, "region_label": region_label})

    return {
        "config_path": str(path),
        "node_count": len(nodes),
        "region_counts": counts,
        "nodes": nodes,
        "regions": regions,
    }


def filter_config_data(data: dict[str, Any], config: dict[str, Any]) -> tuple[dict[str, Any], FilterResult]:
    matcher = RegionMatcher(config["regions"])
    enabled_regions = set(config["filter"].get("enabled_regions", []))
    excluded_regions = set(config["filter"].get("excluded_regions", []))
    allow_unknown = bool(config["filter"].get("allow_unknown", False))

    warnings: list[str] = []
    proxies = data.get("proxies") or []
    if not isinstance(proxies, list):
        raise ValueError("YAML field 'proxies' must be a list")

    if data.get("proxy-providers"):
        warnings.append(
            "proxy-providers is present. This version filters inline proxies and group references, "
            "but does not rewrite provider files."
        )

    original_proxy_names: set[str] = set()
    kept_proxies: list[dict[str, Any]] = []
    node_infos: list[NodeInfo] = []
    region_counts: dict[str, int] = {}
    unknown_nodes: list[str] = []
    excluded_nodes: list[str] = []

    for proxy in proxies:
        if not isinstance(proxy, dict) or "name" not in proxy:
            warnings.append("Skipped a malformed proxy entry without a name")
            continue

        name = str(proxy["name"])
        original_proxy_names.add(name)
        region_id, region_label = matcher.classify(name)
        region_counts[region_id] = region_counts.get(region_id, 0) + 1

        if region_id in excluded_regions:
            kept = False
            reason = "excluded_region"
            excluded_nodes.append(name)
        elif region_id == "unknown":
            kept = allow_unknown
            reason = "unknown_allowed" if kept else "unknown"
            if not kept:
                unknown_nodes.append(name)
        else:
            kept = region_id in enabled_regions or (
                allow_unknown and matcher.is_default_enabled(region_id)
            )
            reason = "enabled_region" if kept else "not_enabled_region"

        if kept:
            kept_proxies.append(proxy)
        node_infos.append(NodeInfo(name, region_id, region_label, kept, reason))

    desired_names = [str(proxy["name"]) for proxy in kept_proxies]
    desired_name_set = set(desired_names)

    if not desired_names:
        raise ValueError("Filtering would remove every proxy; refusing to write an empty config")

    group_summaries: list[dict[str, Any]] = []
    groups = data.get("proxy-groups") or []
    group_names = {str(group.get("name")) for group in groups if isinstance(group, dict) and group.get("name")}

    if isinstance(groups, list):
        for group in groups:
            if not isinstance(group, dict):
                continue
            group_name = str(group.get("name", ""))
            old_items = group.get("proxies")
            if not isinstance(old_items, list):
                continue

            new_items: list[str] = []
            for item in old_items:
                item_name = str(item)
                keep_item = False

                if item_name in original_proxy_names:
                    keep_item = item_name in desired_name_set
                elif item_name in group_names:
                    keep_item = True
                elif item_name in BUILTIN_GROUP_ITEMS:
                    keep_item = True
                elif matcher.matches_desired_name(
                    item_name, enabled_regions, excluded_regions, allow_unknown
                ):
                    keep_item = True

                if keep_item and item_name not in new_items:
                    new_items.append(item_name)

            group_type = str(group.get("type", "")).lower()
            if group_type in {"url-test", "fallback", "load-balance"}:
                new_items = [item for item in new_items if item in desired_name_set]

            if not new_items:
                warnings.append(f"Group {group_name!r} became empty; populated it with desired nodes")
                new_items = desired_names[:]

            if group_name in {"🚀 节点选择", "Proxy", "PROXY"}:
                auto_refs = [item for item in new_items if item in group_names]
                leaf_refs = [item for item in desired_names if item not in auto_refs]
                new_items = auto_refs + leaf_refs

            group["proxies"] = new_items
            group_summaries.append(
                {
                    "name": group_name,
                    "type": group.get("type"),
                    "before": len(old_items),
                    "after": len(new_items),
                    "items": new_items,
                }
            )
    else:
        warnings.append("YAML field 'proxy-groups' is not a list; group references were not rewritten")

    filtered = dict(data)
    filtered["proxies"] = kept_proxies

    kept_nodes = desired_names
    removed_nodes = [info.name for info in node_infos if not info.kept]

    result = FilterResult(
        ok=True,
        changed=False,
        config_path="",
        backup_path=None,
        kept_nodes=kept_nodes,
        removed_nodes=removed_nodes,
        region_counts=region_counts,
        unknown_nodes=unknown_nodes,
        excluded_nodes=excluded_nodes,
        group_summaries=group_summaries,
        warnings=warnings,
        verified=None,
        verification=None,
    )
    return filtered, result


def make_backup(path: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = backup_dir / f"{path.name}.{stamp}.bak"
    shutil.copy2(path, backup)
    return backup


def atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def apply_filter(config: dict[str, Any], reload_openclash: bool = True) -> FilterResult:
    path = Path(config["openclash"]["config_path"])
    backup_dir = Path(config.get("backup_dir") or path.parent / ".region-filter-backups")
    result: FilterResult
    try:
        original_text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(original_text)
        if not isinstance(data, dict):
            raise ValueError("YAML root must be a mapping")

        filtered, result = filter_config_data(data, config)
        rendered = dump_yaml(filtered)
        result.config_path = str(path)

        if rendered != original_text:
            backup = make_backup(path, backup_dir)
            atomic_write(path, rendered)
            result.backup_path = str(backup)
            result.changed = True

        if reload_openclash:
            reload_command = str(config["openclash"].get("reload_command", "")).strip()
            if reload_command:
                completed = subprocess.run(
                    reload_command,
                    shell=True,
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=120,
                )
                if completed.returncode != 0:
                    result.warnings.append(
                        f"Reload command exited {completed.returncode}: {completed.stdout.strip()}"
                    )

        if config["openclash"].get("verify_api"):
            result.verification = verify_running_state(config)
            result.verified = not result.verification.get("bad_nodes")

        return result
    except Exception as exc:
        return FilterResult(
            ok=False,
            changed=False,
            config_path=str(path),
            backup_path=None,
            kept_nodes=[],
            removed_nodes=[],
            region_counts={},
            unknown_nodes=[],
            excluded_nodes=[],
            group_summaries=[],
            warnings=[],
            verified=False,
            verification=None,
            error=str(exc),
        )


def verify_running_state(config: dict[str, Any]) -> dict[str, Any]:
    api = str(config["openclash"].get("dashboard_api", "")).rstrip("/")
    if not api:
        return {"skipped": True, "reason": "dashboard_api is empty"}

    matcher = RegionMatcher(config["regions"])
    enabled_regions = set(config["filter"].get("enabled_regions", []))
    excluded_regions = set(config["filter"].get("excluded_regions", []))
    allow_unknown = bool(config["filter"].get("allow_unknown", False))
    secret = str(config["openclash"].get("dashboard_secret", ""))

    request = urllib.request.Request(f"{api}/proxies")
    if secret:
        request.add_header("Authorization", f"Bearer {secret}")

    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = yaml.safe_load(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError) as exc:
        return {"ok": False, "error": str(exc)}

    proxies = payload.get("proxies", {}) if isinstance(payload, dict) else {}
    leaf_names = []
    bad_nodes = []

    for name, item in proxies.items():
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("all"), list):
            continue
        if item.get("type") in {"Direct", "Reject", "RejectDrop", "Compatible"}:
            continue
        if name == "PASS":
            continue

        leaf_names.append(name)
        if not matcher.matches_desired_name(name, enabled_regions, excluded_regions, allow_unknown):
            bad_nodes.append(name)

    return {
        "ok": True,
        "leaf_count": len(leaf_names),
        "leaf_names": leaf_names,
        "bad_nodes": bad_nodes,
    }
