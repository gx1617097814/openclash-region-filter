from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.defaults import DEFAULT_CONFIG
from app.filtering import filter_config_data
from app.config import merge_regions


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


if __name__ == "__main__":
    unittest.main()
