# RadioTiker vNext Roadmap

Last updated: 2026-02-13
Scope: `RadioTiker-vnext`

This roadmap defines milestone gates from current vnext status through track normalization, metadata enrichment, and analysis.

## Current Snapshot (as of 2026-02-13)

Implemented/in-progress:
1. `core/streamer_api/routes/agent.py` adds v1 onboarding/tunnel APIs:
   - `POST /api/agent/link/start`
   - `POST /api/agent/link/complete`
   - `POST /api/agent/register-key`
   - `POST /api/agent/heartbeat`
2. `thin-agent/tunnel_manager.py` exists with SSH reverse tunnel lifecycle/reconnect logic.
3. `thin-agent/thin_agent.py` can start tunnel from env and keep process alive.
4. Draft architecture docs exist:
   - `docs/REVERSE_TUNNEL.md`
   - `docs/ONBOARDING.md`
   - `docs/DB_SCHEMA.md`
   - `docs/ANALYSIS_PIPELINE.md`

Not yet complete:
1. No persistent DB-backed auth/device/session model (still file + in-memory records).
2. Device-link UX and approval screens are not implemented end-to-end.
3. Tunnel key install/rotation on host SSH side is not automated.
4. DB migrations and worker jobs for normalization/analysis are not implemented.
5. Player reliability baseline from `RadioTiker` mainline has not been formalized as a vnext release gate.

## Milestones

## M0 - Stabilize Current Playback Baseline

Goal: vnext must not regress known playback reliability from the current stack.

Deliverables:
1. Bring current relay/player fixes into vnext parity.
2. Add a minimal playback regression checklist (desktop + mobile).
3. Capture known problematic track IDs and test behavior.

Exit criteria:
1. FLAC fallback works consistently via `/relay-mp3`.
2. No repeated immediate same-track loops.
3. Timeline/time display is present where expected.
4. Mobile playback startup acceptable on typical home network.

## M1 - Agent Linking + Tunnel MVP (Single Happy Path)

Goal: first-time user can link a device and stream without manual port forwarding.

Deliverables:
1. Finalize `agent` API contracts and error handling.
2. Add link approval UI flow (`/link/{device_code}`).
3. Persist agent identity/token/port assignments in DB (not in-memory only).
4. Host-side key registration flow for `rtunnel` account.
5. Tunnel health status visible in user UI.

Exit criteria:
1. New device can be linked in <= 5 minutes.
2. Agent reconnects automatically after server restart.
3. Server can fetch track bytes through assigned tunnel port.

## M2 - Plan Gating + Operational Guardrails

Goal: enforce product limits and prevent accidental overuse.

Deliverables:
1. Plan model (`free`, `pro`) in DB with gating checks.
2. Enforce limits on device linking, scan ingestion, station creation.
3. Basic admin/user observability for:
   - linked devices
   - last heartbeat
   - active tunnel status
4. Operational runbook for tunnel failures and key rotation.

Exit criteria:
1. Limit breaches return deterministic API errors.
2. UI reflects plan constraints clearly.
3. On-call can diagnose tunnel/user issues with logs alone.

## M3 - DB Ingest Foundation (Transition Milestone)

Goal: move from JSON-file libraries to durable relational ingest pipeline.

Deliverables:
1. Implement migrations from `docs/DB_SCHEMA.md` baseline:
   - `users`, `agents`, `tracks`, `track_sources`, `source_tags`, `scans`
2. Write scan ingest path into DB as file-instance observations while preserving compatibility playback.
3. Add scan history and ingest metrics per user/agent.
4. Keep current JSON fallback during rollout (feature-flagged).
5. Pilot DB-backed library reads per user.

Exit criteria:
1. New scans persist to DB successfully.
2. Playback reads from DB-backed track records for pilot users.
3. Source instances survive rescans/restarts without recreating logical track state.
4. Rollback path exists and is tested.

## M4 - Track Normalization + Metadata (Requested Focus Shift)

Goal: establish clean browse/search entities and high-quality metadata.

Deliverables:
1. Normalization job:
   - populate `artists`, `albums`, `tracks_norm`
   - parse `track_no`, `disc_no`, canonical artist/album/title forms
2. Metadata enrichment pass:
   - preserve raw tags in `tags.raw_json`
   - enrich missing fields (provider strategy to be finalized)
3. Conflict policy:
   - embedded tags vs enriched tags precedence
   - audit trail for changes
4. API updates for normalized browse/search endpoints.

Exit criteria:
1. Duplicate artist/album fragmentation reduced with measurable KPI.
2. Search/browse uses normalized entities by default.
3. Original raw tags remain recoverable.

## M5 - Analysis + Quality Layer

Goal: compute file health and audio features for reliable playback and smart curation.

Deliverables:
1. Background worker for health checks + audio analysis.
2. Populate `health_checks` and `track_analysis`.
3. Add progress/status surfaces in UI and API.
4. Use loudness/quality signals to improve playback defaults.

Exit criteria:
1. Newly ingested tracks are analyzed asynchronously.
2. Health errors are actionable in UI.
3. Analysis does not block basic playback.

## M6 - Station Curation + Sharing

Goal: deliver the DJ/listener product layer.

Deliverables:
1. Station CRUD + ordered track assignment.
2. Public/private sharing controls.
3. Listener playback URLs with access checks.

Exit criteria:
1. User can create/publish at least one station end-to-end.
2. Free/Pro gating enforced on station counts.

## Cross-Cutting Release Rules

For every milestone:
1. Define rollback plan before deployment.
2. Ship observability (logs + minimal metrics) with feature.
3. Add/update docs in `docs/` as part of done criteria.
4. Run playback regression checklist before and after infra changes.

## Immediate Next 2 Sprints (Recommended)

Sprint A:
1. Finish M0 regression checklist and automate basic smoke checks.
2. Harden M1 contracts and move token/device state out of in-memory maps.
3. Implement minimal link approval page + API integration.

Sprint B:
1. Implement DB migrations and initial ingest write path (M3).
2. Run pilot with one user and validate rollback.
3. Define exact normalization rules for M4 and lock acceptance tests.

## DB-First Transition Notes

For multi-user onboarding, the relational DB must become the system of record.

Rules:
1. Per-user JSON libraries are fallback/cache only during transition.
2. New DB-backed read paths must be feature-flagged for rollout.
3. Deletion/reset paths must clear DB state, not just JSON cache.
4. Track identity must evolve toward canonical track + file-instance separation.

Current transition flag:
1. `RT_DB_CANONICAL_READS=1`
2. When enabled, library reads prefer DB `tracks.canonical_json` for that user and fall back to JSON only if DB is unavailable.

Next DB-first implementation steps:
1. Add `track_sources` migration and storage helpers.
2. Write scan ingest into `track_sources` per user/agent.
3. Read canonical `tracks` through preferred `track_sources` when DB-backed reads are enabled.
4. Track canonical `tracks` separately from observed files.
5. Move favorites/hidden/playability to canonical `tracks`.
6. Use full replace-scans to mark unseen `track_sources` unavailable so broken/missing file instances can be identified systematically.
