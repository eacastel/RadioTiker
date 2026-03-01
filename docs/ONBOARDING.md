# Onboarding (Draft)

This document defines the device-linking flow for the thin agent and the plan gating model.

## Goals

1. Zero manual networking for users.
2. Simple, secure device linking.
3. Enforce plan limits at onboarding and during scans.

## Flow: Device Linking (Browser)

1. User runs the agent.
2. Agent requests a device link code from the server.
3. Agent opens a browser to the link URL.
4. User logs in, chooses a plan (Free/Pro), and approves the device.
5. Server issues a short-lived agent token.
6. Agent uploads its SSH public key + local port.
7. Server assigns a remote port and returns tunnel settings.
8. Agent starts the tunnel and begins heartbeats.

## API Endpoints (Proposed)

### 1) Start device link
`POST /api/agent/link/start`

Request:
```
{ "device_name": "My NAS", "agent_version": "0.5.0" }
```

Response:
```
{
  "device_code": "ABCD-1234",
  "link_url": "https://next.radio.tiker.es/link/ABCD-1234",
  "expires_in": 600
}
```

### 2) Complete device link (server-side)
`POST /api/agent/link/complete`

Triggered after user approves the device in the web UI.

Response:
```
{ "agent_token": "..." }
```

### 3) Register agent key
`POST /api/agent/register-key`

Request:
```
{
  "agent_token": "...",
  "public_key": "ssh-ed25519 AAAA...",
  "local_port": 8765
}
```

Response:
```
{
  "remote_port": 44001,
  "ssh_user": "rtunnel",
  "ssh_host": "tunnel.radio.tiker.es"
}
```

### 4) Agent heartbeat
`POST /api/agent/heartbeat`

Request:
```
{
  "agent_token": "...",
  "tunnel_ok": true,
  "last_scan": 1770411200
}
```

Response:
```
{ "ok": true }
```

## Plan Gating

Plan status gates:
1. Device linking
2. Library scans
3. Station publishing

Example limits:
- Free: 300 tracks, 1 station
- Pro: unlimited

## Security Notes

- Agent token is short-lived and rotated on re-link.
- SSH public keys are per-user and stored server-side.
- Remote ports are assigned by server and bound to localhost only.
