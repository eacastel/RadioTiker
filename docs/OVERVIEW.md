# RadioTiker Overview (Draft)

This doc captures the high-level product flow and the current network model.

## User Flow (Current)

1. User signs up on the RadioTiker server.
2. User downloads the thin agent for their OS.
3. User runs the agent and selects one or more music folders.
4. Agent scans local files and announces its local file server URL.
5. Server indexes the library and serves playback through the web UI.

## Network Model (Current)

The server must be able to reach the agent's local file server.

Supported today:
1. Tailscale between user network and Hetzner.
2. Port forwarding from the user's router to the agent.
3. Manual VPN or reverse proxy.

## Network Model (Target)

Goal: "turn it on and it works" without manual routing.

Chosen approach (Phase 1):
1. Agent-initiated SSH reverse tunnel to the server.

See `docs/REVERSE_TUNNEL.md` for the detailed design and onboarding flow.

## Metadata and Library

Current metadata comes from the agent scan.
Future work:
1. Server-side metadata enrichment and artwork.
2. Health checks (missing tags, decode failures, duration mismatches).
3. Admin UI for editing and curation.

## Sharing Model (Draft)

Primary users are DJs who curate one or more "radio stations" and share them with friends and family.

Goals:
1. Each DJ can publish multiple stations (themes/genres/sets).
2. Each station has a shareable link or invite.
3. Listeners can play without installing the agent.

## Plans (Draft)

We will start with two plans:

1. Free
   - Limited library size (evaluation tier)
   - 1 station

2. Pro
   - Unlimited library size
   - Multiple stations

Plan status will gate device linking, scans, and station publishing.

## Next Steps (Doc Stub)

1. Define the signup flow and API keys.
2. Define a single recommended network setup for users.
3. Add database-backed library storage.
