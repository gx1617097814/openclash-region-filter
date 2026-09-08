from __future__ import annotations

import copy
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.defaults import DEFAULT_CONFIG
from app.filtering import LATENCY_GROUP_NAME, filter_config_data, scan_regions
from app.config import ConfigStore, merge_regions
from app.service import AppState, INDEX_HTML, automation_loop
from app.mihomo import select_node, selector_status
from app.profiles import get_profile, new_profile, normalize_profiles, runtime_config
from app.subscription import InstallResult, install_filtered_config, parse_subscription_userinfo, refresh_subscription


class FilteringTest(unittest.TestCase):
    def test_filters_openclash_nodes_and_groups(self) -> None:
        data = {
            "proxies": [
                {"name": "🇸🇬狮城-E(流量)", "type": "ss"},
                {"name": "🇺🇸美国-X", "type": "ss"},
                {"name": "🇯🇵日本-X", "type": "ss"},
                {"name": "🇰🇷韩国-A", "type": "ss"},
                {"name": "🇭🇰香港-A", "type": "ss"},
                {"name": "🇨🇳中国-A", "type": "ss"},
                {"name": "猎户座-未知", "type": "ss"},
            ],
            "proxy-groups": [
                {
                    "name": "🚀 节点选择",
                    "type": "select",
                    "proxies": [
                        "♻️ 自动选择",
                        "🇸🇬狮城-E(流量)",
                        "🇺🇸美国-X",
                        "🇯🇵日本-X",
                        "🇰🇷韩国-A",
                        "🇭🇰香港-A",
                        "🇨🇳中国-A",
                        "猎户座-未知",
                        "DIRECT",
                    ],
                },
                {
                    "name": "♻️ 自动选择",
                    "type": "url-test",
                    "proxies": [
                        "🇸🇬狮城-E(流量)",
                        "🇺🇸美国-X",
                        "🇯🇵日本-X",
                        "🇰🇷韩国-A",
                        "🇭🇰香港-A",
                        "🇨🇳中国-A",
                        "猎户座-未知",
                    ],
                },
            ],
        }
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["filter"]["keep_disabled_proxies_for_latency"] = False

        filtered, result = filter_config_data(data, config)
        names = [proxy["name"] for proxy in filtered["proxies"]]

        self.assertEqual(len(names), 5)
        self.assertFalse([name for name in names if "香江" in name or "香港" in name])
        self.assertFalse([name for name in names if "中国" in name or "大陆" in name])
        self.assertIn("🇸🇬狮城-E(流量)", names)
        self.assertIn("🇺🇸美国-X", names)
        self.assertIn("🇯🇵日本-X", names)
        self.assertIn("🇰🇷韩国-A", names)
        self.assertIn("猎户座-未知", names)
        self.assertIn("hong_kong", result.region_counts)
        self.assertIn("china_mainland", result.region_counts)

        groups = {group["name"]: group for group in filtered["proxy-groups"]}
        auto = groups["♻️ 自动选择"]["proxies"]
        select = groups["🚀 节点选择"]["proxies"]

        self.assertEqual(set(auto), set(names))
        self.assertFalse([name for name in auto if "香江" in name or "中国" in name])
        self.assertEqual(select[0], "🇸🇬狮城-E(流量)")
        self.assertEqual(set(select[:-1]), set(names))
        self.assertEqual(select[-1], "♻️ 自动选择")

    def test_region_migration_adds_new_default_regions(self) -> None:
        saved = [
            {
                "id": "hong_kong",
                "label": "香港",
                "patterns": ["香港"],
            }
        ]

        regions = merge_regions(saved)
        region_ids = [region["id"] for region in regions]

        self.assertIn("hong_kong", region_ids)
        self.assertIn("china_mainland", region_ids)

    def test_scan_adds_dynamic_flag_regions(self) -> None:
        data = {
            "proxies": [
                {"name": "🇹🇼台湾-A", "type": "ss"},
                {"name": "🇬🇧英国-A", "type": "ss"},
                {"name": "No Flag Node", "type": "ss"},
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            sample = Path(tmpdir) / "sample.yaml"
            sample.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
            scan = scan_regions(sample, copy.deepcopy(DEFAULT_CONFIG))

        region_ids = {region["id"] for region in scan["regions"]}
        self.assertIn("taiwan", region_ids)
        self.assertIn("auto_gb", region_ids)
        self.assertIn("other_unknown", region_ids)
        self.assertEqual(scan["region_counts"]["taiwan"], 1)
        self.assertEqual(scan["region_counts"]["auto_gb"], 1)
        self.assertEqual(scan["region_counts"]["other_unknown"], 1)

    def test_subscription_refresh_filters_and_caches_yaml(self) -> None:
        data = {
            "proxies": [
                {"name": "🇸🇬新加坡-A", "type": "ss"},
                {"name": "🇭🇰香港-A", "type": "ss"},
                {"name": "🇬🇧英国-A", "type": "ss"},
            ],
            "proxy-groups": [
                {
                    "name": "Proxy",
                    "type": "select",
                    "proxies": ["🇸🇬新加坡-A", "🇭🇰香港-A", "🇬🇧英国-A", "DIRECT"],
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "source.yaml"
            cache = root / "filtered.yaml"
            raw = root / "raw.yaml"
            source.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

            config = copy.deepcopy(DEFAULT_CONFIG)
            config["filter"]["keep_disabled_proxies_for_latency"] = False
            config["subscription"]["source_url"] = source.as_uri()
            config["subscription"]["cache_path"] = str(cache)
            config["subscription"]["last_source_path"] = str(raw)
            config["subscription"]["output_config_path"] = str(root / "openclash-filtered.yaml")
            config["subscription"]["user_agent"] = "clash.meta"
            config["filter"]["excluded_regions"].append("auto_gb")

            updated_config, result = refresh_subscription(config)
            install_result = install_filtered_config(config)
            rendered = yaml.safe_load(cache.read_text(encoding="utf-8"))
            installed = yaml.safe_load(Path(config["subscription"]["output_config_path"]).read_text(encoding="utf-8"))
            names = [proxy["name"] for proxy in rendered["proxies"]]

        self.assertTrue(result.ok)
        self.assertTrue(install_result.ok)
        self.assertIn("auto_gb", result.regions_added)
        self.assertIn("auto_gb", [region["id"] for region in updated_config["regions"]])
        self.assertIn("auto_gb", updated_config["filter"]["excluded_regions"])
        self.assertIn("hong_kong", updated_config["filter"]["excluded_regions"])
        self.assertEqual(result.filter_result["config_path"], str(raw))
        self.assertNotIn(source.as_uri(), str(result.filter_result))
        self.assertEqual(names, ["🇸🇬新加坡-A"])
        self.assertEqual(installed["proxies"], rendered["proxies"])

    def test_latency_mode_keeps_definitions_but_filters_groups(self) -> None:
        data = {
            "proxies": [
                {"name": "🇺🇸美国-A", "type": "ss"},
                {"name": "🇭🇰香港-A", "type": "ss"},
            ],
            "proxy-groups": [
                {"name": "Proxy", "type": "select", "proxies": ["🇺🇸美国-A", "🇭🇰香港-A"]},
            ],
        }
        config = copy.deepcopy(DEFAULT_CONFIG)
        filtered, result = filter_config_data(data, config)

        self.assertEqual([item["name"] for item in filtered["proxies"]], ["🇺🇸美国-A", "🇭🇰香港-A"])
        self.assertEqual(filtered["proxy-groups"][0]["proxies"], ["🇺🇸美国-A"])
        latency_group = next(group for group in filtered["proxy-groups"] if group["name"] == LATENCY_GROUP_NAME)
        self.assertTrue(latency_group["hidden"])
        self.assertEqual(set(latency_group["proxies"]), {"🇺🇸美国-A", "🇭🇰香港-A"})
        self.assertEqual(result.kept_nodes, ["🇺🇸美国-A"])
        self.assertEqual(result.removed_nodes, ["🇭🇰香港-A"])

    def test_nonstandard_primary_selector_gets_enabled_nodes(self) -> None:
        data = {
            "proxies": [
                {"name": "🇺🇸美国-A", "type": "ss"},
                {"name": "🇯🇵日本-A", "type": "ss"},
            ],
            "proxy-groups": [
                {"name": "我的线路", "type": "select", "proxies": ["自动线路"]},
                {"name": "自动线路", "type": "url-test", "proxies": ["🇺🇸美国-A", "🇯🇵日本-A"]},
            ],
        }
        filtered, _ = filter_config_data(data, copy.deepcopy(DEFAULT_CONFIG))
        primary = filtered["proxy-groups"][0]["proxies"]
        self.assertEqual(primary, ["🇺🇸美国-A", "🇯🇵日本-A", "自动线路"])

    def test_primary_selector_restores_preferred_leaf_first(self) -> None:
        data = {
            "proxies": [
                {"name": "SG-A", "type": "ss"},
                {"name": "SG-B", "type": "ss"},
            ],
            "proxy-groups": [
                {"name": "Proxy", "type": "select", "proxies": ["Auto", "SG-A", "SG-B"]},
                {"name": "Auto", "type": "url-test", "proxies": ["SG-A", "SG-B"]},
            ],
        }
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["filter"]["preferred_node"] = "SG-B"

        filtered, _ = filter_config_data(data, config)

        self.assertEqual(filtered["proxy-groups"][0]["proxies"], ["SG-B", "SG-A", "Auto"])

    def test_parse_subscription_userinfo(self) -> None:
        info = parse_subscription_userinfo(
            "upload=1073741824; download=2147483648; total=10737418240; expire=1893456000"
        )

        self.assertEqual(info["used"], 3221225472)
        self.assertEqual(info["remaining"], 7516192768)
        self.assertEqual(info["percent_remaining"], 70.0)
        self.assertEqual(info["used_text"], "3.0 GB")
        self.assertEqual(info["total_text"], "10.0 GB")
        self.assertEqual(info["expire_text"], "2030-01-01 08:00:00")

    def test_zero_usage_is_not_displayed_as_unlimited(self) -> None:
        info = parse_subscription_userinfo("upload=0; download=0; total=0; expire=0")

        self.assertEqual(info["used_text"], "0 B")
        self.assertEqual(info["total_text"], "∞")
        self.assertEqual(info["remaining_text"], "∞")

    def test_managed_mode_keeps_internal_controls_automatic(self) -> None:
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["automation"]["enabled"] = False
        config["filter"]["allow_unknown"] = False
        config["openclash"]["verify_api"] = False
        config["openclash"]["config_path"] = "/tmp/manual.yaml"

        managed = AppState._managed_config(config)

        self.assertTrue(managed["automation"]["enabled"])
        self.assertTrue(managed["filter"]["allow_unknown"])
        self.assertTrue(managed["openclash"]["verify_api"])
        self.assertEqual(
            managed["openclash"]["config_path"],
            managed["subscription"]["output_config_path"],
        )

    def test_legacy_config_migrates_to_one_active_profile(self) -> None:
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["subscription"]["source_url"] = "https://example.test/current.yaml"
        config = normalize_profiles(config)

        self.assertEqual(len(config["subscriptions"]), 1)
        profile = get_profile(config)
        self.assertEqual(profile["source_url"], "https://example.test/current.yaml")
        self.assertEqual(profile["id"], config["active_subscription_id"])
        candidate = runtime_config(config, profile)
        self.assertIn(profile["id"], candidate["subscription"]["cache_path"])

    def test_legacy_timestamps_seed_persistent_attempt_times(self) -> None:
        config = normalize_profiles(copy.deepcopy(DEFAULT_CONFIG))
        profile = get_profile(config)
        profile["last_refresh_at"] = "2026-09-01 12:00:00"
        profile["last_refresh_epoch"] = 0
        profile["last_refresh_attempt_epoch"] = 0
        profile["latency"]["last_test_at"] = "2026-09-01 13:00:00"
        profile["latency"]["last_test_epoch"] = 0
        profile["latency"]["last_test_attempt_epoch"] = 0

        migrated = get_profile(normalize_profiles(config))

        self.assertGreater(migrated["last_refresh_epoch"], 0)
        self.assertEqual(migrated["last_refresh_attempt_epoch"], migrated["last_refresh_epoch"])
        self.assertGreater(migrated["latency"]["last_test_epoch"], 0)
        self.assertEqual(
            migrated["latency"]["last_test_attempt_epoch"],
            migrated["latency"]["last_test_epoch"],
        )

    def test_automation_skips_latency_before_first_subscription_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state = AppState(ConfigStore(Path(tmpdir) / "config.json"))
            with patch.object(state, "refresh_profile", return_value={"ok": False}) as refresh_profile, \
                 patch.object(state, "test_latency") as test_latency, \
                 patch.object(state.stop_event, "wait", side_effect=lambda _seconds: state.stop_event.set()):
                automation_loop(state)

        refresh_profile.assert_not_called()
        test_latency.assert_not_called()

    def test_taiwan_alias_without_flag_is_classified(self) -> None:
        data = {"proxies": [{"name": "TW-IPv6-P1-01", "type": "ss"}]}
        with tempfile.TemporaryDirectory() as tmpdir:
            sample = Path(tmpdir) / "sample.yaml"
            sample.write_text(yaml.safe_dump(data), encoding="utf-8")
            scan = scan_regions(sample, copy.deepcopy(DEFAULT_CONFIG))
        self.assertEqual(scan["region_counts"]["taiwan"], 1)

    def test_dashboard_only_exposes_required_controls(self) -> None:
        self.assertIn("先调整地区和待用节点，确认后统一保存", INDEX_HTML)
        self.assertIn("saveChanges", INDEX_HTML)
        self.assertIn("保存并应用", INDEX_HTML)
        self.assertIn("添加订阅", INDEX_HTML)
        self.assertIn("全部测速", INDEX_HTML)
        self.assertIn("自动测速", INDEX_HTML)
        self.assertIn("当前节点", INDEX_HTML)
        self.assertIn('id="result"', INDEX_HTML)
        self.assertNotIn("一键重载", INDEX_HTML)
        self.assertNotIn("生成配置", INDEX_HTML)
        self.assertNotIn("验证密钥", INDEX_HTML)
        self.assertNotIn("刷新文件", INDEX_HTML)
        self.assertNotIn("立即过滤并应用", INDEX_HTML)
        self.assertNotIn("后续无需手动操作", INDEX_HTML)
        self.assertNotIn("if(!confirm(", INDEX_HTML)
        self.assertNotIn('id="operation"', INDEX_HTML)
        self.assertNotIn("operation-bar", INDEX_HTML)
        self.assertIn('id="idleStatus"', INDEX_HTML)
        self.assertIn('[hidden] { display:none !important; }', INDEX_HTML)
        self.assertIn('id="saveBtn"', INDEX_HTML)
        self.assertIn('id="profileSaveBtn"', INDEX_HTML)
        self.assertIn("保存并读取额度", INDEX_HTML)
        self.assertIn("operationLabel(op.kind)", INDEX_HTML)
        self.assertIn("cursor:not-allowed", INDEX_HTML)
        self.assertNotIn("cursor:wait", INDEX_HTML)
        self.assertNotIn("applyRegionDraft", INDEX_HTML)
        self.assertNotIn("regionTimer", INDEX_HTML)
        self.assertIn('id="confirmDialog"', INDEX_HTML)
        self.assertIn("启用范围", INDEX_HTML)
        self.assertNotIn("保留 ${retained} 个节点", INDEX_HTML)
        self.assertIn("sessionStorage.setItem(expandedStorageKey", INDEX_HTML)
        self.assertIn("expandedKey(p.id,region.id)", INDEX_HTML)
        self.assertIn('data-profile="${esc(p.id)}"', INDEX_HTML)
        choose_node = INDEX_HTML.split("function chooseNode", 1)[1].split("async function poll", 1)[0]
        self.assertNotIn("api(", choose_node)
        self.assertNotIn("await refresh()", choose_node)
        self.assertNotIn("busy(", choose_node)

    def test_selector_status_resolves_nested_group_to_real_node(self) -> None:
        payload = {
            "proxies": {
                "Proxy": {"type": "Selector", "all": ["Auto", "SG-A"], "now": "Auto"},
                "Auto": {"type": "URLTest", "all": ["SG-A"], "now": "SG-A"},
                "SG-A": {"type": "Shadowsocks"},
            }
        }
        with patch("app.mihomo.api_request", return_value=payload):
            status = selector_status(copy.deepcopy(DEFAULT_CONFIG))

        self.assertEqual(status["selected"], "Auto")
        self.assertEqual(status["now"], "SG-A")

    def test_dashboard_uses_requested_latency_bands_and_fastest_summary(self) -> None:
        self.assertIn("delay<=200?'good':delay<=600?'mid':'bad'", INDEX_HTML)
        self.assertIn("最快 ${esc(fastest.name)}", INDEX_HTML)
        self.assertIn("p.latency?.results?.[selected]", INDEX_HTML)

    def test_dashboard_colors_quota_by_remaining_percent(self) -> None:
        self.assertIn("percent<20?'low':percent<60?'medium':'healthy'", INDEX_HTML)
        self.assertIn("quota-fill ${quotaLevel(pct)}", INDEX_HTML)

    def test_state_returns_result_for_active_profile_only(self) -> None:
        config = normalize_profiles(copy.deepcopy(DEFAULT_CONFIG))
        active = get_profile(config)
        active["last_result"] = {"profile": "active"}
        standby = copy.deepcopy(active)
        standby["id"] = "standby"
        standby["name"] = "Standby"
        standby["last_result"] = {"profile": "standby"}
        config["subscriptions"].append(standby)

        with tempfile.TemporaryDirectory() as tmpdir:
            store = ConfigStore(Path(tmpdir) / "config.json")
            store.save(config)
            state = AppState(store)
            with patch("app.service.selector_status") as status:
                payload = state.state_payload()

        self.assertEqual(payload["last_result"], {"profile": "active"})
        status.assert_not_called()

    def test_state_builds_recent_result_from_existing_cache(self) -> None:
        config = normalize_profiles(copy.deepcopy(DEFAULT_CONFIG))
        active = get_profile(config)
        active["last_result"] = None

        with tempfile.TemporaryDirectory() as tmpdir:
            store = ConfigStore(Path(tmpdir) / "config.json")
            store.save(config)
            state = AppState(store)
            state.scan_cache[active["id"]] = {
                "node_count": 2,
                "region_counts": {"singapore": 1, "hong_kong": 1},
                "regions": active["regions"],
                "nodes": [
                    {"name": "SG-A", "region_id": "singapore"},
                    {"name": "HK-A", "region_id": "hong_kong"},
                ],
            }
            payload = state.state_payload()

        self.assertTrue(payload["last_result"]["cached"])
        self.assertEqual(payload["last_result"]["kept_nodes"], ["SG-A"])
        self.assertEqual(payload["last_result"]["removed_nodes"], ["HK-A"])
        self.assertEqual(payload["last_result"]["all_nodes"], ["SG-A", "HK-A"])
        self.assertEqual(len(payload["last_result"]["region_summary"]), 2)
        self.assertEqual(payload["last_result"]["node_count"], 2)

    def test_region_changes_use_cache_and_preserve_hidden_region_preferences(self) -> None:
        data = {
            "proxies": [
                {"name": "🇸🇬SG-A", "type": "ss"},
                {"name": "🇺🇸US-A", "type": "ss"},
            ],
            "proxy-groups": [{"name": "Proxy", "type": "select", "proxies": ["🇸🇬SG-A", "🇺🇸US-A"]}],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            config = normalize_profiles(copy.deepcopy(DEFAULT_CONFIG))
            config["subscription"]["profiles_dir"] = str(Path(tmpdir) / "profiles")
            config["subscription"]["output_config_path"] = str(Path(tmpdir) / "output.yaml")
            profile = get_profile(config)
            profile["selected_node"] = "🇸🇬SG-A"
            store = ConfigStore(Path(tmpdir) / "config.json")
            store.save(config)
            state = AppState(store)
            candidate = runtime_config(state.load_config(), get_profile(state.load_config()))
            source_path = Path(candidate["subscription"]["last_source_path"])
            source_path.parent.mkdir(parents=True)
            source_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
            with state.lock:
                current = state.load_config()
                state._update_scan_cache(current, get_profile(current))
            install = InstallResult(True, False, candidate["subscription"]["cache_path"], str(Path(tmpdir) / "output.yaml"), "now")

            with patch("app.service.install_filtered_config", return_value=install), \
                 patch("app.service.refresh_subscription") as remote_refresh, \
                 patch("app.service.mihomo_select_node", return_value={"ok": True}) as select, \
                 patch("app.service.verify_running_state", return_value={"ok": True, "bad_nodes": []}):
                result = state.save_regions(
                    profile["id"],
                    ["singapore", "united_states"],
                    [],
                    selected_node="🇺🇸US-A",
                )
            updated = get_profile(state.load_config())

        self.assertTrue(result["ok"])
        self.assertIn("hong_kong", updated["filter"]["excluded_regions"])
        self.assertNotIn("united_states", updated["filter"]["excluded_regions"])
        self.assertEqual(updated["selected_node"], "🇺🇸US-A")
        self.assertEqual(result["selection"]["reason"], "requested")
        select.assert_called_once()
        self.assertEqual(select.call_args.args[1], "🇺🇸US-A")
        remote_refresh.assert_not_called()

    def test_rejecting_every_visible_region_keeps_saved_state_and_cache(self) -> None:
        data = {
            "proxies": [{"name": "🇸🇬SG-A", "type": "ss"}],
            "proxy-groups": [{"name": "Proxy", "type": "select", "proxies": ["🇸🇬SG-A"]}],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            config = normalize_profiles(copy.deepcopy(DEFAULT_CONFIG))
            config["subscription"]["profiles_dir"] = str(Path(tmpdir) / "profiles")
            store = ConfigStore(Path(tmpdir) / "config.json")
            store.save(config)
            state = AppState(store)
            profile = get_profile(state.load_config())
            before_filter = copy.deepcopy(profile["filter"])
            candidate = runtime_config(state.load_config(), profile)
            source_path = Path(candidate["subscription"]["last_source_path"])
            cache_path = Path(candidate["subscription"]["cache_path"])
            source_path.parent.mkdir(parents=True)
            source_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
            cache_path.write_text("last-known-good\n", encoding="utf-8")
            with state.lock:
                current = state.load_config()
                state._update_scan_cache(current, get_profile(current))

            result = state.save_regions(profile["id"], [], ["singapore"])
            updated = get_profile(state.load_config())
            cache_contents = cache_path.read_text(encoding="utf-8")

        self.assertFalse(result["ok"])
        self.assertEqual(updated["filter"], before_filter)
        self.assertEqual(cache_contents, "last-known-good\n")

    def test_switch_restores_remembered_reachable_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = normalize_profiles(copy.deepcopy(DEFAULT_CONFIG))
            config["subscription"]["output_config_path"] = str(Path(tmpdir) / "output.yaml")
            target = new_profile("Standby")
            target["id"] = "standby"
            target["selected_node"] = "SG-B"
            config["subscriptions"].append(target)
            store = ConfigStore(Path(tmpdir) / "config.json")
            store.save(config)
            state = AppState(store)
            candidate = runtime_config(state.load_config(), get_profile(state.load_config(), "standby"))
            state.last_subscription_result = {"filter_result": {"kept_nodes": ["SG-A", "SG-B"]}}
            install = InstallResult(True, True, "cache", str(Path(tmpdir) / "output.yaml"), "now")

            with patch("app.service.install_filtered_config", return_value=install), \
                 patch.object(state, "reload_openclash", return_value={"ok": True}), \
                 patch("app.service.time.sleep"), \
                 patch("app.service.test_latencies", return_value={"ok": True, "results": {"SG-A": 90, "SG-B": 120}}), \
                 patch("app.service.mihomo_select_node", return_value={"ok": True}) as select, \
                 patch("app.service.verify_running_state", return_value={"ok": True, "bad_nodes": []}):
                result = state._install_candidate(state.load_config(), "standby", candidate, switching=True)

            self.assertTrue(result["ok"])
            self.assertEqual(result["selection"]["reason"], "restored")
            self.assertEqual(get_profile(state.load_config())["id"], "standby")
            select.assert_called_once_with(candidate, "SG-B")

    def test_new_subscription_selects_lowest_latency_reachable_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = normalize_profiles(copy.deepcopy(DEFAULT_CONFIG))
            config["subscription"]["output_config_path"] = str(Path(tmpdir) / "output.yaml")
            target = new_profile("Standby")
            target["id"] = "standby"
            config["subscriptions"].append(target)
            store = ConfigStore(Path(tmpdir) / "config.json")
            store.save(config)
            state = AppState(store)
            candidate = runtime_config(state.load_config(), get_profile(state.load_config(), "standby"))
            state.last_subscription_result = {"filter_result": {"kept_nodes": ["SG-A", "SG-B", "SG-C"]}}
            install = InstallResult(True, True, "cache", str(Path(tmpdir) / "output.yaml"), "now")

            with patch("app.service.install_filtered_config", return_value=install), \
                 patch.object(state, "reload_openclash", return_value={"ok": True}), \
                 patch("app.service.time.sleep"), \
                 patch("app.service.test_latencies", return_value={"ok": True, "results": {"SG-A": 180, "SG-B": 75, "SG-C": 130}}), \
                 patch("app.service.mihomo_select_node", return_value={"ok": True}) as select, \
                 patch("app.service.verify_running_state", return_value={"ok": True, "bad_nodes": []}):
                result = state._install_candidate(state.load_config(), "standby", candidate, switching=True)

            self.assertTrue(result["ok"])
            self.assertEqual(result["selection"]["reason"], "lowest_latency")
            select.assert_called_once_with(candidate, "SG-B")

    def test_switch_rolls_back_when_all_nodes_are_unreachable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "output.yaml"
            output.write_text("old: true\n", encoding="utf-8")
            config = normalize_profiles(copy.deepcopy(DEFAULT_CONFIG))
            config["subscription"]["output_config_path"] = str(output)
            target = new_profile("Standby")
            target["id"] = "standby"
            config["subscriptions"].append(target)
            store = ConfigStore(Path(tmpdir) / "config.json")
            store.save(config)
            state = AppState(store)
            candidate = runtime_config(state.load_config(), get_profile(state.load_config(), "standby"))
            state.last_subscription_result = {"filter_result": {"kept_nodes": ["SG-A", "SG-B"]}}
            install = InstallResult(True, True, "cache", str(output), "now")

            with patch("app.service.install_filtered_config", return_value=install), \
                 patch.object(state, "reload_openclash", return_value={"ok": True}), \
                 patch("app.service.time.sleep"), \
                 patch("app.service.test_latencies", return_value={"ok": False, "results": {"SG-A": None, "SG-B": None}}), \
                 patch("app.service.mihomo_select_node") as select:
                result = state._install_candidate(state.load_config(), "standby", candidate, switching=True)

            self.assertFalse(result["ok"])
            self.assertTrue(result["rollback_restored"])
            self.assertNotEqual(get_profile(state.load_config())["id"], "standby")
            select.assert_not_called()

    def test_active_refresh_keeps_selected_node_without_latency_failover(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = normalize_profiles(copy.deepcopy(DEFAULT_CONFIG))
            config["subscription"]["output_config_path"] = str(Path(tmpdir) / "output.yaml")
            get_profile(config)["selected_node"] = "SG-B"
            store = ConfigStore(Path(tmpdir) / "config.json")
            store.save(config)
            state = AppState(store)
            active = get_profile(state.load_config())
            candidate = runtime_config(state.load_config(), active)
            state.last_subscription_result = {"filter_result": {"kept_nodes": ["SG-A", "SG-B"]}}
            install = InstallResult(True, False, "cache", str(Path(tmpdir) / "output.yaml"), "now")

            with patch("app.service.install_filtered_config", return_value=install), \
                 patch("app.service.test_latencies") as latency, \
                 patch("app.service.mihomo_select_node", return_value={"ok": True}) as select, \
                 patch("app.service.verify_running_state", return_value={"ok": True, "bad_nodes": []}):
                result = state._install_candidate(state.load_config(), active["id"], candidate, switching=False)

            self.assertTrue(result["ok"])
            latency.assert_not_called()
            select.assert_called_once_with(candidate, "SG-B")

    def test_latency_measurement_never_changes_selected_node(self) -> None:
        data = {
            "proxies": [{"name": "SG-A"}, {"name": "SG-B"}],
            "proxy-groups": [{"name": "Proxy", "type": "select", "proxies": ["SG-A", "SG-B"]}],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            config = normalize_profiles(copy.deepcopy(DEFAULT_CONFIG))
            config["subscription"]["profiles_dir"] = str(Path(tmpdir) / "profiles")
            profile = get_profile(config)
            profile["selected_node"] = "SG-A"
            store = ConfigStore(Path(tmpdir) / "config.json")
            store.save(config)
            state = AppState(store)
            candidate = runtime_config(state.load_config(), get_profile(state.load_config()))
            source_path = Path(candidate["subscription"]["last_source_path"])
            source_path.parent.mkdir(parents=True)
            source_path.write_text(yaml.safe_dump(data), encoding="utf-8")

            with patch("app.service.test_latencies", return_value={"ok": True, "results": {"SG-A": None, "SG-B": 50}}), \
                 patch("app.service.mihomo_select_node") as select:
                result = state.test_latency(profile["id"])
            selected = get_profile(state.load_config())["selected_node"]

        self.assertTrue(result["ok"])
        self.assertEqual(selected, "SG-A")
        select.assert_not_called()

    def test_unchanged_refresh_failure_does_not_reload_openclash(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = normalize_profiles(copy.deepcopy(DEFAULT_CONFIG))
            config["subscription"]["output_config_path"] = str(Path(tmpdir) / "output.yaml")
            get_profile(config)["selected_node"] = "SG-A"
            store = ConfigStore(Path(tmpdir) / "config.json")
            store.save(config)
            state = AppState(store)
            active = get_profile(state.load_config())
            candidate = runtime_config(state.load_config(), active)
            state.last_subscription_result = {"filter_result": {"kept_nodes": ["SG-A"]}}
            install = InstallResult(True, False, "cache", str(Path(tmpdir) / "output.yaml"), "now")

            with patch("app.service.install_filtered_config", return_value=install), \
                 patch.object(state, "reload_openclash") as reload_openclash, \
                 patch("app.service.mihomo_select_node", side_effect=RuntimeError("API unavailable")), \
                 patch("app.service.verify_running_state") as verify:
                result = state._install_candidate(state.load_config(), active["id"], candidate, switching=False)

            self.assertFalse(result["ok"])
            self.assertTrue(result["rollback_restored"])
            reload_openclash.assert_not_called()
            verify.assert_not_called()

    def test_active_refresh_restores_subscription_caches_when_install_fails(self) -> None:
        old_data = {
            "proxies": [{"name": "🇸🇬SG-OLD", "type": "ss"}],
            "proxy-groups": [{"name": "Proxy", "type": "select", "proxies": ["🇸🇬SG-OLD"]}],
        }
        new_data = {
            "proxies": [{"name": "🇺🇸US-NEW", "type": "ss"}],
            "proxy-groups": [{"name": "Proxy", "type": "select", "proxies": ["🇺🇸US-NEW"]}],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            config = normalize_profiles(copy.deepcopy(DEFAULT_CONFIG))
            config["subscription"]["profiles_dir"] = str(Path(tmpdir) / "profiles")
            config["subscription"]["output_config_path"] = str(Path(tmpdir) / "output.yaml")
            profile = get_profile(config)
            profile["source_url"] = "https://example.test/sub.yaml"
            profile["last_refresh_at"] = "2026-01-01 00:00:00"
            store = ConfigStore(Path(tmpdir) / "config.json")
            store.save(config)
            state = AppState(store)
            candidate = runtime_config(state.load_config(), get_profile(state.load_config()))
            source_path = Path(candidate["subscription"]["last_source_path"])
            cache_path = Path(candidate["subscription"]["cache_path"])
            source_path.parent.mkdir(parents=True)
            old_source = yaml.safe_dump(old_data, allow_unicode=True)
            source_path.write_text(old_source, encoding="utf-8")
            cache_path.write_text("old-filtered-cache\n", encoding="utf-8")
            with state.lock:
                current = state.load_config()
                state._update_scan_cache(current, get_profile(current))

            failed_install = InstallResult(False, False, str(cache_path), str(Path(tmpdir) / "output.yaml"), None, "write failed")
            with patch("app.subscription.fetch_subscription_document", return_value=(yaml.safe_dump(new_data, allow_unicode=True), "", 200)), \
                 patch("app.service.install_filtered_config", return_value=failed_install):
                result = state.refresh_profile(profile["id"])
            restored_profile = get_profile(state.load_config())
            restored_scan = state.scan_cache[profile["id"]]
            restored_source = source_path.read_text(encoding="utf-8")
            restored_cache = cache_path.read_text(encoding="utf-8")

        self.assertFalse(result["ok"])
        self.assertEqual(restored_profile["last_refresh_at"], "2026-01-01 00:00:00")
        self.assertEqual(restored_source, old_source)
        self.assertEqual(restored_cache, "old-filtered-cache\n")
        self.assertEqual([node["name"] for node in restored_scan["nodes"]], ["🇸🇬SG-OLD"])

    def test_operation_status_survives_page_refresh_while_work_is_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state = AppState(ConfigStore(Path(tmpdir) / "config.json"))
            profile_id = state.load_config()["active_subscription_id"]
            started = threading.Event()
            release = threading.Event()

            def worker() -> dict[str, object]:
                state._operation_progress("reload", "正在重载 OpenClash", 65)
                started.set()
                release.wait(2)
                return {"ok": True}

            thread = threading.Thread(
                target=lambda: state._run_operation("activate", profile_id, worker)
            )
            thread.start()
            self.assertTrue(started.wait(1))
            payload = state.state_payload()
            release.set()
            thread.join(2)

        self.assertTrue(payload["operation"]["active"])
        self.assertEqual(payload["operation"]["stage"], "reload")
        self.assertEqual(payload["operation"]["progress"], 65)

    def test_editing_subscription_url_probes_new_quota_and_keeps_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ConfigStore(Path(tmpdir) / "config.json")
            config = normalize_profiles(copy.deepcopy(DEFAULT_CONFIG))
            config["subscription"]["profiles_dir"] = str(Path(tmpdir) / "profiles")
            profile = get_profile(config)
            profile["source_url"] = "https://example.test/old.yaml"
            profile["last_refresh_at"] = "2026-01-01 00:00:00"
            profile["last_refresh_epoch"] = 1
            profile["quota"] = {"ok": True}
            profile["last_result"] = {"ok": True}
            profile["latency"] = {
                "interval_seconds": 14400,
                "last_test_at": "2026-01-01 00:00:00",
                "last_test_epoch": 1,
                "results": {"old": 100},
            }
            profile["selected_node"] = "old"
            store.save(config)
            state = AppState(store)

            quota = {
                "ok": True,
                "remaining": 80,
                "remaining_text": "80 B",
                "raw": "must-not-be-saved",
            }
            with patch("app.service.fetch_subscription_info", return_value=quota):
                result = state.save_profile({
                    "id": profile["id"],
                    "source_url": "https://example.test/new.yaml",
                })
            updated = get_profile(state.load_config(), profile["id"])

        self.assertTrue(result["ok"])
        self.assertTrue(updated["source_pending"])
        self.assertEqual(updated["last_refresh_attempt_epoch"], 0)
        self.assertEqual(updated["last_refresh_at"], "2026-01-01 00:00:00")
        self.assertTrue(result["quota_available"])
        self.assertEqual(updated["quota"]["remaining"], 80)
        self.assertNotIn("raw", updated["quota"])
        self.assertEqual(updated["last_result"], {"ok": True})
        self.assertEqual(updated["latency"]["results"], {"old": 100})
        self.assertEqual(updated["selected_node"], "old")

    def test_quota_probe_failure_is_generic_and_does_not_leak_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ConfigStore(Path(tmpdir) / "config.json")
            config = normalize_profiles(copy.deepcopy(DEFAULT_CONFIG))
            profile = get_profile(config)
            profile["source_url"] = "https://example.test/old.yaml"
            store.save(config)
            state = AppState(store)

            with patch(
                "app.service.fetch_subscription_info",
                return_value={"ok": False, "error": "request failed for https://secret.example/token"},
            ):
                result = state.save_profile({
                    "id": profile["id"],
                    "source_url": "https://example.test/new.yaml",
                })
            quota = get_profile(state.load_config(), profile["id"])["quota"]

        self.assertFalse(result["quota_available"])
        self.assertEqual(quota["error"], "额度读取失败")
        self.assertNotIn("secret.example", json.dumps(quota, ensure_ascii=False))

    def test_node_selection_must_be_confirmed_by_runtime(self) -> None:
        selector = {"name": "Proxy", "all": ["SG-A"], "selected": "SG-A", "now": "SG-A"}
        not_applied = {"name": "Proxy", "all": ["SG-A"], "selected": "Auto", "now": "SG-B"}
        with patch("app.mihomo.selector_status", side_effect=[selector, not_applied]), \
             patch("app.mihomo.api_request", return_value={"ok": True}):
            with self.assertRaisesRegex(RuntimeError, "切换后验证失败"):
                select_node(copy.deepcopy(DEFAULT_CONFIG), "SG-A")

    def test_refresh_reuses_quota_header_from_yaml_download(self) -> None:
        data = {
            "proxies": [{"name": "🇸🇬SG-A", "type": "ss"}],
            "proxy-groups": [{"name": "Proxy", "type": "select", "proxies": ["🇸🇬SG-A"]}],
        }
        header = "upload=1; download=2; total=10; expire=0"
        with tempfile.TemporaryDirectory() as tmpdir:
            config = copy.deepcopy(DEFAULT_CONFIG)
            config["subscription"]["source_url"] = "https://example.test/sub.yaml"
            config["subscription"]["cache_path"] = str(Path(tmpdir) / "cache.yaml")
            config["subscription"]["last_source_path"] = str(Path(tmpdir) / "source.yaml")
            with patch(
                "app.subscription.fetch_subscription_document",
                return_value=(yaml.safe_dump(data, allow_unicode=True), header, 200),
            ) as fetch:
                _, result = refresh_subscription(config)

        self.assertTrue(result.ok)
        self.assertEqual(result.subscription_info["remaining"], 7)
        fetch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
