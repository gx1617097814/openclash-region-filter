from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from .config import ConfigStore
from .defaults import DEFAULT_CONFIG
from .filtering import scan_regions, verify_running_state
from .mihomo import select_node as mihomo_select_node
from .mihomo import selector_status, test_latencies
from .profiles import get_profile, new_profile, normalize_profiles, now_text, runtime_config
from .subscription import install_filtered_config, refresh_subscription

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
        self.last_subscription_result: dict[str, Any] | None = None
        self.last_install_result: dict[str, Any] | None = None
        self.scan_cache: dict[str, dict[str, Any]] = {}
        self.runtime_status: dict[str, Any] = {
            "selected_node": "", "selector_choice": "", "mode": "unknown", "updated_at": None, "error": None
        }
        self.operation_status: dict[str, Any] = {
            "active": False,
            "kind": None,
            "profile_id": None,
            "profile_name": None,
            "stage": None,
            "message": None,
            "progress": 0,
            "started_at": None,
            "completed_at": None,
            "ok": None,
        }
        self.stop_event = threading.Event()
        with self.lock:
            config = self._managed_config(self.store.load())
            self.store.save(config)
            for profile in config["subscriptions"]:
                self._update_scan_cache(config, profile)
            self.runtime_status["selected_node"] = str(get_profile(config).get("selected_node") or "")

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

    def _update_scan_cache(self, config: dict[str, Any], profile: dict[str, Any]) -> None:
        candidate = runtime_config(config, profile)
        try:
            scan = scan_regions(candidate["subscription"]["last_source_path"], candidate)
        except Exception as exc:
            scan = {"error": str(exc), "node_count": 0, "region_counts": {}, "nodes": [], "regions": profile.get("regions", [])}
        self.scan_cache[profile["id"]] = scan

    def _operation_progress(self, stage: str, message: str, progress: int) -> None:
        with self.lock:
            self.operation_status.update({"stage": stage, "message": message, "progress": progress})

    def _run_operation(
        self,
        kind: str,
        profile_id: str,
        worker: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        if not self.operation_lock.acquire(blocking=False):
            return {"ok": False, "error": "已有操作正在执行，请稍候"}
        try:
            config = self.load_config()
            profile = get_profile(config, profile_id)
            with self.lock:
                self.operation_status = {
                    "active": True,
                    "kind": kind,
                    "profile_id": profile_id,
                    "profile_name": profile["name"],
                    "stage": "start",
                    "message": "正在准备",
                    "progress": 5,
                    "started_at": now_text(),
                    "completed_at": None,
                    "ok": None,
                }
            result = worker()
            ok = bool(result.get("ok"))
            with self.lock:
                self.operation_status.update({
                    "active": False,
                    "stage": "complete" if ok else "failed",
                    "message": "操作完成" if ok else str(result.get("error") or "操作失败"),
                    "progress": 100,
                    "completed_at": now_text(),
                    "ok": ok,
                })
            return result
        except Exception as exc:
            with self.lock:
                self.operation_status.update({
                    "active": False,
                    "stage": "failed",
                    "message": str(exc),
                    "completed_at": now_text(),
                    "ok": False,
                })
            raise
        finally:
            self.operation_lock.release()

    def _record_result(self, profile_id: str, result: dict[str, Any]) -> None:
        sanitized = sanitize_result_payload(result)
        with self.lock:
            config = self.load_config()
            try:
                get_profile(config, profile_id)["last_result"] = copy.deepcopy(sanitized)
            except KeyError:
                return
            self._save(config)

    def refresh_runtime_status(self) -> None:
        config = self.load_config()
        profile = get_profile(config)
        profile_id = profile["id"]
        try:
            status = selector_status(runtime_config(config, profile))
            selected = str(status.get("now") or profile.get("selected_node") or "")
            selector_choice = str(status.get("selected") or selected)
            mode = "manual" if selector_choice == selected else "automatic"
            payload = {
                "profile_id": profile_id,
                "selected_node": selected,
                "selector_choice": selector_choice,
                "mode": mode,
                "updated_at": now_text(),
                "error": None,
            }
        except RuntimeError as exc:
            mode = "unknown"
            payload = {
                "profile_id": profile_id,
                "selected_node": str(profile.get("selected_node") or ""),
                "selector_choice": str(profile.get("selected_node") or ""),
                "mode": "unknown",
                "updated_at": now_text(),
                "error": str(exc),
            }
        with self.lock:
            latest = self.load_config()
            if latest["active_subscription_id"] == profile_id:
                self.runtime_status = payload
                if mode == "manual" and selected and not self.operation_status.get("active"):
                    latest_profile = get_profile(latest, profile_id)
                    if latest_profile.get("selected_node") != selected:
                        latest_profile["selected_node"] = selected
                        self._save(latest)

    def state_payload(self) -> dict[str, Any]:
        config = self.load_config()
        profiles = copy.deepcopy(config["subscriptions"])
        for profile in profiles:
            if isinstance(profile.get("quota"), dict):
                profile["quota"].pop("raw", None)
        with self.lock:
            scans = copy.deepcopy(self.scan_cache)
            runtime_status = copy.deepcopy(self.runtime_status)
            operation = copy.deepcopy(self.operation_status)
        selected = str(runtime_status.get("selected_node") or get_profile(config).get("selected_node") or "")
        return {
            "config": {"active_subscription_id": config["active_subscription_id"], "subscriptions": profiles},
            "scans": scans,
            "selected_node": selected,
            "runtime_status": runtime_status,
            "operation": operation,
            "last_result": sanitize_result_payload(get_profile(config).get("last_result")),
            "last_subscription_result": sanitize_result_payload(self.last_subscription_result),
            "last_install_result": sanitize_result_payload(self.last_install_result),
        }

    def save_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if self.operation_status.get("active"):
                return {"ok": False, "error": "后台操作进行中，暂时不能编辑订阅"}
            config = self.load_config()
            profile_id = str(payload.get("id") or "")
            if profile_id:
                profile = get_profile(config, profile_id)
            else:
                profile = new_profile()
                config["subscriptions"].append(profile)
            previous_url = profile["source_url"]
            if "name" in payload:
                profile["name"] = str(payload.get("name") or "未命名订阅")[:80]
            if "source_url" in payload:
                source_url = str(payload.get("source_url") or "").strip()
                if urlparse(source_url).scheme not in {"http", "https"}:
                    raise ValueError("订阅地址必须使用 http 或 https")
                profile["source_url"] = source_url
            if "refresh_interval_seconds" in payload:
                profile["refresh_interval_seconds"] = max(300, int(payload["refresh_interval_seconds"]))
            if "latency_interval_seconds" in payload:
                profile["latency"]["interval_seconds"] = max(0, int(payload["latency_interval_seconds"]))
            source_changed = previous_url != profile["source_url"]
            if source_changed:
                candidate = runtime_config(config, profile)
                for key in ("cache_path", "last_source_path"):
                    try:
                        Path(candidate["subscription"][key]).unlink()
                    except FileNotFoundError:
                        pass
                profile["last_refresh_at"] = None
                profile["last_refresh_epoch"] = 0
                profile["last_refresh_attempt_epoch"] = 0
                profile["last_result"] = None
                profile["quota"] = {}
                profile["latency"]["last_test_at"] = None
                profile["latency"]["last_test_epoch"] = 0
                profile["latency"]["last_test_attempt_epoch"] = 0
                profile["latency"]["results"] = {}
                profile["selected_node"] = ""
                self.scan_cache[profile["id"]] = {
                    "node_count": 0, "region_counts": {}, "nodes": [], "regions": profile["regions"]
                }
            self._save(config)
            return {"ok": True, "profile_id": profile["id"]}

    def delete_profile(self, profile_id: str) -> dict[str, Any]:
        with self.lock:
            if self.operation_status.get("active"):
                return {"ok": False, "error": "后台操作进行中，暂时不能删除订阅"}
            config = self.load_config()
            if profile_id == config["active_subscription_id"]:
                return {"ok": False, "error": "当前激活订阅不能删除"}
            before = len(config["subscriptions"])
            config["subscriptions"] = [profile for profile in config["subscriptions"] if profile["id"] != profile_id]
            if len(config["subscriptions"]) == before:
                return {"ok": False, "error": "订阅不存在"}
            self._save(config)
            self.scan_cache.pop(profile_id, None)
            profiles_dir = Path(str(config["subscription"].get("profiles_dir") or "/data/subscriptions"))
            shutil.rmtree(profiles_dir / profile_id, ignore_errors=True)
            return {"ok": True}

    def save_regions(self, profile_id: str, enabled: list[str], excluded: list[str]) -> dict[str, Any]:
        def worker() -> dict[str, Any]:
            with self.lock:
                config = self.load_config()
                profile = get_profile(config, profile_id)
                profile["filter"]["enabled_regions"] = list(dict.fromkeys(map(str, enabled)))
                profile["filter"]["excluded_regions"] = list(dict.fromkeys(map(str, excluded)))
                active = profile_id == config["active_subscription_id"]
                self._save(config)
            return self._refresh_profile_locked(profile_id, install=active)

        return self._run_operation("apply", profile_id, worker)

    def _refresh_candidate(self, config: dict[str, Any], profile_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        self._operation_progress("refresh", "正在下载并过滤订阅", 25)
        profile, candidate = self._profile_config(config, profile_id)
        profile["last_refresh_attempt_epoch"] = int(time.time())
        with self.lock:
            self._save(config)
        updated, result = refresh_subscription(candidate)
        payload = result.to_dict()
        self.last_subscription_result = payload
        if result.ok:
            profile["regions"] = copy.deepcopy(updated["regions"])
            profile["filter"] = copy.deepcopy(updated["filter"])
            profile["last_refresh_at"] = result.generated_at
            profile["last_refresh_epoch"] = int(time.time())
            self._operation_progress("quota", "正在读取订阅额度", 40)
            info = payload.get("subscription_info") or {}
            if info.get("ok"):
                info.pop("raw", None)
                profile["quota"] = info
            with self.lock:
                self._update_scan_cache(config, profile)
        return config, payload, updated

    def refresh_profile(self, profile_id: str, install: bool | None = None) -> dict[str, Any]:
        return self._run_operation(
            "refresh",
            profile_id,
            lambda: self._refresh_profile_locked(profile_id, install),
        )

    def _refresh_profile_locked(self, profile_id: str, install: bool | None = None) -> dict[str, Any]:
        with self.lock:
            config = self.load_config()
            active = profile_id == config["active_subscription_id"]
        config, refresh_payload, candidate = self._refresh_candidate(config, profile_id)
        if not refresh_payload.get("ok"):
            result = {"ok": False, "stage": "refresh", "error": refresh_payload.get("error"), "refresh": refresh_payload}
            self._record_result(profile_id, result)
            return result
        with self.lock:
            self._save(config)
        if not (active if install is None else install):
            result = {"ok": True, "installed": False, "message": "订阅缓存已更新", "refresh": refresh_payload}
            self._record_result(profile_id, result)
            return result
        return self._install_candidate(config, profile_id, candidate, switching=False)

    def _install_candidate(self, config: dict[str, Any], profile_id: str, candidate: dict[str, Any], switching: bool) -> dict[str, Any]:
        self._operation_progress("install", "正在生成 OpenClash 配置", 50)
        output_path = Path(candidate["subscription"]["output_config_path"])
        runtime_path = Path(str(candidate["openclash"].get("runtime_config_path") or ""))
        previous = output_path.read_bytes() if output_path.exists() else (
            runtime_path.read_bytes() if runtime_path.is_file() else None
        )
        install = install_filtered_config(candidate)
        self.last_install_result = install.to_dict()
        if not install.ok:
            result = {"ok": False, "stage": "install", "error": install.error}
            self._record_result(profile_id, result)
            return result
        self._operation_progress("reload", "正在重载 OpenClash", 65)
        reload_result = self.reload_openclash(config) if install.changed else {"ok": True, "skipped": True}
        if install.changed and reload_result.get("ok"):
            time.sleep(max(1, int(config.get("automation", {}).get("settle_seconds", 3))))

        refresh = self.last_subscription_result or {}
        filter_result = refresh.get("filter_result") or {}
        kept_nodes = list(filter_result.get("kept_nodes") or [])
        profile = get_profile(config, profile_id)
        remembered = str(profile.get("selected_node") or "")
        if not switching and not remembered:
            cached_current = str(self.runtime_status.get("selected_node") or "")
            if cached_current in kept_nodes:
                remembered = cached_current

        latency_result: dict[str, Any] | None = None
        selection_reason = "restored"
        selected = remembered if remembered in kept_nodes else ""
        if switching and reload_result.get("ok"):
            self._operation_progress("latency", "正在验证候选节点连通性", 78)
            try:
                latency_result = test_latencies(candidate, candidate["subscription"]["last_source_path"])
            except RuntimeError as exc:
                latency_result = {"ok": False, "results": {}, "error": str(exc)}
            reachable = {
                name for name, delay in (latency_result.get("results") or {}).items()
                if isinstance(delay, (int, float)) and delay > 0
            }
            if selected not in reachable:
                selected = next((name for name in kept_nodes if name in reachable), "")
                selection_reason = "first_reachable"
            if not selected:
                return self._rollback_candidate(
                    config,
                    profile_id,
                    output_path,
                    previous,
                    reload_result,
                    install.changed,
                    "候选配置没有可连接节点，已恢复原配置",
                    {"ok": False, "error": "所有保留节点均不可达"},
                )
            with self.lock:
                latest = self.load_config()
                target = get_profile(latest, profile_id)
                target["latency"]["results"] = latency_result.get("results") or {}
                target["latency"]["last_test_at"] = now_text()
                target["latency"]["last_test_epoch"] = int(time.time())
                target["latency"]["last_test_attempt_epoch"] = target["latency"]["last_test_epoch"]
                self._save(latest)
                config = latest
                profile = target
        elif not selected:
            selected = kept_nodes[0] if kept_nodes else ""
            selection_reason = "first_kept"

        selection: dict[str, Any] = {
            "selected_node": selected,
            "previous_node": remembered or None,
            "reason": selection_reason,
            "changed_automatically": bool(remembered and selected != remembered),
        }
        if reload_result.get("ok") and selected:
            self._operation_progress("selection", "正在恢复该订阅的节点选择", 86)
            try:
                mihomo_select_node(candidate, selected)
            except RuntimeError as exc:
                return self._rollback_candidate(
                    config,
                    profile_id,
                    output_path,
                    previous,
                    reload_result,
                    install.changed,
                    "无法恢复节点选择，已恢复原配置",
                    {"ok": False, "error": str(exc)},
                )

        self._operation_progress("verify", "正在验证 OpenClash 运行状态", 92)
        verification = verify_running_state(candidate) if reload_result.get("ok") else {"ok": False, "error": "OpenClash 重载失败"}
        verified = bool(verification.get("ok")) and not verification.get("bad_nodes")
        if not reload_result.get("ok") or not verified:
            return self._rollback_candidate(
                config,
                profile_id,
                output_path,
                previous,
                reload_result,
                install.changed,
                "候选配置验证失败，已恢复原配置",
                verification,
            )
        if switching:
            with self.lock:
                config["active_subscription_id"] = profile_id
                get_profile(config, profile_id)["selected_node"] = selected
                self._save(config)
        elif selected:
            with self.lock:
                latest = self.load_config()
                get_profile(latest, profile_id)["selected_node"] = selected
                self._save(latest)
        with self.lock:
            self.runtime_status = {
                "profile_id": profile_id,
                "selected_node": selected,
                "selector_choice": selected,
                "mode": "manual",
                "updated_at": now_text(),
                "error": None,
            }
        result = {
            **filter_result,
            "ok": True,
            "changed": install.changed,
            "reload": reload_result,
            "verification": verification,
            "verified": True,
            "switched": switching,
            "selection": selection,
        }
        self._record_result(profile_id, result)
        return result

    def _rollback_candidate(
        self,
        config: dict[str, Any],
        profile_id: str,
        output_path: Path,
        previous: bytes | None,
        reload_result: dict[str, Any],
        config_changed: bool,
        error: str,
        verification: dict[str, Any],
    ) -> dict[str, Any]:
        self._operation_progress("rollback", "验证失败，正在恢复原配置", 96)
        restored = not config_changed
        if config_changed and previous is not None:
            tmp = output_path.with_suffix(output_path.suffix + ".rollback")
            tmp.write_bytes(previous)
            os.replace(tmp, output_path)
            restored = bool(self.reload_openclash(config).get("ok"))
            if restored:
                try:
                    active_profile = get_profile(config)
                    remembered = str(active_profile.get("selected_node") or "")
                    if remembered:
                        mihomo_select_node(runtime_config(config, active_profile), remembered)
                except (KeyError, RuntimeError):
                    pass
        result = {
            "ok": False,
            "stage": "verify",
            "error": error,
            "reload": reload_result,
            "verification": verification,
            "rollback_restored": restored,
        }
        self._record_result(profile_id, result)
        return result

    def activate_profile(self, profile_id: str) -> dict[str, Any]:
        def worker() -> dict[str, Any]:
            with self.lock:
                config = self.load_config()
                if profile_id == config["active_subscription_id"]:
                    return {"ok": True, "skipped": True, "message": "该订阅已在使用中"}
            config, refresh_payload, candidate = self._refresh_candidate(config, profile_id)
            if not refresh_payload.get("ok"):
                result = {"ok": False, "stage": "refresh", "error": refresh_payload.get("error")}
                self._record_result(profile_id, result)
                return result
            with self.lock:
                self._save(config)
            return self._install_candidate(config, profile_id, candidate, switching=True)

        return self._run_operation("activate", profile_id, worker)

    def test_latency(self, profile_id: str) -> dict[str, Any]:
        def worker() -> dict[str, Any]:
            with self.lock:
                config = self.load_config()
                if profile_id != config["active_subscription_id"]:
                    return {"ok": False, "error": "只能测试当前激活订阅"}
                profile, candidate = self._profile_config(config, profile_id)
            self._operation_progress("latency", "正在测试全部节点延迟", 45)
            with self.lock:
                config = self.load_config()
                profile = get_profile(config, profile_id)
                profile["latency"]["last_test_attempt_epoch"] = int(time.time())
                self._save(config)
            result = test_latencies(candidate, candidate["subscription"]["last_source_path"])
            with self.lock:
                config = self.load_config()
                profile = get_profile(config, profile_id)
                profile["latency"]["results"] = result["results"]
                profile["latency"]["last_test_at"] = now_text()
                profile["latency"]["last_test_epoch"] = int(time.time())
                profile["latency"]["last_test_attempt_epoch"] = profile["latency"]["last_test_epoch"]
                self._save(config)
            return {**result, "last_test_at": profile["latency"]["last_test_at"]}

        return self._run_operation("latency", profile_id, worker)

    def select_node(self, profile_id: str, node_name: str) -> dict[str, Any]:
        def worker() -> dict[str, Any]:
            with self.lock:
                config = self.load_config()
                if profile_id != config["active_subscription_id"]:
                    return {"ok": False, "error": "只能选择当前激活订阅的节点"}
                _, candidate = self._profile_config(config, profile_id)
            self._operation_progress("selection", "正在切换到指定节点", 60)
            result = mihomo_select_node(candidate, node_name)
            with self.lock:
                config = self.load_config()
                get_profile(config, profile_id)["selected_node"] = node_name
                self.runtime_status = {
                    "profile_id": profile_id,
                    "selected_node": node_name,
                    "selector_choice": node_name,
                    "mode": "manual",
                    "updated_at": now_text(),
                    "error": None,
                }
                self._save(config)
            return result

        return self._run_operation("select", profile_id, worker)

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
    while not state.stop_event.is_set():
        try:
            config = state.load_config()
            now = time.time()
            active_id = config["active_subscription_id"]
            for profile in config["subscriptions"]:
                profile_id = profile["id"]
                if not str(profile.get("source_url") or "").strip():
                    continue
                last_refresh = max(0, int(profile.get("last_refresh_attempt_epoch", 0) or 0))
                if now - last_refresh >= max(300, int(profile.get("refresh_interval_seconds", 3600))):
                    state.refresh_profile(profile_id, install=profile_id == active_id)
                latency_interval = max(0, int(profile.get("latency", {}).get("interval_seconds", 14400)))
                last_latency = max(0, int(profile.get("latency", {}).get("last_test_attempt_epoch", 0) or 0))
                has_source_cache = bool(profile.get("last_refresh_at")) and Path(
                    runtime_config(config, profile)["subscription"]["last_source_path"]
                ).is_file()
                if profile_id == active_id and has_source_cache and latency_interval and now - last_latency >= latency_interval:
                    state.test_latency(profile_id)
            state.stop_event.wait(max(10, int(config["automation"].get("poll_seconds", 30))))
        except Exception:
            LOG.exception("Automation loop failed")
            state.stop_event.wait(30)


def runtime_status_loop(state: AppState) -> None:
    while not state.stop_event.is_set():
        try:
            state.refresh_runtime_status()
        except Exception:
            LOG.exception("Runtime status refresh failed")
        state.stop_event.wait(10)


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
    threading.Thread(target=runtime_status_loop, args=(state,), daemon=True).start()
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
