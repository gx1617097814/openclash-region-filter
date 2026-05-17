#!/bin/sh
set -eu

ROUTER_HOST=${ROUTER_HOST:-${1:-192.168.2.1}}
ROUTER_USER=${ROUTER_USER:-root}
KEY=${KEY:-"$HOME/.ssh/id_ed25519"}

if [ ! -f "$KEY" ]; then
  ssh-keygen -t ed25519 -f "$KEY" -N ""
fi

cat "$KEY.pub" | ssh "$ROUTER_USER@$ROUTER_HOST" \
  'mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys'

echo "SSH key installed for $ROUTER_USER@$ROUTER_HOST"
