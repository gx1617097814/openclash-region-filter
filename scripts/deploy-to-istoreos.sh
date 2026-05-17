#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
ROUTER_HOST=${ROUTER_HOST:-${1:-192.168.2.1}}
ROUTER_USER=${ROUTER_USER:-root}
ROUTER="$ROUTER_USER@$ROUTER_HOST"
REMOTE_TAR=${REMOTE_TAR:-/tmp/openclash-region-filter.tar.gz}
REMOTE_SCRIPT=${REMOTE_SCRIPT:-/tmp/deploy-ocfilter.sh}
PORT=${PORT:-8088}

"$ROOT/scripts/package.sh"

echo "== Uploading package to $ROUTER =="
scp "$ROOT/artifacts/openclash-region-filter.tar.gz" "$ROUTER:$REMOTE_TAR"
scp "$ROOT/scripts/deploy-ocfilter.sh" "$ROUTER:$REMOTE_SCRIPT"

echo "== Deploying on iStoreOS =="
ssh "$ROUTER" "APP_TAR='$REMOTE_TAR' PORT='$PORT' sh '$REMOTE_SCRIPT'"

echo "== Checking panel =="
curl -fsS "http://$ROUTER_HOST:$PORT/api/state" >/dev/null
echo "OpenClash Region Filter is available at http://$ROUTER_HOST:$PORT"
