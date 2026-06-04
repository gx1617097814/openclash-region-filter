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
    body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f7f9fb; color: #111827; }
    header { padding: 18px 22px; background: #ffffff; border-bottom: 1px solid var(--line); display: flex; align-items: center; justify-content: space-between; gap: 16px; }
    h1 { margin: 0; font-size: 20px; font-weight: 650; }
    main { padding: 18px 22px 32px; max-width: 1360px; margin: 0 auto; }
    section { background: #fff; border: 1px solid var(--line); border-radius: 8px; padding: 16px; margin-bottom: 16px; }
    h2 { margin: 0; font-size: 16px; }
    .section-head { display: grid; gap: 6px; margin-bottom: 16px; }
    .lower-grid { --lower-panel-height: 560px; display: grid; grid-template-columns: minmax(620px, 1fr) minmax(420px, 520px); gap: 16px; align-items: stretch; }
    .lower-grid section { box-sizing: border-box; height: var(--lower-panel-height); margin-bottom: 0; }
    .settings-section { min-height: 0; overflow: auto; }
    .settings-layout { display: grid; gap: 22px; align-items: start; justify-content: start; }
    .settings-stack { display: grid; gap: 14px; min-width: 0; }
    .settings-side { display: grid; grid-template-columns: 130px 360px; gap: 14px 16px; align-items: end; min-width: 0; }
    .field { display: grid; gap: 7px; min-width: 0; }
    .field.full { grid-column: 1 / -1; }
    .input-row { display: grid; gap: 8px; align-items: center; min-width: 0; }
    .path-row { grid-template-columns: 205px 180px 104px; }
    .command-row { grid-template-columns: 393px 104px; }
    .api-row { grid-template-columns: 282px 72px; }
    .input-row input, .input-row select { min-width: 0; }
    label { font-size: 13px; color: #334155; }
    input[type="text"], input[type="number"], input[type="password"], select { border: 1px solid var(--line); border-radius: 6px; padding: 9px 10px; font-size: 14px; width: 100%; height: 40px; box-sizing: border-box; background: #fff; color: #111827; }
    button { border: 1px solid #0f766e; background: var(--accent); color: #fff; border-radius: 6px; padding: 9px 12px; min-height: 40px; font-size: 14px; cursor: pointer; white-space: nowrap; transition: background .16s ease, border-color .16s ease, color .16s ease, opacity .16s ease; }
    button.secondary { background: #fff; color: var(--accent); }
    button.neutral { border-color: #a8b3c2; background: #fff; color: #334155; }
    button:disabled { opacity: .55; cursor: wait; }
    button.is-success { border-color: #16a34a; background: #16a34a; color: #fff; opacity: 1; }
    button.is-error { border-color: #dc2626; background: #dc2626; color: #fff; opacity: 1; }
    #feedback { position: fixed; top: 76px; right: 22px; z-index: 20; display: grid; gap: 8px; width: min(330px, calc(100vw - 44px)); pointer-events: none; }
    .notice { border: 1px solid var(--line); border-left: 4px solid var(--accent); border-radius: 6px; background: #fff; color: #111827; box-shadow: 0 8px 24px rgba(15, 23, 42, .12); padding: 9px 12px; font-size: 13px; line-height: 1.4; transform: translateY(-4px); opacity: 0; animation: notice-in .18s ease forwards; }
    .notice.ok { border-left-color: #16a34a; }
    .notice.bad { border-left-color: #dc2626; }
    .notice.info { border-left-color: #2563eb; }
    @keyframes notice-in { to { transform: translateY(0); opacity: 1; } }
    .settings-actions { display: flex; flex-wrap: wrap; gap: 12px 16px; align-items: center; padding-top: 14px; margin-top: 14px; border-top: 1px solid var(--line); }
    .settings-actions label { white-space: nowrap; }
    .hint { color: var(--muted); font-size: 12px; line-height: 1.45; margin: 0; }
    .field-help { color: var(--muted); font-size: 12px; line-height: 1.45; margin: 0; }
    .option-list { display: flex; flex-wrap: wrap; gap: 12px 18px; align-items: flex-start; }
    .option-list label { display: grid; gap: 4px; max-width: 210px; white-space: normal; }
    .option-title { display: inline-flex; gap: 6px; align-items: center; color: #334155; font-size: 13px; }
    .quota { border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; background: #f8fafc; display: grid; gap: 8px; }
    .quota-main { display: flex; justify-content: space-between; gap: 12px; align-items: baseline; font-size: 13px; color: #334155; }
    .quota-main strong { font-size: 15px; color: #111827; }
    .quota-bar { height: 9px; overflow: hidden; border-radius: 999px; background: #e2e8f0; }
    .quota-fill { height: 100%; width: 0%; background: #16a34a; border-radius: inherit; transition: width .2s ease; }
    .quota-meta { display: flex; flex-wrap: wrap; gap: 8px 14px; color: var(--muted); font-size: 12px; }
    .regions { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; align-items: stretch; }
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
    pre { white-space: pre-wrap; word-break: break-word; background: #0f172a; color: #e2e8f0; border-radius: 8px; padding: 12px; min-height: 0; overflow: auto; font-size: 12px; }
    .nodes { color: var(--muted); font-size: 12px; line-height: 1.45; height: 86px; overflow: auto; }
    .row { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
    .header-actions { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; justify-content: flex-end; }
    .pill { border: 1px solid var(--line); border-radius: 999px; padding: 4px 8px; font-size: 12px; background: #f8fafc; }
    @media (max-width: 980px) {
      .lower-grid { grid-template-columns: 1fr; }
      .lower-grid section { height: auto; }
      .result-section { height: 360px !important; }
    }
    @media (max-width: 860px) {
      .settings-layout, .settings-side { grid-template-columns: 1fr; }
      .path-row, .command-row, .api-row { grid-template-columns: 1fr; }
      header { align-items: flex-start; flex-direction: column; }
      .header-actions { justify-content: flex-start; }
      #feedback { top: 92px; width: calc(100vw - 44px); }
    }
    @media (prefers-color-scheme: dark) {
      body { background: #0b1220; color: #e5e7eb; }
      header, section { background: #111827; }
      input[type="text"], input[type="number"], input[type="password"], select, button.secondary, button.neutral, .notice, .quota { background: #0b1220; color: #e5e7eb; }
      .quota-main, .quota-main strong { color: #e5e7eb; }
      .quota-bar { background: #1f2937; }
      label, .count, .nodes, .region-state, .field-help { color: #9ca3af; }
      .option-title { color: #e5e7eb; }
      .pill { background: #0b1220; }
    }
  </style>
</head>
<body>
  <header>
    <h1>OpenClash 地区过滤</h1>
    <div class="header-actions">
      <span id="status" class="pill">加载中</span>
      <button class="secondary" onclick="refresh(this)">刷新</button>
      <button onclick="applyNow(this)">立即过滤并应用</button>
      <button class="secondary" onclick="saveSettings(this)">保存并应用</button>
      <div id="feedback" aria-live="polite"></div>
    </div>
  </header>
  <main>
    <section>
      <h2>地区规则</h2>
      <div id="regions" class="regions"></div>
    </section>

    <div class="lower-grid">
      <section class="settings-section">
        <div class="section-head">
          <h2>运行设置</h2>
          <p class="hint">订阅更新后会自动过滤，日常主要调整地区开关。</p>
        </div>
        <div class="settings-layout">
          <div class="settings-stack">
            <div class="field">
              <label>配置文件路径</label>
              <div class="input-row path-row">
                <input id="config_path" type="text">
                <select id="config_file_select" onchange="selectConfigFile()"></select>
                <button class="neutral" onclick="loadConfigFiles(true, this)" type="button">刷新文件</button>
              </div>
              <p class="field-help">重新读取 OpenClash 配置目录，方便从现有 YAML 中选择要处理的配置。</p>
            </div>
            <div class="field">
              <label>重载命令</label>
              <div class="input-row command-row">
                <input id="reload_command" type="text">
                <button class="neutral" onclick="reloadOpenClash(this)" type="button">一键重载</button>
              </div>
              <p class="field-help">执行配置中的 OpenClash 重启/重载命令，让已生成的配置进入运行态。</p>
            </div>
            <div class="field">
              <label>远程 YAML 订阅</label>
              <div class="input-row command-row">
                <input id="source_url" type="text" placeholder="https://example.com/clash.yaml">
                <button class="neutral" onclick="refreshSubscription(this)" type="button">刷新订阅</button>
              </div>
              <p class="field-help">从原始 Clash/OpenClash YAML 订阅拉取最新节点，并按当前地区规则生成过滤缓存。</p>
            </div>
            <div class="field">
              <label>本地订阅地址</label>
              <div class="input-row command-row">
                <input id="local_subscription_url" type="text" readonly>
                <button class="neutral" onclick="copySubscriptionUrl(this)" type="button">复制</button>
              </div>
              <p class="field-help">复制过滤工具暴露的订阅地址；当前方案下 OpenClash 主要使用本地输出配置文件。</p>
            </div>
            <div class="field">
              <label>OpenClash 输出配置</label>
              <div class="input-row command-row">
                <input id="output_config_path" type="text">
                <button class="neutral" onclick="installFilteredConfig(this)" type="button">生成配置</button>
              </div>
              <p class="field-help">把过滤后的 YAML 写入 OpenClash 配置目录；不会单独触发 OpenClash 重载。</p>
            </div>
          </div>
          <div class="settings-side">
            <div class="field">
              <label>轮询间隔</label>
              <input id="poll_seconds" type="number" min="5">
              <p class="field-help">自动监听开启时的检查频率，单位为秒。</p>
            </div>
            <div class="field">
              <label>运行态验证 API</label>
              <div class="input-row api-row">
                <input id="dashboard_api" type="text">
                <button class="neutral" onclick="checkDashboardApi(this)" type="button">检查</button>
              </div>
              <p class="field-help">连接 OpenClash Meta 控制 API，用于确认当前运行节点是否符合过滤结果。</p>
            </div>
            <div class="field full">
              <label>验证密钥</label>
              <input id="dashboard_secret" type="password" placeholder="可留空">
              <p class="field-help">OpenClash Dashboard Secret；只在检查运行态 API 时使用。</p>
            </div>
            <div class="field full"><p class="hint">运行态验证 API 只用于检查 OpenClash 当前运行节点；不影响过滤本身。通常保持默认即可。</p></div>
          </div>
        </div>
        <div class="settings-actions">
          <div class="option-list">
            <label>
              <span class="option-title"><input id="automation_enabled" type="checkbox"> 自动监听配置变化</span>
              <span class="field-help">定期拉取远程订阅并重新生成过滤缓存。</span>
            </label>
            <label>
              <span class="option-title"><input id="allow_unknown" type="checkbox"> 允许未知地区节点</span>
              <span class="field-help">启用未匹配到地区规则的节点；关闭后未知节点会被排除。</span>
            </label>
            <label>
              <span class="option-title"><input id="verify_api" type="checkbox"> 应用后运行态验证</span>
              <span class="field-help">应用完成后检查 OpenClash 当前运行节点是否仍包含被禁用地区。</span>
            </label>
          </div>
        </div>
      </section>

      <section class="result-section">
        <h2>最近结果</h2>
        <div id="quota" class="quota">
          <div class="quota-main"><strong>读取中</strong><span>--</span></div>
          <div class="quota-bar"><div class="quota-fill"></div></div>
          <div class="quota-meta"><span>到期：--</span><span>检查：--</span></div>
        </div>
        <pre id="result">暂无</pre>
      </section>
    </div>
  </main>
<script>
let state = null;
let noticeTimer = null;

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
  return state.config.regions;
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
  notice(`${label}${stateText}，点击“保存设置”后生效`, "info");
}

async function api(path, options) {
  const res = await fetch(path, options);
  const text = await res.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch (_) { data = {raw: text}; }
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

async function refresh(button) {
  return withFeedback(button, {busy: "刷新中", success: "已刷新", error: "刷新失败"}, async () => {
    document.getElementById("status").textContent = "刷新中";
    state = await api("/api/state");
    document.getElementById("config_path").value = state.config.openclash.config_path || "";
    document.getElementById("reload_command").value = state.config.openclash.reload_command || "";
    document.getElementById("dashboard_api").value = state.config.openclash.dashboard_api || "";
    document.getElementById("dashboard_secret").value = state.config.openclash.dashboard_secret || "";
    document.getElementById("source_url").value = state.config.subscription.source_url || "";
    document.getElementById("local_subscription_url").value = localSubscriptionUrl();
    document.getElementById("output_config_path").value = state.config.subscription.output_config_path || "/etc/openclash/config/openclash-region-filter.yaml";
    document.getElementById("poll_seconds").value = state.config.automation.poll_seconds || 30;
    document.getElementById("automation_enabled").checked = !!state.config.automation.enabled;
    document.getElementById("allow_unknown").checked = !!state.config.filter.allow_unknown;
    document.getElementById("verify_api").checked = !!state.config.openclash.verify_api;
    document.getElementById("regions").innerHTML = visibleRegions().map(regionNode).join("");
    document.getElementById("result").textContent = JSON.stringify(state.last_result || state.scan, null, 2);
    renderQuota(state.subscription_info);
    document.getElementById("status").textContent = state.config.automation.enabled ? "自动监听中" : "自动监听关闭";
    await loadConfigFiles(false);
  });
}

function renderQuota(info) {
  const quota = document.getElementById("quota");
  if (!quota) return;
  if (!info || !info.ok) {
    quota.innerHTML = `<div class="quota-main"><strong>未读取到额度</strong><span>${(info && info.error) || "--"}</span></div>
      <div class="quota-bar"><div class="quota-fill" style="width:0%"></div></div>
      <div class="quota-meta"><span>到期：--</span><span>检查：--</span></div>`;
    return;
  }
  const percent = info.percent_remaining == null ? 0 : Math.max(0, Math.min(100, Number(info.percent_remaining)));
  quota.innerHTML = `<div class="quota-main"><strong>剩余 ${info.remaining_text}</strong><span>${percent}%</span></div>
    <div class="quota-bar"><div class="quota-fill" style="width:${percent}%"></div></div>
    <div class="quota-meta">
      <span>已用：${info.used_text}</span>
      <span>总量：${info.total_text}</span>
      <span>到期：${info.expire_text}</span>
      <span>剩余天数：${info.days_left ?? "--"}</span>
      <span>检查：${info.checked_at || "--"}</span>
    </div>`;
}

function localSubscriptionUrl() {
  const subscription = state.config.subscription || {};
  const path = subscription.public_path || "/subscription.yaml";
  const url = new URL(path, window.location.origin);
  if (subscription.token) url.searchParams.set("token", subscription.token);
  return url.toString();
}

async function copySubscriptionUrl(button) {
  return withFeedback(button, {busy: "复制中", success: "已复制", error: "复制失败"}, async () => {
    const value = document.getElementById("local_subscription_url").value;
    if (!value) throw new Error("订阅地址为空");
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value);
    } else {
      const input = document.getElementById("local_subscription_url");
      input.focus();
      input.select();
      input.setSelectionRange(0, value.length);
      if (!document.execCommand("copy")) {
        throw new Error("浏览器拒绝复制，请手动选中地址复制");
      }
      window.getSelection().removeAllRanges();
    }
    document.getElementById("status").textContent = "订阅地址已复制";
  });
}

async function loadConfigFiles(showStatus = true, button = null) {
  return withFeedback(button, {busy: "读取中", success: "已读取", error: "读取失败"}, async () => {
    if (showStatus) document.getElementById("status").textContent = "读取配置目录";
    const current = document.getElementById("config_path").value;
    const data = await api(`/api/config-files?path=${encodeURIComponent(current)}`);
    const select = document.getElementById("config_file_select");
    select.innerHTML = `<option value="">选择配置文件</option>` + data.files.map(file => {
      const selected = file.path === current ? "selected" : "";
      return `<option value="${file.path}" ${selected}>${file.name}</option>`;
    }).join("");
    if (showStatus) document.getElementById("status").textContent = "配置目录已读取";
  });
}

function selectConfigFile() {
  const value = document.getElementById("config_file_select").value;
  if (value) document.getElementById("config_path").value = value;
}

async function reloadOpenClash(button) {
  return withFeedback(button, {busy: "重载中", success: "重载完成", error: "重载失败"}, async () => {
    document.getElementById("status").textContent = "重载中";
    await saveOnly();
    const result = await api("/api/reload", {method: "POST"});
    document.getElementById("result").textContent = JSON.stringify(result, null, 2);
    document.getElementById("status").textContent = result.ok ? "重载完成" : "重载失败";
    if (!result.ok) throw new Error(result.error || result.output || "重载命令失败");
  });
}

async function checkDashboardApi(button) {
  return withFeedback(button, {busy: "检查中", success: "检查通过", error: "检查失败"}, async () => {
    await saveOnly();
    const result = await api("/api/dashboard/status", {method: "POST"});
    document.getElementById("result").textContent = JSON.stringify(result, null, 2);
    if (!result.ok) throw new Error(result.error || result.reason || "运行态验证 API 不可用");
  });
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
    openclash: {
      config_path: document.getElementById("config_path").value,
      reload_command: document.getElementById("reload_command").value,
      dashboard_api: document.getElementById("dashboard_api").value,
      dashboard_secret: document.getElementById("dashboard_secret").value,
      verify_api: document.getElementById("verify_api").checked
    },
    subscription: {
      source_url: document.getElementById("source_url").value,
      public_path: state.config.subscription.public_path || "/subscription.yaml",
      token: state.config.subscription.token || "",
      cache_path: state.config.subscription.cache_path || "/data/subscription-filtered.yaml",
      last_source_path: state.config.subscription.last_source_path || "/data/subscription-source.yaml",
      output_config_path: document.getElementById("output_config_path").value || "/etc/openclash/config/openclash-region-filter.yaml",
      timeout_seconds: state.config.subscription.timeout_seconds || 30,
      refresh_interval_seconds: state.config.subscription.refresh_interval_seconds || 3600,
      user_agent: state.config.subscription.user_agent || "clash.meta"
    },
    automation: {
      enabled: document.getElementById("automation_enabled").checked,
      poll_seconds: Number(document.getElementById("poll_seconds").value || 30)
    },
    filter: {
      allow_unknown: document.getElementById("allow_unknown").checked,
      enabled_regions: Array.from(enabled),
      excluded_regions: Array.from(excluded)
    }
  };
}

async function saveOnly() {
  return api("/api/settings", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(collectSettings())
  });
}

async function saveSettings(button) {
  return withFeedback(button, {busy: "保存并应用中", success: "保存并应用完成", error: "保存并应用失败"}, async () => {
    document.getElementById("status").textContent = "保存中";
    const result = await api("/api/settings/apply", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(collectSettings())
    });
    document.getElementById("result").textContent = JSON.stringify(result, null, 2);
    await refresh();
    if (!result.ok) throw new Error(result.error || "保存并应用失败");
  });
}

async function applyNow(button) {
  return withFeedback(button, {busy: "应用中", success: "应用完成", error: "应用失败"}, async () => {
    document.getElementById("status").textContent = "应用中";
    await saveOnly();
    const result = await api("/api/apply", {method: "POST"});
    document.getElementById("result").textContent = JSON.stringify(result, null, 2);
    await refresh();
    if (!result.ok) throw new Error(result.error || "应用失败");
  });
}

async function refreshSubscription(button) {
  return withFeedback(button, {busy: "刷新订阅中", success: "订阅已刷新", error: "刷新订阅失败"}, async () => {
    document.getElementById("status").textContent = "刷新订阅中";
    await saveOnly();
    const result = await api("/api/subscription/refresh", {method: "POST"});
    document.getElementById("result").textContent = JSON.stringify(result, null, 2);
    await refresh();
    if (!result.ok) throw new Error(result.error || "刷新订阅失败");
  });
}

async function installFilteredConfig(button) {
  return withFeedback(button, {busy: "生成配置中", success: "配置已生成", error: "生成配置失败"}, async () => {
    document.getElementById("status").textContent = "生成配置中";
    await saveOnly();
    const refreshResult = await api("/api/subscription/refresh", {method: "POST"});
    if (!refreshResult.ok) {
      document.getElementById("result").textContent = JSON.stringify(refreshResult, null, 2);
      document.getElementById("status").textContent = "刷新订阅失败";
      throw new Error(refreshResult.error || "刷新订阅失败");
    }
    const installResult = await api("/api/subscription/install", {method: "POST"});
    document.getElementById("result").textContent = JSON.stringify({refresh: refreshResult, install: installResult}, null, 2);
    document.getElementById("status").textContent = installResult.ok ? "配置已生成" : "生成配置失败";
    await refresh();
    if (!installResult.ok) throw new Error(installResult.error || "生成配置失败");
  });
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
        self.last_subscription_result: dict[str, Any] | None = None
        self.last_install_result: dict[str, Any] | None = None
        self.stop_event = threading.Event()

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
            self.store.save(config)
            self.last_result = {
                "ok": True,
                "message": "设置已保存；如需让 OpenClash 立即使用新地区规则，请点击右上角“保存并应用”或“立即过滤并应用”。",
                "applied": False,
            }
            return config

    def apply(self) -> dict[str, Any]:
        with self.lock:
            config = self.store.load()
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

        reload_result = self.reload_openclash()
        payload["reload"] = reload_result
        if not reload_result.get("ok"):
            payload.update({"ok": False, "stage": "reload_openclash", "error": reload_result.get("error") or reload_result.get("output") or "重载失败"})
            with self.lock:
                self.last_result = payload
            return payload

        if updated_config["openclash"].get("verify_api"):
            payload["verification"] = verify_running_state(updated_config)
            payload["verified"] = not payload["verification"].get("bad_nodes")
            if payload["verification"].get("ok") is False or payload["verification"].get("bad_nodes"):
                payload["ok"] = False
                payload["stage"] = "verify_running_state"
                payload["error"] = "运行态验证失败"

        with self.lock:
            self.last_result = payload
        return payload

    def save_and_apply(self, patch: dict[str, Any]) -> dict[str, Any]:
        config = self.save_config(patch)
        return self.apply_subscription_config(config) if str(config.get("subscription", {}).get("source_url", "")).strip() else self.apply()

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
        return fetch_subscription_info(config)


def list_config_files(config_path: str) -> dict[str, Any]:
    path = Path(config_path or "/etc/openclash/config")
    directory = path if path.is_dir() else path.parent
    files = []

    if directory.exists() and directory.is_dir():
        for item in sorted(directory.iterdir(), key=lambda p: p.name.lower()):
            if item.is_file() and item.suffix.lower() in {".yaml", ".yml"}:
                files.append({"name": item.name, "path": str(item)})

    return {"directory": str(directory), "files": files}


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
                    state.refresh_subscription()
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
            self.send_json(
                {
                    "config": response_config,
                    "scan": scan,
                    "last_result": self.server.app_state.last_result,
                    "last_subscription_result": self.server.app_state.last_subscription_result,
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
