#!/usr/bin/env bash
set -euo pipefail

# Provision/update one agent SSH key for reverse tunnel access on the rtunnel account.
# Usage:
#   provision_rtunnel_key.sh <user_id> <remote_port> [agent_id]
# Public key must be provided via STDIN.

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <user_id> <remote_port> [agent_id]" >&2
  exit 2
fi

USER_ID="$1"
REMOTE_PORT="$2"
AGENT_ID="${3:-unknown}"

if ! [[ "$REMOTE_PORT" =~ ^[0-9]+$ ]]; then
  echo "remote_port must be numeric" >&2
  exit 2
fi

if (( REMOTE_PORT < 44000 || REMOTE_PORT > 44999 )); then
  echo "remote_port out of allowed range (44000-44999)" >&2
  exit 2
fi

PUBKEY_RAW="$(cat)"
PUBKEY="$(echo "$PUBKEY_RAW" | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
if [[ -z "$PUBKEY" ]]; then
  echo "missing public key on stdin" >&2
  exit 2
fi

# Keep only key type + key blob; drop caller-provided comments.
KEY_TYPE="$(awk '{print $1}' <<<"$PUBKEY")"
KEY_BLOB="$(awk '{print $2}' <<<"$PUBKEY")"
if [[ -z "$KEY_TYPE" || -z "$KEY_BLOB" ]]; then
  echo "invalid public key format" >&2
  exit 2
fi

case "$KEY_TYPE" in
  ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256|ecdsa-sha2-nistp384|ecdsa-sha2-nistp521)
    ;;
  *)
    echo "unsupported key type: $KEY_TYPE" >&2
    exit 2
    ;;
esac

if ! id rtunnel >/dev/null 2>&1; then
  useradd --system --create-home --shell /usr/sbin/nologin rtunnel
fi

RT_HOME="$(getent passwd rtunnel | cut -d: -f6)"
SSH_DIR="${RT_HOME}/.ssh"
AUTH_KEYS="${SSH_DIR}/authorized_keys"

install -d -m 700 -o rtunnel -g rtunnel "$SSH_DIR"
touch "$AUTH_KEYS"
chown rtunnel:rtunnel "$AUTH_KEYS"
chmod 600 "$AUTH_KEYS"

MARKER="radiotiker:user=${USER_ID}:agent=${AGENT_ID}:port=${REMOTE_PORT}"
TMP_FILE="$(mktemp)"
trap 'rm -f "$TMP_FILE"' EXIT

if [[ -s "$AUTH_KEYS" ]]; then
  grep -v "$MARKER" "$AUTH_KEYS" > "$TMP_FILE" || true
else
  : > "$TMP_FILE"
fi

OPTS="no-pty,no-user-rc,no-X11-forwarding,no-agent-forwarding,permitlisten=\"127.0.0.1:${REMOTE_PORT}\""
echo "${OPTS} ${KEY_TYPE} ${KEY_BLOB} ${MARKER}" >> "$TMP_FILE"

install -m 600 -o rtunnel -g rtunnel "$TMP_FILE" "$AUTH_KEYS"
echo "ok user=${USER_ID} agent=${AGENT_ID} port=${REMOTE_PORT}"
