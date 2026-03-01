# Reverse Tunnel (Phase 1) — SSH Design

This document defines the Phase 1 connectivity model for RadioTiker vnext.
Goal: "turn it on and it works" without manual router configuration.

## Summary

Each thin agent opens an outbound SSH reverse tunnel to the server.
The server reaches the agent's local file server through that tunnel.

## Defaults (can change later)

- Tunnel endpoint (SSH): `tunnel.radio.tiker.es`
- Public API/UI: `next.radio.tiker.es`
- Server user: `rtunnel`
- Repo/workdir: `~/RadioTiker-vnext`

## Components

1. Thin Agent (user device)
   - Local file server (HTTP) bound to `127.0.0.1:<LOCAL_PORT>`
   - SSH reverse tunnel to the server
   - Auto-start on boot (per-OS)

2. Server (Hetzner)
   - SSH daemon allows reverse tunnels for `rtunnel`
   - Streamer API talks to agent via the tunnel endpoint

## Tunnel Shape

The agent opens:
```
ssh -N -T -o ExitOnForwardFailure=yes \
  -R <REMOTE_PORT>:127.0.0.1:<LOCAL_PORT> \
  rtunnel@tunnel.radio.tiker.es
```

Server then reaches the agent at:
```
http://127.0.0.1:<REMOTE_PORT>/
```

The Streamer API stores the agent base URL as:
```
http://127.0.0.1:<REMOTE_PORT>
```

## Per-User Keys

Each agent uses its own SSH key pair:
1. Agent generates key pair locally.
2. User completes onboarding, receiving a one-time token.
3. Agent posts its public key to the server.
4. Server adds the key to `~rtunnel/.ssh/authorized_keys` with restrictions.

Recommended restrictions:
- `command="...optional forced command..."`
- `permitopen="127.0.0.1:<REMOTE_PORT>"`
- `no-pty,no-agent-forwarding,no-user-rc,no-X11-forwarding`

## Onboarding Flow (High Level)

1. User signs up on `next.radio.tiker.es`.
2. User downloads the agent and runs it.
3. Agent opens a browser to complete device linking.
4. User logs in, selects a plan, and authorizes the device.
5. Agent sends its public key + local port.
6. Server assigns a remote port and returns tunnel instructions.
7. Agent starts the SSH tunnel and heartbeats.

## Agent Behavior

- Maintain tunnel (reconnect with backoff).
- Log status locally.
- Heartbeat to server every 30s with:
  - agent_id
  - local_port
  - tunnel_status
  - last_scan_time

## Auto-Start (UX Goal)

We want "install once and forget it."

OS-specific:
1. Linux: systemd user service
2. macOS: LaunchAgent plist
3. Windows: Windows Service (or Task Scheduler for v1)

## Failure Modes

1. Tunnel drops → agent reconnects.
2. Server restarts → agent reconnects.
3. NAS offline → scan fails, surface in UI.

## Security Notes

- Only allow reverse tunnel from agent keys.
- Restrict `authorized_keys` for each user.
- Do not expose the remote port publicly (keep bound to localhost).

## Plans (Draft)

We will start with two plans to keep onboarding simple:

1. Free
   - Limited library size (e.g., 300 tracks)
   - 1 station
   - Standard bitrate
   - Intended for evaluation

2. Pro
   - Unlimited library size
   - Multiple stations
   - Higher bitrate and advanced features (leveling, metadata tools)

Plan status gates:
1. Device linking
2. Library scans
3. Station publishing

## Next Steps

1. Implement tunnel manager in the agent.
2. Add server endpoints for key registration and port assignment.
3. Add UI feedback: agent connected / last seen / tunnel status.
