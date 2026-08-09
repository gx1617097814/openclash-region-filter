from __future__ import annotations

import argparse
import copy
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
from .defaults import DEFAULT_CONFIG
from .filtering import apply_filter, scan_regions, verify_running_state
from .subscription import fetch_subscription_info, install_filtered_config, refresh_subscription


LOG = logging.getLogger("openclash-region-filter")


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OpenClash 地区过滤</title>
  <style>
    :root { color-scheme: light dark; --accent: #0f766e; --green: #16a34a; --line: #d7dee6; --muted: #687585; --soft: #f1f5f9; }
    html, body { width: 100%; max-width: 100%; overflow-x: hidden; }
    body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f7f9fb; color: #111827; }
    header { padding: 18px 22px; background: #ffffff; border-bottom: 1px solid var(--line); display: flex; align-items: center; justify-content: space-between; gap: 16px; }
    h1 { margin: 0; font-size: 20px; font-weight: 650; }
    main { box-sizing: border-box; width: 100%; padding: 18px 22px 32px; max-width: 1404px; margin: 0 auto; min-width: 0; }
    section { box-sizing: border-box; width: 100%; background: #fff; border: 1px solid var(--line); border-radius: 8px; padding: 16px; margin-bottom: 16px; min-width: 0; }
    h2 { margin: 0; font-size: 16px; }
    .section-head { display: grid; gap: 6px; margin-bottom: 16px; }
    .layout-row { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 16px; align-items: stretch; margin-bottom: 16px; }
    .layout-row section { margin-bottom: 0; min-width: 0; }
    .main-row section { height: 620px; }
    .subscription-section { display: grid; align-content: start; gap: 16px; }
    .quota-section { display: grid; grid-template-rows: auto minmax(0, 1fr); gap: 16px; align-content: start; }
    .regions-section { overflow: auto; }
    .source-row { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; gap: 10px; align-items: center; }
    .source-row input { min-width: 0; }
    label { font-size: 13px; color: #334155; }
    input[type="text"] { border: 1px solid var(--line); border-radius: 6px; padding: 9px 10px; font-size: 14px; width: 100%; height: 40px; box-sizing: border-box; background: #fff; color: #111827; }
    button { border: 1px solid #0f766e; background: var(--accent); color: #fff; border-radius: 6px; padding: 9px 12px; min-height: 40px; font-size: 14px; cursor: pointer; white-space: nowrap; transition: background .16s ease, border-color .16s ease, color .16s ease, opacity .16s ease; }
    button.secondary { background: #fff; color: var(--accent); }
    button:disabled { opacity: .55; cursor: wait; }
    button.is-success { border-color: #16a34a; background: #16a34a; color: #fff; opacity: 1; }
    button.is-error { border-color: #dc2626; background: #dc2626; color: #fff; opacity: 1; }
    #feedback { position: fixed; top: 76px; right: 22px; z-index: 20; display: grid; gap: 8px; width: min(330px, calc(100vw - 44px)); pointer-events: none; }
    .notice { border: 1px solid var(--line); border-left: 4px solid var(--accent); border-radius: 6px; background: #fff; color: #111827; box-shadow: 0 8px 24px rgba(15, 23, 42, .12); padding: 9px 12px; font-size: 13px; line-height: 1.4; transform: translateY(-4px); opacity: 0; animation: notice-in .18s ease forwards; }
    .notice.ok { border-left-color: #16a34a; }
    .notice.bad { border-left-color: #dc2626; }
    .notice.info { border-left-color: #2563eb; }
    @keyframes notice-in { to { transform: translateY(0); opacity: 1; } }
    .hint { color: var(--muted); font-size: 12px; line-height: 1.45; margin: 0; }
    .quota { border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; background: #f8fafc; display: grid; gap: 8px; }
    .quota-main { display: flex; justify-content: space-between; gap: 12px; align-items: baseline; font-size: 13px; color: #334155; min-width: 0; flex-wrap: wrap; }
    .quota-main strong { font-size: 15px; color: #111827; }
    .quota-bar { height: 9px; overflow: hidden; border-radius: 999px; background: #e2e8f0; }
    .quota-fill { height: 100%; width: 0%; background: #16a34a; border-radius: inherit; transition: width .2s ease; }
    .quota-meta { display: flex; flex-wrap: wrap; gap: 8px 14px; color: var(--muted); font-size: 12px; }
    .regions { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(220px, 100%), 1fr)); gap: 12px; align-items: stretch; min-width: 0; }
    .region { border: 1px solid var(--line); border-radius: 8px; padding: 12px; display: grid; grid-template-rows: 48px 86px; gap: 8px; min-height: 154px; }
    .region-top { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 12px; align-items: start; }
    .region-title { min-width: 0; }
    .region-title strong { display: block; font-size: 16px; line-height: 1.25; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .region-actions { display: grid; justify-items: end; gap: 2px; min-width: 52px; }
    .region-state { color: var(--muted); font-size: 12px; line-height: 1.1; white-space: nowrap; }
    button.region-toggle { width: 48px; height: 24px; min-height: 24px; border: 0; border-radius: 999px; padding: 2px; background: #cbd5e1; display: inline-flex; align-items: center; justify-content: flex-start; transition: background .16s ease; }
    button.region-toggle::after { content: ""; width: 20px; height: 20px; border-radius: 50%; background: #fff; box-shadow: 0 1px 3px rgba(15, 23, 42, .2); transition: transform .16s ease; }
    button.region-toggle.on { background: var(--green); }
    button.region-toggle.on::after { transform: translateX(24px); }
    .count { color: var(--muted); font-size: 12px; }
    .bad { color: #b42318; }
    .ok { color: #087443; }
    .result-section { display: grid; grid-template-rows: auto auto minmax(0, 1fr); gap: 10px; min-height: 0; overflow: hidden; }
    .result-summary { border: 1px solid var(--line); border-radius: 8px; padding: 12px; background: #f8fafc; display: grid; gap: 10px; align-content: start; }
    .result-title { display: flex; justify-content: space-between; gap: 12px; align-items: center; min-width: 0; flex-wrap: wrap; }
    .result-title strong { font-size: 14px; }
    .result-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 16px; }
    .result-item { display: grid; gap: 3px; min-width: 0; }
    .result-item span { color: var(--muted); font-size: 12px; }
    .result-item strong { font-size: 13px; overflow-wrap: anywhere; }
    .result-json { box-sizing: border-box; width: 100%; min-height: 0; margin: 0; white-space: pre-wrap; word-break: break-word; overflow: auto; background: #0f172a; color: #e2e8f0; border-radius: 8px; padding: 12px; font-size: 12px; }
    .nodes { color: var(--muted); font-size: 12px; line-height: 1.45; height: 86px; overflow: auto; }
    .row { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
    .header-actions { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; justify-content: flex-end; }
    .pill { border: 1px solid var(--line); border-radius: 999px; padding: 4px 8px; font-size: 12px; background: #f8fafc; }
    @media (max-width: 980px) {
      .layout-row { grid-template-columns: 1fr; }
      .main-row section { height: auto; }
      .result-section { height: 500px !important; }
    }
    @media (max-width: 860px) {
      .source-row, .result-grid { grid-template-columns: 1fr; }
      main { padding-left: 14px; padding-right: 14px; }
      header { align-items: flex-start; flex-direction: column; }
      .header-actions { justify-content: flex-start; }
      #feedback { top: 92px; width: calc(100vw - 44px); }
    }
    @media (prefers-color-scheme: dark) {
      body { background: #0b1220; color: #e5e7eb; }
      header, section { background: #111827; }
      input[type="text"], button.secondary, .notice, .quota, .result-summary { background: #0b1220; color: #e5e7eb; }
      .quota-main, .quota-main strong { color: #e5e7eb; }
      .quota-bar { background: #1f2937; }
      label, .count, .nodes, .region-state { color: #9ca3af; }
      .pill { background: #0b1220; }
    }
  </style>
</head>
<body>
  <header>
    <h1>OpenClash 地区过滤</h1>
    <div class="header-actions">
      <span id="status" class="pill">加载中</span>
      <div id="feedback" aria-live="polite"></div>
    </div>
  </header>
  <main>
    <div class="layout-row top-row">
      <section class="subscription-section">
        <div class="section-head">
          <h2>订阅设置</h2>
          <p class="hint">填写原始 Clash/OpenClash YAML 订阅。系统每小时自动更新一次，也可以随时手动更新。</p>
        </div>
        <div class="source-row">
          <input id="source_url" type="text" inputmode="url" placeholder="https://example.com/clash.yaml" aria-label="订阅链接">
          <button class="secondary" onclick="updateSubscription(this)" type="button">立即更新订阅</button>
          <button onclick="saveSettings(this)" type="button">保存并应用</button>
        </div>
      </section>

      <section class="quota-section">
        <h2>订阅额度</h2>
        <div id="quota" class="quota">
          <div class="quota-main"><strong>读取中</strong><span>--</span></div>
          <div class="quota-bar"><div class="quota-fill"></div></div>
          <div class="quota-meta"><span>到期：--</span><span>检查：--</span></div>
        </div>
      </section>
    </div>

    <div class="layout-row main-row">
      <section class="regions-section">
        <div class="section-head">
          <h2>地区规则</h2>
          <p class="hint">只显示当前订阅实际出现的地区；无法识别地区的节点归入“其他地区”。调整后点击上方“保存并应用”。</p>
        </div>
        <div id="regions" class="regions"></div>
      </section>

      <section class="result-section">
        <h2>最近结果</h2>
        <div id="result" class="result-summary">暂无运行结果</div>
        <pre id="result_json" class="result-json">暂无详细结果</pre>
      </section>
    </div>
  </main>
<script>
let state = null;
let noticeTimer = null;

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[char]);
}

function notice(message, type = "info") {
  const host = document.getElementById("feedback");
  if (!host) return;
  host.innerHTML = `<div class="notice ${type}">${message}</div>`;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { host.innerHTML = ""; }, 3200);
}

function buttonText(button, text) {
  if (!button) return;
  if (!button.dataset.originalText) button.dataset.originalText = button.textContent;
  button.textContent = text;
}

function restoreButton(button) {
  if (!button) return;
  button.disabled = false;
  button.classList.remove("is-success", "is-error");
  if (button.dataset.originalText) button.textContent = button.dataset.originalText;
}

async function withFeedback(button, labels, task) {
  if (!button) return task();
  button.disabled = true;
  button.classList.remove("is-success", "is-error");
  buttonText(button, labels.busy || "处理中");
  notice(labels.busy || "处理中", "info");
  try {
    const result = await task();
    button.classList.add("is-success");
    buttonText(button, labels.success || "完成");
    notice(labels.success || "操作完成", "ok");
    setTimeout(() => restoreButton(button), 1100);
    return result;
  } catch (err) {
    button.classList.add("is-error");
    buttonText(button, labels.error || "失败");
    notice(`${labels.error || "操作失败"}：${err.message || err}`, "bad");
    document.getElementById("result").textContent = String(err);
    setTimeout(() => restoreButton(button), 1800);
    return null;
  }
}

function visibleRegions() {
  return state.config.regions.filter(region => (state.scan.region_counts[region.id] || 0) > 0);
}

function regionNode(region) {
  const id = region.id;
  const count = state.scan.region_counts[id] || 0;
  const nodes = state.scan.nodes.filter(n => n.region_id === id).map(n => n.name);
  const defaultEnabled = !!region.default_enabled && !!state.config.filter.allow_unknown;
  const enabled = (state.config.filter.enabled_regions.includes(id) || defaultEnabled) && !state.config.filter.excluded_regions.includes(id);
  const stateText = enabled ? "已启用" : "已禁用";
  return `<div class="region">
    <div class="region-top">
      <div class="region-title"><strong>${escapeHtml(region.label)}</strong><span class="count">${count} 个节点</span></div>
      <div class="region-actions">
        <button type="button" class="region-toggle ${enabled ? "on" : ""}" data-region="${escapeHtml(id)}" data-enabled="${enabled}" aria-label="${escapeHtml(region.label)}${stateText}" aria-pressed="${enabled}" onclick="toggleRegion(this)"></button>
        <span class="region-state" data-region-state="${escapeHtml(id)}">${stateText}</span>
      </div>
    </div>
    <div class="nodes">${nodes.slice(0, 12).map(escapeHtml).join("<br>")}</div>
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
  notice(`${label}${stateText}，点击“保存并应用”后生效`, "info");
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
  document.getElementById("status").textContent = "同步状态中";
  state = await api("/api/state");
  const sourceInput = document.getElementById("source_url");
  if (document.activeElement !== sourceInput) {
    sourceInput.value = state.config.subscription.source_url || "";
  }
  const regions = visibleRegions();
  document.getElementById("regions").innerHTML = regions.length
    ? regions.map(regionNode).join("")
    : '<p class="hint">订阅中暂未发现可识别节点。填写订阅链接并保存后会自动读取。</p>';
  renderQuota(state.subscription_info);
  renderResult(state.last_result, state.last_subscription_result, state.scan);
  document.getElementById("status").textContent = state.config.subscription.source_url ? "自动运行中" : "等待订阅链接";
}

function renderQuota(info) {
  const quota = document.getElementById("quota");
  if (!quota) return;
  if (!state.config.subscription.source_url) {
    quota.innerHTML = `<div class="quota-main"><strong>填写订阅后显示额度</strong><span>--</span></div>
      <div class="quota-bar"><div class="quota-fill" style="width:0%"></div></div>
      <div class="quota-meta"><span>到期：--</span><span>检查：--</span></div>`;
    return;
  }
  if (!info || !info.ok) {
    quota.innerHTML = `<div class="quota-main"><strong>订阅未提供额度信息</strong><span>--</span></div>
      <div class="quota-bar"><div class="quota-fill" style="width:0%"></div></div>
      <div class="quota-meta"><span>到期：--</span><span>检查：--</span></div>`;
    return;
  }
  const percent = info.percent_remaining == null ? 0 : Math.max(0, Math.min(100, Number(info.percent_remaining)));
  quota.innerHTML = `<div class="quota-main"><strong>剩余 ${escapeHtml(info.remaining_text)}</strong><span>${percent}%</span></div>
    <div class="quota-bar"><div class="quota-fill" style="width:${percent}%"></div></div>
    <div class="quota-meta">
      <span>已用：${escapeHtml(info.used_text)}</span>
      <span>总量：${escapeHtml(info.total_text)}</span>
      <span>到期：${escapeHtml(info.expire_text)}</span>
      <span>剩余天数：${escapeHtml(info.days_left ?? "--")}</span>
      <span>检查：${escapeHtml(info.checked_at || "--")}</span>
    </div>`;
}

function renderResult(result, subscriptionResult, scan) {
  const host = document.getElementById("result");
  const jsonHost = document.getElementById("result_json");
  jsonHost.textContent = JSON.stringify(result || subscriptionResult || scan || {}, null, 2);
  if (!result) {
    const count = Number(scan && scan.node_count || 0);
    host.innerHTML = `<div class="result-title"><strong>等待首次应用</strong><span class="pill">${count} 个源节点</span></div>
      <p class="hint">填写订阅链接或调整地区后，点击“保存并应用”。</p>`;
    return;
  }

  const ok = !!result.ok;
  const kept = Array.isArray(result.kept_nodes) ? result.kept_nodes.length : 0;
  const removed = Array.isArray(result.removed_nodes) ? result.removed_nodes.length : 0;
  const verification = result.verification || {};
  const verified = result.verified === true && !(verification.bad_nodes || []).length;
  const generatedAt = (result.refresh && result.refresh.generated_at)
    || (subscriptionResult && subscriptionResult.generated_at)
    || "--";
  const reloadText = result.reload && result.reload.skipped ? "配置无变化，无需重载" : (result.reload && result.reload.ok ? "已自动重载" : "--");
  const verifyText = verified ? "已通过" : (result.verified === false ? "未通过" : "--");
  const error = result.error ? `<p class="bad">${escapeHtml(result.error)}</p>` : "";
  host.innerHTML = `<div class="result-title"><strong>${ok ? "应用成功" : "应用失败"}</strong><span class="pill ${ok ? "ok" : "bad"}">${ok ? "正常" : "需处理"}</span></div>
    <div class="result-grid">
      <div class="result-item"><span>保留节点</span><strong>${kept}</strong></div>
      <div class="result-item"><span>排除节点</span><strong>${removed}</strong></div>
      <div class="result-item"><span>OpenClash</span><strong>${reloadText}</strong></div>
      <div class="result-item"><span>运行验证</span><strong>${verifyText}</strong></div>
      <div class="result-item"><span>最近更新</span><strong>${escapeHtml(generatedAt)}</strong></div>
      <div class="result-item"><span>自动刷新</span><strong>已开启</strong></div>
    </div>${error}`;
}

function collectSettings() {
  const enabled = new Set(state.config.filter.enabled_regions || []);
  const excluded = new Set(state.config.filter.excluded_regions || []);
  document.querySelectorAll("#regions .region-toggle").forEach(el => {
    if (el.dataset.enabled === "true") {
      enabled.add(el.dataset.region);
      excluded.delete(el.dataset.region);
    } else {
      enabled.delete(el.dataset.region);
      excluded.add(el.dataset.region);
    }
  });
  return {
    regions: state.config.regions,
    openclash: {verify_api: true},
    subscription: {
      source_url: document.getElementById("source_url").value,
      public_path: state.config.subscription.public_path || "/subscription.yaml",
      token: state.config.subscription.token || "",
      cache_path: state.config.subscription.cache_path || "/data/subscription-filtered.yaml",
      last_source_path: state.config.subscription.last_source_path || "/data/subscription-source.yaml",
      output_config_path: state.config.subscription.output_config_path || "/etc/openclash/config/openclash-region-filter.yaml",
      timeout_seconds: state.config.subscription.timeout_seconds || 30,
      refresh_interval_seconds: state.config.subscription.refresh_interval_seconds || 3600,
      user_agent: state.config.subscription.user_agent || "clash.meta"
    },
    automation: {enabled: true},
    filter: {
      allow_unknown: true,
      enabled_regions: Array.from(enabled),
      excluded_regions: Array.from(excluded)
    }
  };
}

async function saveSettings(button) {
  return withFeedback(button, {busy: "保存并应用中", success: "保存并应用完成", error: "保存并应用失败"}, async () => {
    if (!document.getElementById("source_url").value.trim()) {
      throw new Error("请先填写订阅链接");
    }
    document.getElementById("status").textContent = "保存中";
    const result = await api("/api/settings/apply", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(collectSettings())
    });
    await refresh();
    if (!result.ok) throw new Error(result.error || "保存并应用失败");
  });
}

async function updateSubscription(button) {
  return withFeedback(button, {busy: "更新中", success: "订阅已更新", error: "更新失败"}, async () => {
    document.getElementById("status").textContent = "更新订阅中";
    const result = await api("/api/apply", {method: "POST"});
    await refresh();
    if (!result.ok) throw new Error(result.error || "更新订阅失败");
  });
}

refresh().catch(err => {
  document.getElementById("status").textContent = "加载失败";
  document.getElementById("result").textContent = String(err);
});
setInterval(() => refresh().catch(() => {}), 60000);
</script>
</body>
</html>
"""


class AppState:
    def __init__(self, store: ConfigStore):
        self.store = store
        self.lock = threading.Lock()
        self.operation_lock = threading.Lock()
        self.last_result: dict[str, Any] | None = None
        self.last_subscription_result: dict[str, Any] | None = None
        self.last_install_result: dict[str, Any] | None = None
        self.subscription_info_cache: dict[str, Any] | None = None
        self.subscription_info_checked_at = 0.0
        self.subscription_info_source = ""
        self.stop_event = threading.Event()
        with self.lock:
            config = self._managed_config(self.store.load())
            self.store.save(config)

    @staticmethod
    def _managed_config(config: dict[str, Any]) -> dict[str, Any]:
        output_path = str(
            config.get("subscription", {}).get("output_config_path")
            or DEFAULT_CONFIG["subscription"]["output_config_path"]
        )
        config["subscription"]["output_config_path"] = output_path
        config["openclash"]["config_path"] = output_path
        config["openclash"]["runtime_config_path"] = str(
            DEFAULT_CONFIG["openclash"]["runtime_config_path"]
        )
        config["openclash"]["reload_command"] = str(
            config["openclash"].get("reload_command")
            or DEFAULT_CONFIG["openclash"]["reload_command"]
        )
        config["openclash"]["dashboard_api"] = str(
            config["openclash"].get("dashboard_api")
            or DEFAULT_CONFIG["openclash"]["dashboard_api"]
        )
        config["openclash"]["verify_api"] = True
        config["automation"]["enabled"] = True
        config["filter"]["allow_unknown"] = True
        return config

    def load_config(self) -> dict[str, Any]:
        with self.lock:
            return self.store.load()

    def save_config(self, patch: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            config = self.store.load()
            for section in ("openclash", "subscription", "automation", "filter"):
                if section in patch and isinstance(patch[section], dict):
                    config[section].update(patch[section])
            if "regions" in patch and isinstance(patch["regions"], list):
                config["regions"] = patch["regions"]
            config = self._managed_config(config)
            self.store.save(config)
            source_url = str(config["subscription"].get("source_url", ""))
            if source_url != self.subscription_info_source:
                self.subscription_info_cache = None
                self.subscription_info_checked_at = 0.0
                self.subscription_info_source = source_url
            self.last_result = {
                "ok": True,
                "message": "设置已保存，正在自动生成并应用 OpenClash 配置。",
                "applied": False,
            }
            return config

    def apply(self) -> dict[str, Any]:
        with self.operation_lock:
            with self.lock:
                config = self._managed_config(self.store.load())
            if str(config.get("subscription", {}).get("source_url", "")).strip():
                return self.apply_subscription_config(config)
            result = apply_filter(config)
            with self.lock:
                self.last_result = result.to_dict()
            return result.to_dict()

    def apply_subscription_config(self, config: dict[str, Any]) -> dict[str, Any]:
        updated_config, refresh_result = refresh_subscription(config)
        refresh_payload = refresh_result.to_dict()
        with self.lock:
            if refresh_result.ok:
                self.store.save(updated_config)
            self.last_subscription_result = refresh_payload

        if not refresh_result.ok:
            payload = {
                "ok": False,
                "stage": "refresh_subscription",
                "error": refresh_result.error or "刷新订阅失败",
                "refresh": refresh_payload,
            }
            with self.lock:
                self.last_result = payload
            return payload

        install_result = install_filtered_config(updated_config)
        install_payload = install_result.to_dict()
        with self.lock:
            self.last_install_result = install_payload

        filter_payload = refresh_result.filter_result or {}
        payload: dict[str, Any] = {
            **filter_payload,
            "ok": bool(filter_payload.get("ok", True)) and install_result.ok,
            "changed": bool(refresh_result.changed or install_result.changed),
            "config_path": install_result.output_config_path,
            "refresh": refresh_payload,
            "install": install_payload,
        }

        if not install_result.ok:
            payload.update({"ok": False, "stage": "install_filtered_config", "error": install_result.error})
            with self.lock:
                self.last_result = payload
            return payload

        reload_result = (
            self.reload_openclash()
            if install_result.changed
            else {"ok": True, "skipped": True, "reason": "config unchanged"}
        )
        payload["reload"] = reload_result
        if not reload_result.get("ok"):
            payload.update({"ok": False, "stage": "reload_openclash", "error": reload_result.get("error") or reload_result.get("output") or "重载失败"})
            with self.lock:
                self.last_result = payload
            return payload

        if updated_config["openclash"].get("verify_api"):
            payload["verification"] = verify_running_state(updated_config)
            if not install_result.changed and payload["verification"].get("bad_nodes"):
                reload_result = self.reload_openclash()
                payload["reload"] = reload_result
                if reload_result.get("ok"):
                    payload["verification"] = verify_running_state(updated_config)
            payload["verified"] = bool(payload["verification"].get("ok")) and not payload["verification"].get("bad_nodes")
            if payload["verification"].get("ok") is False or payload["verification"].get("bad_nodes"):
                payload["ok"] = False
                payload["stage"] = "verify_running_state"
                payload["error"] = "运行态验证失败"

        with self.lock:
            self.last_result = payload
        return payload

    def save_and_apply(self, patch: dict[str, Any]) -> dict[str, Any]:
        self.save_config(patch)
        return self.apply()

    def refresh_subscription(self) -> dict[str, Any]:
        with self.lock:
            config = self.store.load()
        updated_config, result = refresh_subscription(config)
        payload = result.to_dict()
        with self.lock:
            if result.ok:
                self.store.save(updated_config)
            self.last_subscription_result = payload
        return payload

    def install_filtered_config(self) -> dict[str, Any]:
        with self.lock:
            config = self.store.load()
        result = install_filtered_config(config)
        payload = result.to_dict()
        with self.lock:
            self.last_install_result = payload
        return payload

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

    def dashboard_status(self) -> dict[str, Any]:
        with self.lock:
            config = self.store.load()
        return verify_running_state(config)

    def subscription_info(self) -> dict[str, Any]:
        with self.lock:
            config = self.store.load()
            source_url = str(config.get("subscription", {}).get("source_url", ""))
            if (
                self.subscription_info_cache is not None
                and source_url == self.subscription_info_source
                and time.time() - self.subscription_info_checked_at < 300
            ):
                return copy.deepcopy(self.subscription_info_cache)

        info = fetch_subscription_info(config)
        with self.lock:
            self.subscription_info_cache = copy.deepcopy(info)
            self.subscription_info_checked_at = time.time()
            self.subscription_info_source = source_url
        return info


def list_config_files(config_path: str) -> dict[str, Any]:
    path = Path(config_path or "/etc/openclash/config")
    directory = path if path.is_dir() else path.parent
    files = []

    if directory.exists() and directory.is_dir():
        for item in sorted(directory.iterdir(), key=lambda p: p.name.lower()):
            if item.is_file() and item.suffix.lower() in {".yaml", ".yml"}:
                files.append({"name": item.name, "path": str(item)})

    return {"directory": str(directory), "files": files}


def sanitize_result_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        sanitized = {}
        for key, value in payload.items():
            if key in {"source_url", "dashboard_secret"}:
                sanitized[key] = ""
            elif key == "config_path" and isinstance(value, str) and value.startswith(("http://", "https://")):
                sanitized[key] = "remote subscription"
            else:
                sanitized[key] = sanitize_result_payload(value)
        return sanitized
    if isinstance(payload, list):
        return [sanitize_result_payload(value) for value in payload]
    return payload


def subscription_authorized(config: dict[str, Any], query: dict[str, list[str]], headers: Any) -> bool:
    token = str(config.get("subscription", {}).get("token", ""))
    if not token:
        return True
    supplied = (query.get("token") or [""])[0] or headers.get("X-Subscription-Token", "")
    return supplied == token


def automation_loop(state: AppState) -> None:
    last_mtime: float | None = None
    last_subscription_refresh: float | None = None
    while not state.stop_event.is_set():
        try:
            config = state.load_config()
            path = Path(config["openclash"]["config_path"])
            automation = config.get("automation", {})
            poll_seconds = max(5, int(automation.get("poll_seconds", 30)))
            subscription = config.get("subscription", {})
            source_url = str(subscription.get("source_url", "")).strip()

            if not automation.get("enabled"):
                state.stop_event.wait(poll_seconds)
                continue

            if source_url:
                interval = max(60, int(subscription.get("refresh_interval_seconds", 3600)))
                now = time.time()
                if (
                    last_subscription_refresh is None
                    or now - last_subscription_refresh >= interval
                ):
                    state.apply()
                    last_subscription_refresh = now
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

    def send_subscription(self, head_only: bool = False) -> bool:
        parsed = urlparse(self.path)
        config = self.server.app_state.load_config()
        subscription = config.get("subscription", {})
        public_path = str(subscription.get("public_path") or "/subscription.yaml")
        if parsed.path != public_path:
            return False

        query = parse_qs(parsed.query)
        if not subscription_authorized(config, query, self.headers):
            self.send_json({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
            return True

        cache_path = Path(str(subscription.get("cache_path") or "/data/subscription-filtered.yaml"))
        if not cache_path.exists() and str(subscription.get("source_url", "")).strip():
            self.server.app_state.refresh_subscription()

        if not cache_path.exists():
            self.send_json(
                {"error": "subscription cache is empty; refresh subscription first"},
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
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
        if self.send_subscription(head_only=True):
            return
        self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        config = self.server.app_state.load_config()
        subscription = config.get("subscription", {})
        if self.send_subscription():
            return
        if path == "/":
            body = INDEX_HTML.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/state":
            scan_path = Path(str(config["openclash"]["config_path"]))
            source_url = str(subscription.get("source_url", "")).strip()
            raw_subscription_path = Path(
                str(subscription.get("last_source_path") or "/data/subscription-source.yaml")
            )
            if source_url and raw_subscription_path.exists():
                scan_path = raw_subscription_path
            try:
                scan = scan_regions(scan_path, config)
            except Exception as exc:
                scan = {"error": str(exc), "node_count": 0, "region_counts": {}, "nodes": []}
            response_config = copy.deepcopy(config)
            if isinstance(scan.get("regions"), list):
                response_config["regions"] = scan["regions"]
            response_config["openclash"]["dashboard_secret"] = ""
            self.send_json(
                {
                    "config": response_config,
                    "scan": scan,
                    "last_result": sanitize_result_payload(self.server.app_state.last_result),
                    "last_subscription_result": sanitize_result_payload(self.server.app_state.last_subscription_result),
                    "last_install_result": self.server.app_state.last_install_result,
                    "subscription_info": self.server.app_state.subscription_info(),
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
        if path == "/api/settings/apply":
            self.send_json(self.server.app_state.save_and_apply(self.read_json()))
            return
        if path == "/api/apply":
            self.send_json(self.server.app_state.apply())
            return
        if path == "/api/subscription/refresh":
            self.send_json(self.server.app_state.refresh_subscription())
            return
        if path == "/api/subscription/install":
            self.send_json(self.server.app_state.install_filtered_config())
            return
        if path == "/api/dashboard/status":
            self.send_json(self.server.app_state.dashboard_status())
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
