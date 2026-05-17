from __future__ import annotations

import argparse
import json
import logging
import subprocess
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import ConfigStore
from .filtering import apply_filter, scan_regions


LOG = logging.getLogger("openclash-region-filter")


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OpenClash 地区过滤</title>
  <style>
    :root { color-scheme: light dark; --accent: #0f766e; --green: #16a34a; --line: #d7dee6; --muted: #687585; --soft: #f1f5f9; }
    body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f7f9fb; color: #111827; }
    header { padding: 18px 22px; background: #ffffff; border-bottom: 1px solid var(--line); display: flex; align-items: center; justify-content: space-between; gap: 16px; }
    h1 { margin: 0; font-size: 20px; font-weight: 650; }
    main { padding: 18px 22px 32px; max-width: 1180px; margin: 0 auto; }
    section { background: #fff; border: 1px solid var(--line); border-radius: 8px; padding: 16px; margin-bottom: 16px; }
    h2 { margin: 0 0 12px; font-size: 16px; }
    .settings-grid { display: grid; grid-template-columns: repeat(12, 1fr); gap: 12px 14px; align-items: end; }
    .field { display: grid; gap: 6px; grid-column: span 4; min-width: 0; }
    .field.wide { grid-column: span 8; }
    .field.compact { grid-column: span 2; }
    .field.full { grid-column: 1 / -1; }
    .input-row { display: flex; gap: 8px; align-items: center; min-width: 0; }
    .input-row input, .input-row select { flex: 1 1 auto; min-width: 0; }
    label { font-size: 13px; color: #334155; }
    input[type="text"], input[type="number"], input[type="password"], select { border: 1px solid var(--line); border-radius: 6px; padding: 9px 10px; font-size: 14px; width: 100%; box-sizing: border-box; background: #fff; color: #111827; }
    button { border: 1px solid #0f766e; background: var(--accent); color: #fff; border-radius: 6px; padding: 9px 12px; font-size: 14px; cursor: pointer; }
    button.secondary { background: #fff; color: var(--accent); }
    button.neutral { border-color: #a8b3c2; background: #fff; color: #334155; }
    button:disabled { opacity: .55; cursor: wait; }
    .settings-actions { display: flex; flex-wrap: wrap; gap: 12px 16px; align-items: center; padding-top: 4px; }
    .settings-actions label { white-space: nowrap; }
    .hint { color: var(--muted); font-size: 12px; line-height: 1.45; margin: 0; }
    .regions { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; align-items: stretch; }
    .region { border: 1px solid var(--line); border-radius: 8px; padding: 12px; display: grid; grid-template-rows: 56px 86px; gap: 10px; min-height: 164px; }
    .region-top { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 12px; align-items: start; }
    .region-title { min-width: 0; }
    .region-title strong { display: block; font-size: 16px; line-height: 1.25; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .region-actions { display: grid; justify-items: end; gap: 5px; min-width: 62px; }
    .region-state { color: var(--muted); font-size: 12px; line-height: 1.2; white-space: nowrap; }
    button.region-toggle { width: 54px; height: 30px; border: 0; border-radius: 999px; padding: 3px; background: #cbd5e1; display: inline-flex; align-items: center; justify-content: flex-start; transition: background .16s ease; }
    button.region-toggle::after { content: ""; width: 24px; height: 24px; border-radius: 50%; background: #fff; box-shadow: 0 1px 3px rgba(15, 23, 42, .2); transition: transform .16s ease; }
    button.region-toggle.on { background: var(--green); }
    button.region-toggle.on::after { transform: translateX(24px); }
    .count { color: var(--muted); font-size: 12px; }
    .bad { color: #b42318; }
    .ok { color: #087443; }
    pre { white-space: pre-wrap; word-break: break-word; background: #0f172a; color: #e2e8f0; border-radius: 8px; padding: 12px; max-height: 360px; overflow: auto; font-size: 12px; }
    .nodes { color: var(--muted); font-size: 12px; line-height: 1.45; height: 86px; overflow: auto; }
    .row { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
    .pill { border: 1px solid var(--line); border-radius: 999px; padding: 4px 8px; font-size: 12px; background: #f8fafc; }
    @media (max-width: 760px) {
      .field, .field.wide, .field.compact { grid-column: 1 / -1; }
      header { align-items: flex-start; flex-direction: column; }
    }
    @media (prefers-color-scheme: dark) {
      body { background: #0b1220; color: #e5e7eb; }
      header, section { background: #111827; }
      input[type="text"], input[type="number"], input[type="password"], select, button.secondary, button.neutral { background: #0b1220; color: #e5e7eb; }
      label, .count, .nodes, .region-state { color: #9ca3af; }
      .pill { background: #0b1220; }
    }
  </style>
</head>
<body>
  <header>
    <h1>OpenClash 地区过滤</h1>
    <div class="row">
      <span id="status" class="pill">加载中</span>
      <button class="secondary" onclick="refresh()">刷新</button>
      <button onclick="applyNow()">立即过滤并应用</button>
    </div>
  </header>
  <main>
    <section>
      <h2>运行设置</h2>
      <div class="settings-grid">
        <div class="field wide">
          <label>配置文件路径</label>
          <div class="input-row">
            <input id="config_path" type="text">
            <select id="config_file_select" onchange="selectConfigFile()"></select>
            <button class="neutral" onclick="loadConfigFiles()" type="button">浏览</button>
          </div>
        </div>
        <div class="field compact"><label>轮询间隔（秒）</label><input id="poll_seconds" type="number" min="5"></div>
        <div class="field wide">
          <label>重载命令</label>
          <div class="input-row">
            <input id="reload_command" type="text">
            <button class="neutral" onclick="reloadOpenClash()" type="button">一键重载</button>
          </div>
        </div>
        <div class="field">
          <label>运行态验证 API</label>
          <div class="input-row">
            <input id="dashboard_api" type="text">
            <button class="neutral" onclick="openDashboardApi()" type="button">打开</button>
          </div>
        </div>
        <div class="field"><label>验证密钥</label><input id="dashboard_secret" type="password" placeholder="可留空"></div>
        <div class="field full"><p class="hint">运行态验证 API 用于过滤后检查 OpenClash 当前运行的节点列表；不影响过滤本身。通常保持默认即可，只有开启验证且 OpenClash 设置了外部控制密钥时才需要填写验证密钥。</p></div>
      </div>
      <div class="settings-actions">
        <label><input id="automation_enabled" type="checkbox"> 自动监听配置变化</label>
        <label><input id="allow_unknown" type="checkbox"> 允许未知地区节点</label>
        <label><input id="verify_api" type="checkbox"> 应用后运行态验证</label>
        <button class="secondary" onclick="saveSettings()">保存设置</button>
      </div>
    </section>

    <section>
      <h2>地区规则</h2>
      <div id="regions" class="regions"></div>
    </section>

    <section>
      <h2>最近结果</h2>
      <pre id="result">暂无</pre>
    </section>
  </main>
<script>
let state = null;

function regionNode(region) {
  const id = region.id;
  const count = state.scan.region_counts[id] || 0;
  const nodes = state.scan.nodes.filter(n => n.region_id === id).map(n => n.name);
  const enabled = state.config.filter.enabled_regions.includes(id) && !state.config.filter.excluded_regions.includes(id);
  const stateText = enabled ? "已启用" : "已禁用";
  return `<div class="region">
    <div class="region-top">
      <div class="region-title"><strong>${region.label}</strong><span class="count">${count} 个节点</span></div>
      <div class="region-actions">
        <button type="button" class="region-toggle ${enabled ? "on" : ""}" data-region="${id}" data-enabled="${enabled}" aria-label="${region.label}${stateText}" aria-pressed="${enabled}" onclick="toggleRegion(this)"></button>
        <span class="region-state" data-region-state="${id}">${stateText}</span>
      </div>
    </div>
    <div class="nodes">${nodes.slice(0, 12).join("<br>") || "当前未发现节点"}</div>
  </div>`;
}

function toggleRegion(button) {
  const enabled = button.dataset.enabled !== "true";
  button.dataset.enabled = String(enabled);
  button.classList.toggle("on", enabled);
  button.setAttribute("aria-pressed", String(enabled));
  const label = button.closest(".region").querySelector("strong").textContent;
  const stateLabel = document.querySelector(`[data-region-state="${button.dataset.region}"]`);
  const stateText = enabled ? "已启用" : "已禁用";
  stateLabel.textContent = stateText;
  button.setAttribute("aria-label", `${label}${stateText}`);
}

async function api(path, options) {
  const res = await fetch(path, options);
  const text = await res.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch (_) { data = {raw: text}; }
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

async function refresh() {
  document.getElementById("status").textContent = "刷新中";
  state = await api("/api/state");
  document.getElementById("config_path").value = state.config.openclash.config_path || "";
  document.getElementById("reload_command").value = state.config.openclash.reload_command || "";
  document.getElementById("dashboard_api").value = state.config.openclash.dashboard_api || "";
  document.getElementById("dashboard_secret").value = state.config.openclash.dashboard_secret || "";
  document.getElementById("poll_seconds").value = state.config.automation.poll_seconds || 30;
  document.getElementById("automation_enabled").checked = !!state.config.automation.enabled;
  document.getElementById("allow_unknown").checked = !!state.config.filter.allow_unknown;
  document.getElementById("verify_api").checked = !!state.config.openclash.verify_api;
  document.getElementById("regions").innerHTML = state.config.regions.map(regionNode).join("");
  document.getElementById("result").textContent = JSON.stringify(state.last_result || state.scan, null, 2);
  document.getElementById("status").textContent = state.config.automation.enabled ? "自动监听中" : "自动监听关闭";
  await loadConfigFiles(false);
}

async function loadConfigFiles(showStatus = true) {
  if (showStatus) document.getElementById("status").textContent = "读取配置目录";
  const current = document.getElementById("config_path").value;
  const data = await api(`/api/config-files?path=${encodeURIComponent(current)}`);
  const select = document.getElementById("config_file_select");
  select.innerHTML = `<option value="">选择配置文件</option>` + data.files.map(file => {
    const selected = file.path === current ? "selected" : "";
    return `<option value="${file.path}" ${selected}>${file.name}</option>`;
  }).join("");
  if (showStatus) document.getElementById("status").textContent = "配置目录已读取";
}

function selectConfigFile() {
  const value = document.getElementById("config_file_select").value;
  if (value) document.getElementById("config_path").value = value;
}

async function reloadOpenClash() {
  document.getElementById("status").textContent = "重载中";
  await saveSettings();
  const result = await api("/api/reload", {method: "POST"});
  document.getElementById("result").textContent = JSON.stringify(result, null, 2);
  document.getElementById("status").textContent = result.ok ? "重载完成" : "重载失败";
}

function openDashboardApi() {
  const raw = document.getElementById("dashboard_api").value.trim();
  if (!raw) return;
  try {
    const url = new URL(raw, window.location.href);
    if (["127.0.0.1", "localhost", "0.0.0.0"].includes(url.hostname)) {
      url.hostname = window.location.hostname;
    }
    window.open(url.toString(), "_blank", "noopener");
  } catch (_) {
    document.getElementById("result").textContent = "运行态验证 API 地址格式不正确";
  }
}

function collectSettings() {
  const enabled = [];
  const excluded = [];
  document.querySelectorAll("#regions .region-toggle").forEach(el => {
    if (el.dataset.enabled === "true") {
      enabled.push(el.dataset.region);
    } else {
      excluded.push(el.dataset.region);
    }
  });
  return {
    openclash: {
      config_path: document.getElementById("config_path").value,
      reload_command: document.getElementById("reload_command").value,
      dashboard_api: document.getElementById("dashboard_api").value,
      dashboard_secret: document.getElementById("dashboard_secret").value,
      verify_api: document.getElementById("verify_api").checked
    },
    automation: {
      enabled: document.getElementById("automation_enabled").checked,
      poll_seconds: Number(document.getElementById("poll_seconds").value || 30)
    },
    filter: {
      allow_unknown: document.getElementById("allow_unknown").checked,
      enabled_regions: enabled,
      excluded_regions: excluded
    }
  };
}

async function saveSettings() {
  document.getElementById("status").textContent = "保存中";
  await api("/api/settings", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(collectSettings())
  });
  await refresh();
}

async function applyNow() {
  document.getElementById("status").textContent = "应用中";
  await saveSettings();
  const result = await api("/api/apply", {method: "POST"});
  document.getElementById("result").textContent = JSON.stringify(result, null, 2);
  await refresh();
}

refresh().catch(err => {
  document.getElementById("status").textContent = "加载失败";
  document.getElementById("result").textContent = String(err);
});
</script>
</body>
</html>
"""


class AppState:
    def __init__(self, store: ConfigStore):
        self.store = store
        self.lock = threading.Lock()
        self.last_result: dict[str, Any] | None = None
        self.stop_event = threading.Event()

    def load_config(self) -> dict[str, Any]:
        with self.lock:
            return self.store.load()

    def save_config(self, patch: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            config = self.store.load()
            for section in ("openclash", "automation", "filter"):
                if section in patch and isinstance(patch[section], dict):
                    config[section].update(patch[section])
            if "regions" in patch and isinstance(patch["regions"], list):
                config["regions"] = patch["regions"]
            self.store.save(config)
            return config

    def apply(self) -> dict[str, Any]:
        with self.lock:
            config = self.store.load()
        result = apply_filter(config)
        with self.lock:
            self.last_result = result.to_dict()
        return result.to_dict()

    def reload_openclash(self) -> dict[str, Any]:
        with self.lock:
            config = self.store.load()
        reload_command = str(config["openclash"].get("reload_command", "")).strip()
        if not reload_command:
            return {"ok": False, "error": "reload_command is empty"}

        completed = subprocess.run(
            reload_command,
            shell=True,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=120,
        )
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "command": reload_command,
            "output": completed.stdout.strip(),
        }


def list_config_files(config_path: str) -> dict[str, Any]:
    path = Path(config_path or "/etc/openclash/config")
    directory = path if path.is_dir() else path.parent
    files = []

    if directory.exists() and directory.is_dir():
        for item in sorted(directory.iterdir(), key=lambda p: p.name.lower()):
            if item.is_file() and item.suffix.lower() in {".yaml", ".yml"}:
                files.append({"name": item.name, "path": str(item)})

    return {"directory": str(directory), "files": files}


def automation_loop(state: AppState) -> None:
    last_mtime: float | None = None
    while not state.stop_event.is_set():
        try:
            config = state.load_config()
            path = Path(config["openclash"]["config_path"])
            automation = config.get("automation", {})
            poll_seconds = max(5, int(automation.get("poll_seconds", 30)))

            if not automation.get("enabled"):
                state.stop_event.wait(poll_seconds)
                continue

            if not path.exists():
                LOG.warning("Config file does not exist: %s", path)
                state.stop_event.wait(poll_seconds)
                continue

            mtime = path.stat().st_mtime
            if last_mtime is None:
                last_mtime = mtime
                if automation.get("apply_on_start"):
                    time.sleep(float(automation.get("settle_seconds", 3)))
                    state.apply()
                    last_mtime = path.stat().st_mtime
            elif mtime != last_mtime:
                time.sleep(float(automation.get("settle_seconds", 3)))
                state.apply()
                last_mtime = path.stat().st_mtime

            state.stop_event.wait(poll_seconds)
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
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            body = INDEX_HTML.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/state":
            config = self.server.app_state.load_config()
            try:
                scan = scan_regions(config["openclash"]["config_path"], config)
            except Exception as exc:
                scan = {"error": str(exc), "node_count": 0, "region_counts": {}, "nodes": []}
            self.send_json(
                {
                    "config": config,
                    "scan": scan,
                    "last_result": self.server.app_state.last_result,
                }
            )
            return
        if path == "/api/config-files":
            query = parse_qs(parsed.query)
            requested_path = (query.get("path") or [""])[0]
            if not requested_path:
                config = self.server.app_state.load_config()
                requested_path = str(config["openclash"].get("config_path", ""))
            self.send_json(list_config_files(requested_path))
            return
        self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/settings":
            config = self.server.app_state.save_config(self.read_json())
            self.send_json({"ok": True, "config": config})
            return
        if path == "/api/apply":
            self.send_json(self.server.app_state.apply())
            return
        if path == "/api/reload":
            try:
                self.send_json(self.server.app_state.reload_openclash())
            except subprocess.TimeoutExpired as exc:
                self.send_json(
                    {"ok": False, "error": f"reload command timed out: {exc}"},
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            return
        self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)


class RegionFilterServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], app_state: AppState):
        super().__init__(address, Handler)
        self.app_state = app_state


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenClash local region filter")
    parser.add_argument("--config", default="/data/config.json", help="service config JSON path")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--once", action="store_true", help="apply filter once and exit")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    state = AppState(ConfigStore(args.config))

    if args.once:
        print(json.dumps(state.apply(), ensure_ascii=False, indent=2))
        return 0

    worker = threading.Thread(target=automation_loop, args=(state,), daemon=True)
    worker.start()

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
