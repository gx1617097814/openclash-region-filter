#!/bin/sh
set -eu

ROUTER_HOST=${ROUTER_HOST:-${1:-192.168.2.1}}
ROUTER_USER=${ROUTER_USER:-root}
ROUTER="$ROUTER_USER@$ROUTER_HOST"
PORT=${PORT:-8088}

ssh "$ROUTER" "PORT='$PORT' sh -s" <<'REMOTE'
set -eu

mkdir -p /usr/share/luci/menu.d
mkdir -p /www/luci-static/resources/view/openclash-region-filter

cat > /usr/share/luci/menu.d/luci-app-openclash-region-filter.json <<'JSON'
{
  "admin/services/openclash-region-filter": {
    "title": "OpenClash 地区过滤",
    "order": 45,
    "action": {
      "type": "view",
      "path": "openclash-region-filter/panel"
    }
  }
}
JSON

cat > /www/luci-static/resources/view/openclash-region-filter/panel.js <<EOF
'use strict';
'require view';

return view.extend({
  render: function() {
    return E('iframe', {
      src: 'http://' + window.location.hostname + ':$PORT',
      style: 'width: 100%; min-height: calc(100vh - 155px); border: none; border-radius: 8px; background: #fff;'
    });
  },
  handleSaveApply: null,
  handleSave: null,
  handleReset: null
});
EOF

rm -rf /tmp/luci-indexcache /tmp/luci-modulecache
/etc/init.d/uhttpd reload >/dev/null 2>&1 || true

echo "LuCI menu installed: admin/services/openclash-region-filter"
REMOTE
