from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import subprocess
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import ConfigStore
from .defaults import DEFAULT_CONFIG
from .filtering import scan_regions, verify_running_state
from .mihomo import select_node as mihomo_select_node
from .mihomo import selector_status, test_latencies
from .profiles import get_profile, new_profile, normalize_profiles, now_text, runtime_config
from .subscription import fetch_subscription_info, install_filtered_config, refresh_subscription

LOG = logging.getLogger("openclash-region-filter")
INDEX_HTML = Path(__file__).with_name("dashboard.html").read_text(encoding="utf-8")


def sanitize_result_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {key: ("" if key in {"source_url", "dashboard_secret"} else sanitize_result_payload(value)) for key, value in payload.items()}
    if isinstance(payload, list):
        return [sanitize_result_payload(value) for value in payload]
    return payload


class AppState:
    def __init__(self, store: ConfigStore):
        self.store = store
        self.lock = threading.RLock()
        self.operation_lock = threading.Lock()
        self.last_result: dict[str, Any] | None = None
        self.profile_results: dict[str, dict[str, Any]] = {}
        self.last_subscription_result: dict[str, Any] | None = None
        self.last_install_result: dict[str, Any] | None = None
        self.stop_event = threading.Event()
        with self.lock:
            self.store.save(self._managed_config(self.store.load()))

    @staticmethod
    def _managed_config(config: dict[str, Any]) -> dict[str, Any]:
        config = normalize_profiles(config)
        output_path = str(config.get("subscription", {}).get("output_config_path") or DEFAULT_CONFIG["subscription"]["output_config_path"])
        config["subscription"]["output_config_path"] = output_path
        config["openclash"]["config_path"] = output_path
        config["openclash"]["runtime_config_path"] = DEFAULT_CONFIG["openclash"]["runtime_config_path"]
        config["openclash"]["reload_command"] = str(config["openclash"].get("reload_command") or DEFAULT_CONFIG["openclash"]["reload_command"])
        config["openclash"]["dashboard_api"] = str(config["openclash"].get("dashboard_api") or DEFAULT_CONFIG["openclash"]["dashboard_api"])
        config["openclash"]["verify_api"] = True
        config["automation"]["enabled"] = True
        active = get_profile(config)
        config["subscription"]["source_url"] = active["source_url"]
        config["filter"] = copy.deepcopy(active["filter"])
        config["regions"] = copy.deepcopy(active["regions"])
        return config

    def load_config(self) -> dict[str, Any]:
        with self.lock:
            return self._managed_config(self.store.load())

    def _save(self, config: dict[str, Any]) -> None:
        self.store.save(self._managed_config(config))

    def _profile_config(self, config: dict[str, Any], profile_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        profile = get_profile(config, profile_id)
        return profile, runtime_config(config, profile)

    def state_payload(self) -> dict[str, Any]:
        config = self.load_config()
        profiles = copy.deepcopy(config["subscriptions"])
        for profile in profiles:
            if isinstance(profile.get("quota"), dict):
                profile["quota"].pop("raw", None)
        scans: dict[str, Any] = {}
        for profile in profiles:
            candidate = runtime_config(config, profile)
            try:
                scans[profile["id"]] = scan_regions(candidate["subscription"]["last_source_path"], candidate)
            except Exception as exc:
                scans[profile["id"]] = {"error": str(exc), "node_count": 0, "region_counts": {}, "nodes": []}
        selected = str(get_profile(config).get("selected_node") or "")
        try:
            selected = str(selector_status(runtime_config(config, get_profile(config))).get("now") or selected)
        except RuntimeError:
            pass
        return {
            "config": {"active_subscription_id": config["active_subscription_id"], "subscriptions": profiles},
            "scans": scans,
            "selected_node": selected,
            "last_result": sanitize_result_payload(self.profile_results.get(config["active_subscription_id"])),
            "last_subscription_result": sanitize_result_payload(self.last_subscription_result),
            "last_install_result": sanitize_result_payload(self.last_install_result),
        }

    def save_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            config = self.load_config()
            profile_id = str(payload.get("id") or "")
            if profile_id:
                profile = get_profile(config, profile_id)
            else:
                profile = new_profile()
                config["subscriptions"].append(profile)
            if "name" in payload:
                profile["name"] = str(payload.get("name") or "未命名订阅")[:80]
            if "source_url" in payload:
                profile["source_url"] = str(payload.get("source_url") or "").strip()
            if "refresh_interval_seconds" in payload:
                profile["refresh_interval_seconds"] = max(300, int(payload["refresh_interval_seconds"]))
            if "latency_interval_seconds" in payload:
                profile["latency"]["interval_seconds"] = max(0, int(payload["latency_interval_seconds"]))
            self._save(config)
            return {"ok": True, "profile_id": profile["id"]}

    def delete_profile(self, profile_id: str) -> dict[str, Any]:
        with self.lock:
            config = self.load_config()
            if profile_id == config["active_subscription_id"]:
                return {"ok": False, "error": "当前激活订阅不能删除"}
            before = len(config["subscriptions"])
            config["subscriptions"] = [profile for profile in config["subscriptions"] if profile["id"] != profile_id]
            if len(config["subscriptions"]) == before:
                return {"ok": False, "error": "订阅不存在"}
            self._save(config)
            return {"ok": True}

    def save_regions(self, profile_id: str, enabled: list[str], excluded: list[str]) -> dict[str, Any]:
        with self.lock:
            config = self.load_config()
            profile = get_profile(config, profile_id)
            profile["filter"]["enabled_regions"] = list(dict.fromkeys(map(str, enabled)))
            profile["filter"]["excluded_regions"] = list(dict.fromkeys(map(str, excluded)))
            active = profile_id == config["active_subscription_id"]
            self._save(config)
        return self.refresh_profile(profile_id, install=active)

    def _refresh_candidate(self, config: dict[str, Any], profile_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        profile, candidate = self._profile_config(config, profile_id)
        updated, result = refresh_subscription(candidate)
        payload = result.to_dict()
        self.last_subscription_result = payload
        if result.ok:
            profile["regions"] = copy.deepcopy(updated["regions"])
            profile["filter"] = copy.deepcopy(updated["filter"])
            profile["last_refresh_at"] = result.generated_at
            info = fetch_subscription_info(updated)
            if info.get("ok"):
                info.pop("raw", None)
                profile["quota"] = info
        return config, payload, updated

    def refresh_profile(self, profile_id: str, install: bool | None = None) -> dict[str, Any]:
        with self.operation_lock:
            with self.lock:
                config = self.load_config()
                active = profile_id == config["active_subscription_id"]
            config, refresh_payload, candidate = self._refresh_candidate(config, profile_id)
            if not refresh_payload.get("ok"):
                result = {"ok": False, "stage": "refresh", "error": refresh_payload.get("error"), "refresh": refresh_payload}
                self.last_result = result
                self.profile_results[profile_id] = result
                return result
            with self.lock:
                self._save(config)
            if not (active if install is None else install):
                result = {"ok": True, "installed": False, "message": "订阅缓存已更新", "refresh": refresh_payload}
                self.last_result = result
                self.profile_results[profile_id] = result
                return result
            return self._install_candidate(config, profile_id, candidate, switching=False)

    def _install_candidate(self, config: dict[str, Any], profile_id: str, candidate: dict[str, Any], switching: bool) -> dict[str, Any]:
        output_path = Path(candidate["subscription"]["output_config_path"])
        previous = output_path.read_bytes() if output_path.exists() else None
        install = install_filtered_config(candidate)
        self.last_install_result = install.to_dict()
        if not install.ok:
            result = {"ok": False, "stage": "install", "error": install.error}
            self.last_result = result
            self.profile_results[profile_id] = result
            return result
        reload_result = self.reload_openclash(config) if install.changed else {"ok": True, "skipped": True}
        if install.changed and reload_result.get("ok"):
            time.sleep(max(1, int(config.get("automation", {}).get("settle_seconds", 3))))
        verification = verify_running_state(candidate) if reload_result.get("ok") else {"ok": False, "error": "OpenClash 重载失败"}
        verified = bool(verification.get("ok")) and not verification.get("bad_nodes")
        if not reload_result.get("ok") or not verified:
            if install.changed and previous is not None:
                tmp = output_path.with_suffix(output_path.suffix + ".rollback")
                tmp.write_bytes(previous)
                os.replace(tmp, output_path)
                self.reload_openclash(config)
            result = {"ok": False, "stage": "verify", "error": "候选配置验证失败，已恢复原配置", "reload": reload_result, "verification": verification}
            self.last_result = result
            self.profile_results[profile_id] = result
            return result
        if switching:
            with self.lock:
                config["active_subscription_id"] = profile_id
                self._save(config)
        refresh = self.last_subscription_result or {}
        result = {**(refresh.get("filter_result") or {}), "ok": True, "changed": install.changed, "reload": reload_result, "verification": verification, "verified": True, "switched": switching}
        self.last_result = result
        self.profile_results[profile_id] = result
        return result

    def activate_profile(self, profile_id: str) -> dict[str, Any]:
        with self.operation_lock:
            with self.lock:
                config = self.load_config()
                if profile_id == config["active_subscription_id"]:
                    return {"ok": True, "skipped": True, "message": "该订阅已在使用中"}
            config, refresh_payload, candidate = self._refresh_candidate(config, profile_id)
            if not refresh_payload.get("ok"):
                return {"ok": False, "stage": "refresh", "error": refresh_payload.get("error")}
            with self.lock:
                self._save(config)
            return self._install_candidate(config, profile_id, candidate, switching=True)

    def test_latency(self, profile_id: str) -> dict[str, Any]:
        with self.operation_lock:
            with self.lock:
                config = self.load_config()
                if profile_id != config["active_subscription_id"]:
                    return {"ok": False, "error": "只能测试当前激活订阅"}
                profile, candidate = self._profile_config(config, profile_id)
            result = test_latencies(candidate, candidate["subscription"]["last_source_path"])
            with self.lock:
                config = self.load_config()
                profile = get_profile(config, profile_id)
                profile["latency"]["results"] = result["results"]
                profile["latency"]["last_test_at"] = now_text()
                self._save(config)
            return {**result, "last_test_at": profile["latency"]["last_test_at"]}

    def select_node(self, profile_id: str, node_name: str) -> dict[str, Any]:
        with self.lock:
            config = self.load_config()
            if profile_id != config["active_subscription_id"]:
                return {"ok": False, "error": "只能选择当前激活订阅的节点"}
            _, candidate = self._profile_config(config, profile_id)
        result = mihomo_select_node(candidate, node_name)
        with self.lock:
            config = self.load_config()
            get_profile(config, profile_id)["selected_node"] = node_name
            self._save(config)
        return result

    def reload_openclash(self, config: dict[str, Any] | None = None) -> dict[str, Any]:
        config = config or self.load_config()
        command = str(config["openclash"].get("reload_command", "")).strip()
        if not command:
            return {"ok": False, "error": "OpenClash 重载命令未配置"}
        try:
            completed = subprocess.run(command, shell=True, check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
            return {"ok": completed.returncode == 0, "returncode": completed.returncode, "output": completed.stdout.strip()}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "OpenClash 重载超时"}


def automation_loop(state: AppState) -> None:
    last_refresh: dict[str, float] = {}
    last_latency: dict[str, float] = {}
    while not state.stop_event.is_set():
        try:
            config = state.load_config()
            now = time.time()
            active_id = config["active_subscription_id"]
            for profile in config["subscriptions"]:
                profile_id = profile["id"]
                if now - last_refresh.get(profile_id, 0) >= max(300, int(profile.get("refresh_interval_seconds", 3600))):
                    state.refresh_profile(profile_id, install=profile_id == active_id)
                    last_refresh[profile_id] = time.time()
                latency_interval = max(0, int(profile.get("latency", {}).get("interval_seconds", 14400)))
                if profile_id == active_id and profile_id not in last_latency:
                    last_latency[profile_id] = now
                elif profile_id == active_id and latency_interval and now - last_latency.get(profile_id, now) >= latency_interval:
                    state.test_latency(profile_id)
                    last_latency[profile_id] = time.time()
            state.stop_event.wait(max(10, int(config["automation"].get("poll_seconds", 30))))
        except Exception:
            LOG.exception("Automation loop failed")
            state.stop_event.wait(30)


class Handler(BaseHTTPRequestHandler):
    server: "RegionFilterServer"

    def log_message(self, fmt: str, *args: Any) -> None:
        LOG.info("%s - %s", self.address_string(), fmt % args)

    def send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

    def send_subscription(self, head_only: bool = False) -> bool:
        parsed = urlparse(self.path)
        config = self.server.app_state.load_config()
        if parsed.path != str(config["subscription"].get("public_path") or "/subscription.yaml"):
            return False
        candidate = runtime_config(config, get_profile(config))
        token = str(config["subscription"].get("token", ""))
        supplied = (parse_qs(parsed.query).get("token") or [""])[0] or self.headers.get("X-Subscription-Token", "")
        if token and supplied != token:
            self.send_json({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
            return True
        cache_path = Path(candidate["subscription"]["cache_path"])
        if not cache_path.exists():
            self.send_json({"error": "subscription cache is empty"}, HTTPStatus.SERVICE_UNAVAILABLE)
            return True
        body = cache_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/yaml; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)
        return True

    def do_HEAD(self) -> None:
        if not self.send_subscription(True):
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if self.send_subscription():
            return
        if path == "/":
            body = INDEX_HTML.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/state":
            self.send_json(self.server.app_state.state_payload())
        else:
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        payload = self.read_json()
        state = self.server.app_state
        try:
            if path == "/api/subscriptions/save": result = state.save_profile(payload)
            elif path == "/api/subscriptions/delete": result = state.delete_profile(str(payload.get("id", "")))
            elif path == "/api/subscriptions/refresh": result = state.refresh_profile(str(payload.get("id", "")))
            elif path == "/api/subscriptions/activate": result = state.activate_profile(str(payload.get("id", "")))
            elif path == "/api/regions/apply": result = state.save_regions(str(payload.get("id", "")), payload.get("enabled_regions", []), payload.get("excluded_regions", []))
            elif path == "/api/latency/test": result = state.test_latency(str(payload.get("id", "")))
            elif path == "/api/nodes/select": result = state.select_node(str(payload.get("id", "")), str(payload.get("name", "")))
            else:
                self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            self.send_json(result, HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST)
        except (KeyError, ValueError, RuntimeError, OSError) as exc:
            self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)


class RegionFilterServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], app_state: AppState):
        super().__init__(address, Handler)
        self.app_state = app_state


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenClash local region filter")
    parser.add_argument("--config", default="/data/config.json")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    state = AppState(ConfigStore(args.config))
    if args.once:
        print(json.dumps(state.refresh_profile(state.load_config()["active_subscription_id"]), ensure_ascii=False, indent=2))
        return 0
    threading.Thread(target=automation_loop, args=(state,), daemon=True).start()
    server = RegionFilterServer((args.host, args.port), state)
    LOG.info("Serving on http://%s:%s", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        state.stop_event.set()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
