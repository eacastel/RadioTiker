#!/usr/bin/env bash
set -euo pipefail

# One-command vNext device onboarding + tunnel env setup.
#
# Usage:
#   ./onboard_and_start.sh --user-id eacastel --library-path /path/to/Music
#   ./onboard_and_start.sh --user-id eacastel --library-path /path/to/Music --run

usage() {
  cat <<'EOF'
Usage:
  onboard_and_start.sh --user-id <user_id> --library-path <path> [options]

Required:
  --user-id <id>          RadioTiker user id
  --library-path <path>   Local music root

Options:
  --api-base <url>        API base (default: https://next.radio.tiker.es/streamer/api)
  --agent-port <port>     Local agent file server port (default: 8765)
  --device-name <name>    Device name shown in onboarding (default: hostname)
  --key-path <path>       SSH private key path (default: ~/.radiotiker/agent_ed25519)
  --env-file <path>       Output env file (default: ./.env)
  --run                   Start thin_agent.py after writing env
  --help                  Show this help
EOF
}

USER_ID=""
LIBRARY_PATH=""
API_BASE="https://next.radio.tiker.es/streamer/api"
AGENT_PORT="8765"
DEVICE_NAME="$(hostname)"
KEY_PATH="${HOME}/.radiotiker/agent_ed25519"
ENV_FILE=".env"
RUN_AFTER=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user-id) USER_ID="${2:-}"; shift 2 ;;
    --library-path) LIBRARY_PATH="${2:-}"; shift 2 ;;
    --api-base) API_BASE="${2:-}"; shift 2 ;;
    --agent-port) AGENT_PORT="${2:-}"; shift 2 ;;
    --device-name) DEVICE_NAME="${2:-}"; shift 2 ;;
    --key-path) KEY_PATH="${2:-}"; shift 2 ;;
    --env-file) ENV_FILE="${2:-}"; shift 2 ;;
    --run) RUN_AFTER=1; shift 1 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "${USER_ID}" || -z "${LIBRARY_PATH}" ]]; then
  usage
  exit 2
fi

if [[ ! -d "${LIBRARY_PATH}" ]]; then
  echo "Invalid --library-path: ${LIBRARY_PATH}" >&2
  exit 2
fi

if ! [[ "${AGENT_PORT}" =~ ^[0-9]+$ ]]; then
  echo "--agent-port must be numeric" >&2
  exit 2
fi

json_field() {
  local field="$1"
  python3 - "$field" <<'PY'
import json,sys
field = sys.argv[1]
obj = json.load(sys.stdin)
cur = obj
for part in field.split("."):
    if isinstance(cur, dict):
        cur = cur.get(part)
    else:
        cur = None
        break
if cur is None:
    sys.exit(1)
print(cur)
PY
}

echo "== RadioTiker vNext agent onboarding =="
echo "API_BASE=${API_BASE}"
echo "USER_ID=${USER_ID}"
echo "LIBRARY_PATH=${LIBRARY_PATH}"

mkdir -p "$(dirname "${KEY_PATH}")"
if [[ ! -f "${KEY_PATH}" ]]; then
  echo "-- Generating SSH key: ${KEY_PATH}"
  ssh-keygen -t ed25519 -N '' -f "${KEY_PATH}" -C "radiotiker-${USER_ID}-$(hostname)" >/dev/null
fi
if [[ ! -f "${KEY_PATH}.pub" ]]; then
  echo "Missing public key: ${KEY_PATH}.pub" >&2
  exit 2
fi
PUB_KEY="$(cat "${KEY_PATH}.pub")"

echo "-- link/start"
RESP_START="$(curl -fsS -X POST "${API_BASE}/agent/link/start" \
  -H 'Content-Type: application/json' \
  -d "{\"device_name\":\"${DEVICE_NAME}\",\"agent_version\":\"0.5.0\"}")"
DEVICE_CODE="$(printf '%s' "${RESP_START}" | json_field "device_code")"
LINK_URL="$(printf '%s' "${RESP_START}" | json_field "link_url")"
echo "device_code=${DEVICE_CODE}"
echo "link_url=${LINK_URL}"

echo "-- link/complete"
RESP_COMPLETE="$(curl -fsS -X POST "${API_BASE}/agent/link/complete" \
  -H 'Content-Type: application/json' \
  -d "{\"device_code\":\"${DEVICE_CODE}\",\"user_id\":\"${USER_ID}\"}")"
AGENT_TOKEN="$(printf '%s' "${RESP_COMPLETE}" | json_field "agent_token")"
AGENT_ID="$(printf '%s' "${RESP_COMPLETE}" | json_field "agent_id")"
echo "agent_id=${AGENT_ID}"

echo "-- register-key"
RESP_REGISTER="$(curl -fsS -X POST "${API_BASE}/agent/register-key" \
  -H 'Content-Type: application/json' \
  -d "{\"agent_token\":\"${AGENT_TOKEN}\",\"public_key\":\"${PUB_KEY}\",\"local_port\":${AGENT_PORT}}")"
REMOTE_PORT="$(printf '%s' "${RESP_REGISTER}" | json_field "remote_port")"
SSH_HOST="$(printf '%s' "${RESP_REGISTER}" | json_field "ssh_host")"
SSH_USER="$(printf '%s' "${RESP_REGISTER}" | json_field "ssh_user")"
echo "remote_port=${REMOTE_PORT}"
echo "ssh_target=${SSH_USER}@${SSH_HOST}"

SERVER_URL="${API_BASE}/submit-scan"
ANNOUNCE_URL="${API_BASE}/agent/announce"
PUBLIC_BASE_URL="http://127.0.0.1:${REMOTE_PORT}"

echo "-- writing env: ${ENV_FILE}"
cat > "${ENV_FILE}" <<EOF
SERVER_URL=${SERVER_URL}
ANNOUNCE_URL=${ANNOUNCE_URL}
USER_ID=${USER_ID}
LIBRARY_PATH=${LIBRARY_PATH}
AGENT_PORT=${AGENT_PORT}
VALID_AUDIO_EXTENSIONS=.mp3,.flac,.wav,.m4a
PUBLIC_BASE_URL=${PUBLIC_BASE_URL}

TUNNEL_ENABLE=1
SSH_HOST=${SSH_HOST}
SSH_USER=${SSH_USER}
SSH_KEY_PATH=${KEY_PATH}
SSH_PORT=22
REMOTE_PORT=${REMOTE_PORT}
EOF

echo "-- heartbeat"
curl -fsS -X POST "${API_BASE}/agent/heartbeat" \
  -H 'Content-Type: application/json' \
  -d "{\"agent_token\":\"${AGENT_TOKEN}\",\"tunnel_ok\":true,\"last_scan\":$(date +%s)}" >/dev/null

echo "✅ Onboarding complete."
echo "env_file=${ENV_FILE}"
echo "To run agent:"
echo "  set -a; source ${ENV_FILE}; set +a"
echo "  python3 thin_agent.py"

if [[ "${RUN_AFTER}" -eq 1 ]]; then
  echo "-- starting thin_agent.py"
  set -a
  # shellcheck source=/dev/null
  source "${ENV_FILE}"
  set +a
  exec python3 thin_agent.py
fi
