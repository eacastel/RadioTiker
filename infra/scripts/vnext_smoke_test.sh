#!/usr/bin/env bash
set -euo pipefail

# Local smoke test for vNext API + agent onboarding path.
# Runs against localhost uvicorn endpoint (default: 127.0.0.1:8091).
#
# Usage:
#   bash infra/scripts/vnext_smoke_test.sh eacastel
#   API_BASE=http://127.0.0.1:8091/api bash infra/scripts/vnext_smoke_test.sh eacastel

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <user_id>" >&2
  exit 2
fi

USER_ID="$1"
API_BASE="${API_BASE:-http://127.0.0.1:8091/api}"

echo "== vnext smoke test =="
echo "API_BASE=${API_BASE}"
echo "USER_ID=${USER_ID}"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

json_post() {
  local url="$1"
  local payload="$2"
  curl -fsS -H 'Content-Type: application/json' -X POST "$url" -d "$payload"
}

extract_json_field() {
  local json="$1"
  local field="$2"
  python3 - "$json" "$field" <<'PY'
import json,sys
obj=json.loads(sys.argv[1])
cur=obj
for part in sys.argv[2].split("."):
    if isinstance(cur, dict):
        cur=cur.get(part)
    else:
        cur=None
        break
if cur is None:
    sys.exit(1)
print(cur)
PY
}

echo "-- Health check"
curl -fsS "${API_BASE}/health" ; echo

echo "-- link/start"
resp_start="$(json_post "${API_BASE}/agent/link/start" '{"device_name":"smoke-test","agent_version":"0.5.0"}')"
echo "$resp_start"
device_code="$(extract_json_field "$resp_start" "device_code")"

echo "-- link/complete"
resp_complete="$(json_post "${API_BASE}/agent/link/complete" "{\"device_code\":\"${device_code}\",\"user_id\":\"${USER_ID}\"}")"
echo "$resp_complete"
agent_token="$(extract_json_field "$resp_complete" "agent_token")"
agent_id="$(extract_json_field "$resp_complete" "agent_id")"

echo "-- generate temp key"
ssh-keygen -t ed25519 -N '' -f "${tmpdir}/agent_key" -C "radiotiker-smoke-${USER_ID}" >/dev/null
pubkey="$(cat "${tmpdir}/agent_key.pub")"

echo "-- register-key (provisions rtunnel authorized_keys via API)"
resp_register="$(json_post "${API_BASE}/agent/register-key" "$(cat <<JSON
{"agent_token":"${agent_token}","public_key":"${pubkey}","local_port":8765}
JSON
)")"
echo "$resp_register"
remote_port="$(extract_json_field "$resp_register" "remote_port")"

echo "-- heartbeat"
resp_hb="$(json_post "${API_BASE}/agent/heartbeat" "{\"agent_token\":\"${agent_token}\",\"tunnel_ok\":true,\"last_scan\":$(date +%s)}")"
echo "$resp_hb"

echo "-- agent status"
curl -fsS "${API_BASE%/api}/api/agent/${USER_ID}/status" ; echo

echo "-- done"
echo "agent_id=${agent_id}"
echo "remote_port=${remote_port}"
echo "temp_private_key=${tmpdir}/agent_key"
echo "To test SSH tunnel manually:"
echo "  ssh -N -T -o ExitOnForwardFailure=yes -i ${tmpdir}/agent_key -R ${remote_port}:127.0.0.1:8765 rtunnel@tunnel.radio.tiker.es"
