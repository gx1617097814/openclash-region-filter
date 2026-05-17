#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUT=${OUT:-"$ROOT/artifacts/openclash-region-filter.tar.gz"}

mkdir -p "$(dirname -- "$OUT")"
tar \
  --exclude 'openclash-region-filter/data' \
  --exclude 'openclash-region-filter/.git' \
  --exclude 'openclash-region-filter/artifacts' \
  --exclude 'openclash-region-filter/__pycache__' \
  --exclude 'openclash-region-filter/app/__pycache__' \
  --exclude 'openclash-region-filter/tests/__pycache__' \
  -czf "$OUT" \
  -C "$(dirname -- "$ROOT")" \
  openclash-region-filter

printf 'Packaged %s\n' "$OUT"
