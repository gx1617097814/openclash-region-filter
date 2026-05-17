#!/bin/sh
set -eu

ROUTER_HOST=${ROUTER_HOST:-${1:-192.168.2.1}}
ROUTER_USER=${ROUTER_USER:-root}
KEY=${KEY:-"$HOME/.ssh/id_ed25519"}

if [ ! -f "$KEY" ]; then
  ssh-keygen -t ed25519 -f "$KEY" -N ""
fi

PUBKEY=$(cat "$KEY.pub")

ssh "$ROUTER_USER@$ROUTER_HOST" "PUBKEY='$PUBKEY' sh -s" <<'REMOTE'
set -eu

install_key() {
  file=$1
  dir=$(dirname "$file")
  mkdir -p "$dir"
  touch "$file"
  grep -qxF "$PUBKEY" "$file" || printf '%s\n' "$PUBKEY" >> "$file"
  chmod 600 "$file"
}

install_key "$HOME/.ssh/authorized_keys"
chmod 700 "$HOME/.ssh"

if [ -d /etc/dropbear ] || command -v dropbear >/dev/null 2>&1; then
  install_key /etc/dropbear/authorized_keys
fi

echo "Installed key into:"
echo "  $HOME/.ssh/authorized_keys"
if [ -f /etc/dropbear/authorized_keys ]; then
  echo "  /etc/dropbear/authorized_keys"
fi
REMOTE

echo "SSH key installed for $ROUTER_USER@$ROUTER_HOST"
