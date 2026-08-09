from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.defaults import DEFAULT_CONFIG
from app.filtering import filter_config_data, scan_regions
from app.config import merge_regions
from app.service import AppState, INDEX_HTML
from app.subscription import install_filtered_config, parse_subscription_userinfo, refresh_subscription


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
        self.assertEqual(select[0], "♻️ 自动选择")
        self.assertEqual(set(select[1:]), set(names))

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
        self.assertIn("auto_tw", region_ids)
        self.assertIn("auto_gb", region_ids)
        self.assertIn("other_unknown", region_ids)
        self.assertEqual(scan["region_counts"]["auto_tw"], 1)
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

    def test_dashboard_only_exposes_required_controls(self) -> None:
        self.assertIn("保存并应用", INDEX_HTML)
        self.assertIn("订阅链接", INDEX_HTML)
        self.assertIn("订阅额度", INDEX_HTML)
        self.assertIn("立即更新订阅", INDEX_HTML)
        self.assertIn('id="result_json"', INDEX_HTML)
        self.assertNotIn("一键重载", INDEX_HTML)
        self.assertNotIn("生成配置", INDEX_HTML)
        self.assertNotIn("验证密钥", INDEX_HTML)
        self.assertNotIn("刷新文件", INDEX_HTML)
        self.assertNotIn("立即过滤并应用", INDEX_HTML)
        self.assertNotIn("后续无需手动操作", INDEX_HTML)
        self.assertIn("filter(region => (state.scan.region_counts[region.id] || 0) > 0)", INDEX_HTML)


if __name__ == "__main__":
    unittest.main()
