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
  --agent-bin <path>      Agent binary path (default: ./radiotiker-thin-agent-vnext-latest-linux)
  --autostart             Install/update systemd user auto-start service (opt-in)
  --run                   Start thin_agent.py after writing env
  --gui                   Start thin_agent_gui.py after writing env (implies --run)
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
AGENT_BIN="./radiotiker-thin-agent-vnext-latest-linux"
INSTALL_AUTOSTART=0
RUN_AFTER=0
GUI_AFTER=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user-id) USER_ID="${2:-}"; shift 2 ;;
    --library-path) LIBRARY_PATH="${2:-}"; shift 2 ;;
    --api-base) API_BASE="${2:-}"; shift 2 ;;
    --agent-port) AGENT_PORT="${2:-}"; shift 2 ;;
    --device-name) DEVICE_NAME="${2:-}"; shift 2 ;;
    --key-path) KEY_PATH="${2:-}"; shift 2 ;;
    --env-file) ENV_FILE="${2:-}"; shift 2 ;;
    --agent-bin) AGENT_BIN="${2:-}"; shift 2 ;;
    --autostart) INSTALL_AUTOSTART=1; shift 1 ;;
    --run) RUN_AFTER=1; shift 1 ;;
    --gui) GUI_AFTER=1; RUN_AFTER=1; shift 1 ;;
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
  python3 -c '
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
' "$field"
}

is_json() {
  python3 -c 'import json,sys; json.load(sys.stdin)'
}

post_json_checked() {
  local url="$1"
  local payload="$2"
  local resp status body
  resp="$(curl -sS -w $'\n%{http_code}' -X POST "${url}" \
    -H 'Content-Type: application/json' \
    -d "${payload}")"
  status="$(printf '%s' "${resp}" | tail -n 1)"
  body="$(printf '%s' "${resp}" | sed '$d')"
  if [[ "${status}" != "200" ]]; then
    echo "Request failed: POST ${url} -> HTTP ${status}" >&2
    echo "Response body:" >&2
    echo "${body}" >&2
    exit 1
  fi
  if ! printf '%s' "${body}" | is_json >/dev/null 2>&1; then
    echo "Request failed: POST ${url} returned non-JSON body" >&2
    echo "Response body:" >&2
    echo "${body}" >&2
    exit 1
  fi
  printf '%s' "${body}"
}

install_autostart_service() {
  local env_src="$1"
  local run_cmd="$2"
  local cfg_dir="${HOME}/.config/radiotiker-vnext"
  local user_svc_dir="${HOME}/.config/systemd/user"
  local env_dst="${cfg_dir}/agent.env"
  local svc="${user_svc_dir}/radiotiker-vnext-agent.service"

  mkdir -p "${cfg_dir}" "${user_svc_dir}"
  cp "${env_src}" "${env_dst}"

  cat > "${svc}" <<EOF
[Unit]
Description=RadioTiker vNext Thin Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Restart=always
RestartSec=3
EnvironmentFile=${env_dst}
ExecStart=${run_cmd}

[Install]
WantedBy=default.target
EOF

  if command -v systemctl >/dev/null 2>&1; then
    if systemctl --user daemon-reload && systemctl --user enable --now radiotiker-vnext-agent.service; then
      echo "✅ Auto-start service installed: ${svc}"
    else
      echo "⚠️ Could not start user service automatically."
      echo "   Try manually:"
      echo "   systemctl --user daemon-reload"
      echo "   systemctl --user enable --now radiotiker-vnext-agent.service"
    fi
  fi

  if command -v loginctl >/dev/null 2>&1; then
    linger="$(loginctl show-user "${USER}" -p Linger 2>/dev/null | cut -d= -f2 || true)"
    if [[ "${linger}" != "yes" ]]; then
      echo "ℹ️ To keep agent running after reboot without login:"
      echo "   sudo loginctl enable-linger ${USER}"
    fi
  fi
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
RESP_START="$(post_json_checked "${API_BASE}/agent/link/start" \
  "{\"device_name\":\"${DEVICE_NAME}\",\"agent_version\":\"0.5.0\"}")"
DEVICE_CODE="$(printf '%s' "${RESP_START}" | json_field "device_code")"
LINK_URL="$(printf '%s' "${RESP_START}" | json_field "link_url")"
echo "device_code=${DEVICE_CODE}"
echo "link_url=${LINK_URL}"

echo "-- link/complete"
RESP_COMPLETE="$(post_json_checked "${API_BASE}/agent/link/complete" \
  "{\"device_code\":\"${DEVICE_CODE}\",\"user_id\":\"${USER_ID}\"}")"
AGENT_TOKEN="$(printf '%s' "${RESP_COMPLETE}" | json_field "agent_token")"
AGENT_ID="$(printf '%s' "${RESP_COMPLETE}" | json_field "agent_id")"
echo "agent_id=${AGENT_ID}"

echo "-- register-key"
RESP_REGISTER="$(post_json_checked "${API_BASE}/agent/register-key" \
  "{\"agent_token\":\"${AGENT_TOKEN}\",\"public_key\":\"${PUB_KEY}\",\"local_port\":${AGENT_PORT}}")"
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
ENABLE_ACOUSTID_SCAN=${ENABLE_ACOUSTID_SCAN:-0}
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

RUN_CMD=""
if [[ -f "thin_agent.py" ]]; then
  RUN_CMD="/usr/bin/python3 $(pwd)/thin_agent.py"
elif [[ -x "${AGENT_BIN}" ]]; then
  RUN_CMD="$(realpath "${AGENT_BIN}")"
fi

if [[ "${INSTALL_AUTOSTART}" -eq 1 && -n "${RUN_CMD}" ]]; then
  echo "-- installing auto-start"
  install_autostart_service "${ENV_FILE}" "${RUN_CMD}"
fi

echo "✅ Onboarding complete."
echo "env_file=${ENV_FILE}"
echo "To run agent:"
echo "  set -a; source ${ENV_FILE}; set +a"
echo "  python3 thin_agent.py"
if [[ -f "thin_agent_gui.py" ]]; then
  echo "  python3 thin_agent_gui.py   # GUI mode"
fi

if [[ "${RUN_AFTER}" -eq 1 ]]; then
  echo "-- starting agent"
  if [[ "${INSTALL_AUTOSTART}" -eq 1 ]] && command -v systemctl >/dev/null 2>&1; then
    if systemctl --user restart radiotiker-vnext-agent.service; then
      echo "✅ Started via user service: radiotiker-vnext-agent.service"
      echo "Check status: systemctl --user status radiotiker-vnext-agent.service --no-pager"
      exit 0
    fi
  fi
  set -a
  # shellcheck source=/dev/null
  source "${ENV_FILE}"
  set +a
  if [[ "${GUI_AFTER}" -eq 1 ]] && [[ -f "thin_agent_gui.py" ]]; then
    exec python3 thin_agent_gui.py
  fi
  if [[ -f "thin_agent.py" ]]; then
    exec python3 thin_agent.py
  fi
  if [[ -x "${AGENT_BIN}" ]]; then
    exec "${AGENT_BIN}"
  fi
  echo "Could not start agent: thin_agent.py not found and --agent-bin not executable (${AGENT_BIN})" >&2
  echo "Run manually by either:" >&2
  echo "  1) cd to repo thin-agent folder and run python3 thin_agent.py" >&2
  echo "  2) pass --agent-bin /path/to/radiotiker-thin-agent-vnext-latest-linux --run" >&2
  exit 1
fi
