from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import yaml

from .filtering import LATENCY_GROUP_NAME, load_yaml, proxy_names_from_data


def dashboard_secrets(config: dict[str, Any]) -> list[str]:
    secrets: list[str] = []
    configured = str(config.get("openclash", {}).get("dashboard_secret", "")).strip()
    if configured:
        secrets.append(configured)
    candidates = [
        config.get("openclash", {}).get("runtime_config_path"),
        "/etc/openclash/openclash-region-filter.yaml",
        config.get("openclash", {}).get("config_path"),
        config.get("subscription", {}).get("output_config_path"),
    ]
    seen_paths: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        candidate_path = str(candidate)
        if candidate_path in seen_paths:
            continue
        seen_paths.add(candidate_path)
        try:
            secret = str(load_yaml(candidate_path).get("secret", "")).strip()
        except (OSError, ValueError):
            continue
        if secret and secret not in secrets:
            secrets.append(secret)
            break
    return secrets


def api_request(
    config: dict[str, Any], path: str, *, method: str = "GET", payload: dict[str, Any] | None = None,
    timeout: int = 8,
) -> Any:
    api = str(config.get("openclash", {}).get("dashboard_api", "")).rstrip("/")
    if not api:
        raise RuntimeError("OpenClash 控制 API 未配置")
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    authorization_failed = False
    secrets = dashboard_secrets(config)
    for secret in secrets + ([""] if not secrets else []):
        request = urllib.request.Request(api + path, data=body, method=method)
        request.add_header("Content-Type", "application/json")
        if secret:
            request.add_header("Authorization", f"Bearer {secret}")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw else {"ok": True}
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                authorization_failed = True
                continue
            raise RuntimeError(f"OpenClash 控制 API 返回 HTTP {exc.code}") from exc
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"无法连接 OpenClash 控制 API：{exc}") from exc
    if authorization_failed:
        raise RuntimeError("OpenClash 控制 API 验证失败")
    raise RuntimeError("OpenClash 控制 API 不可用")


def selector_status(config: dict[str, Any]) -> dict[str, Any]:
    payload = api_request(config, "/proxies")
    proxies = payload.get("proxies", {}) if isinstance(payload, dict) else {}
    selectors = []
    for name, item in proxies.items():
        if not isinstance(item, dict) or not isinstance(item.get("all"), list):
            continue
        if str(item.get("type", "")).lower() != "selector":
            continue
        selectors.append({"name": name, "now": item.get("now"), "all": item.get("all", [])})
    preferred = None
    for name in ("🚀 节点选择", "Proxy", "PROXY"):
        preferred = next((item for item in selectors if item["name"] == name), None)
        if preferred:
            break
    preferred = preferred or (max(selectors, key=lambda item: len(item["all"])) if selectors else None)
    if not preferred:
        raise RuntimeError("没有找到可手动选择节点的策略组")
    selected = preferred.get("now")
    resolved = selected
    visited: set[str] = set()
    while isinstance(resolved, str) and resolved not in visited:
        visited.add(resolved)
        child = proxies.get(resolved)
        if not isinstance(child, dict) or not child.get("now"):
            break
        resolved = child["now"]
    preferred["selected"] = selected
    preferred["now"] = resolved
    return preferred


def select_node(config: dict[str, Any], node_name: str) -> dict[str, Any]:
    selector = selector_status(config)
    if node_name not in selector["all"]:
        raise RuntimeError("该节点不在当前已启用地区中")
    path = "/proxies/" + urllib.parse.quote(str(selector["name"]), safe="")
    api_request(config, path, method="PUT", payload={"name": node_name})
    verified = selector_status(config)
    if verified.get("selected") != node_name or verified.get("now") != node_name:
        raise RuntimeError("节点切换后验证失败，OpenClash 未使用所选节点")
    return {"ok": True, "group": selector["name"], "selected_node": node_name}


def test_latencies(config: dict[str, Any], source_path: str | Path) -> dict[str, Any]:
    data = yaml.safe_load(Path(source_path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("订阅缓存无效")
    names = proxy_names_from_data(data)
    if not names:
        raise RuntimeError("当前订阅没有可测速节点")
    encoded_url = urllib.parse.quote("https://www.gstatic.com/generate_204", safe="")
    group = urllib.parse.quote(LATENCY_GROUP_NAME, safe="")
    payload = api_request(
        config,
        f"/group/{group}/delay?timeout=8000&url={encoded_url}",
        timeout=15,
    )
    results: dict[str, int | None] = {}
    for name in names:
        delay = payload.get(name) if isinstance(payload, dict) else None
        results[name] = int(delay) if isinstance(delay, (int, float)) and delay > 0 else None
    reachable = sum(delay is not None for delay in results.values())
    return {"ok": reachable > 0, "total": len(names), "reachable": reachable, "results": results}
