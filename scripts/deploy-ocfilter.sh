#!/bin/sh
set -eu

APP_TAR=${APP_TAR:-/tmp/openclash-region-filter.tar.gz}
FILE_HOST=${FILE_HOST:-192.168.2.190}
IMAGE=openclash-region-filter:local
BASE_IMAGE=${BASE_IMAGE:-}
BUILD=ocfilter-build
RUN=openclash-region-filter
DATA=/overlay/upper/opt/openclash-region-filter-data
PORT=${PORT:-8088}

echo "== OpenClash region filter deploy =="
date

if [ ! -s "$APP_TAR" ]; then
  wget -O "$APP_TAR" "$FILE_HOST/openclash-region-filter.tar.gz"
fi

mkdir -p "$DATA"

if [ -z "$BASE_IMAGE" ]; then
  if docker image inspect "$IMAGE" >/dev/null 2>&1; then
    BASE_IMAGE=$IMAGE
  else
    BASE_IMAGE=alpine-local
  fi
fi

if ! docker image inspect "$BASE_IMAGE" >/dev/null 2>&1; then
  echo "missing base image: $BASE_IMAGE"
  exit 1
fi

if ! docker ps -a --format '{{.Names}}' | grep -qx "$BUILD"; then
  docker run -d --name "$BUILD" "$BASE_IMAGE" sleep 3600
elif ! docker ps --format '{{.Names}}' | grep -qx "$BUILD"; then
  docker start "$BUILD"
fi

if ! docker exec "$BUILD" python3 --version >/dev/null 2>&1; then
  docker exec "$BUILD" apk add --no-cache python3 py3-yaml util-linux curl tar
fi

docker cp "$APP_TAR" "$BUILD:$APP_TAR"
docker exec "$BUILD" sh -c 'rm -rf /opt/openclash-region-filter && mkdir -p /opt && tar -xzf /tmp/openclash-region-filter.tar.gz -C /opt'
docker commit "$BUILD" "$IMAGE"
docker rm -f "$BUILD" >/dev/null 2>&1 || true

docker rm -f "$RUN" >/dev/null 2>&1 || true
docker run -d --name "$RUN" --restart unless-stopped \
  --network host --pid host --privileged \
  -v /etc/openclash:/etc/openclash \
  -v "$DATA:/data" \
  -e TZ=Asia/Shanghai \
  "$IMAGE" \
  sh -c "cd /opt/openclash-region-filter && python3 -m app.service --config /data/config.json --host 0.0.0.0 --port $PORT"

sleep 3
echo "== docker ps =="
docker ps --filter "name=$RUN" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
echo "== logs =="
docker logs --tail 30 "$RUN" || true
echo "== state =="
curl -fsS "http://127.0.0.1:$PORT/api/state" \
  | sed 's/"dashboard_secret": "[^"]*"/"dashboard_secret": "***"/g' \
  | sed 's/"source_url": "[^"]*"/"source_url": "***"/g' || true
