# Database Schema (PostgreSQL) — Draft

This document defines the initial PostgreSQL schema for RadioTiker vnext.
Goals: scalable catalog, metadata normalization, station curation, and plan gating.

## Guiding Principles

1. Store raw metadata alongside normalized fields.
2. Separate file identity from tags (file can be retagged).
3. Keep scan history to support rescans and health checks.

## Core Tables

### users
- `id` (uuid, pk)
- `email` (text, unique)
- `plan` (text: free|pro)
- `plan_status` (text: active|past_due|canceled)
- `created_at` (timestamptz)

### agents
- `id` (uuid, pk)
- `user_id` (uuid, fk → users.id)
- `name` (text)
- `public_key` (text)
- `ssh_user` (text)
- `ssh_host` (text)
- `remote_port` (int)
- `local_port` (int)
- `last_seen_at` (timestamptz)
- `created_at` (timestamptz)

### tracks
Represents a canonical logical track chosen for playback/search/metadata.

- `id` (uuid, pk)
- `user_id` (uuid, fk → users.id)
- `canonical_key` (text, nullable)   # stable dedupe key / future merge key
- `preferred_source_id` (uuid, nullable, fk → track_sources.id)
- `duration_sec` (numeric)
- `codec` (text)
- `bitrate` (int)
- `sample_rate` (int)
- `channels` (int)
- `playability_status` (text: ok|flaky|bad)
- `playability_fail_count` (int)
- `playability_last_error` (text)
- `favorite` (bool, default false)
- `hidden` (bool, default false)
- `created_at` (timestamptz)
- `updated_at` (timestamptz)

### track_sources
Represents one observed file instance discovered by an agent.

- `id` (uuid, pk)
- `track_id` (uuid, fk → tracks.id)
- `user_id` (uuid, fk → users.id)
- `agent_id` (uuid, fk → agents.id)
- `rel_path` (text)               # normalized relative path
- `file_size` (bigint)
- `mtime` (bigint)
- `checksum` (text, nullable)     # optional, for integrity
- `duration_sec` (numeric)
- `codec` (text)
- `bitrate` (int)
- `sample_rate` (int)
- `channels` (int)
- `source_rank` (int)             # preferred file quality / source order
- `is_available` (bool)
- `last_seen_at` (timestamptz)
- `created_at` (timestamptz)
- `updated_at` (timestamptz)

### source_tags
Raw embedded tags extracted from file instances.

- `track_source_id` (uuid, fk → track_sources.id, pk)
- `title` (text)
- `artist` (text)
- `album` (text)
- `track_no` (text)
- `disc_no` (text)
- `year` (text)
- `genre` (text)
- `album_artist` (text)
- `composer` (text)
- `raw_json` (jsonb)              # full raw tag dump

### artists
- `id` (uuid, pk)
- `name` (text, unique)

### albums
- `id` (uuid, pk)
- `title` (text)
- `artist_id` (uuid, fk → artists.id)
- `year` (text)

### tracks_norm
Normalized references for search/browse (derived from canonical track metadata).

- `track_id` (uuid, pk, fk → tracks.id)
- `artist_id` (uuid, fk → artists.id)
- `album_id` (uuid, fk → albums.id)
- `title` (text)
- `track_no` (int)
- `disc_no` (int)

### stations
- `id` (uuid, pk)
- `user_id` (uuid, fk → users.id)
- `name` (text)
- `description` (text)
- `public_slug` (text, unique)
- `is_public` (bool)
- `created_at` (timestamptz)

### station_tracks
- `station_id` (uuid, fk → stations.id)
- `track_id` (uuid, fk → tracks.id)
- `position` (int)
- primary key (`station_id`, `track_id`)

### scans
- `id` (uuid, pk)
- `user_id` (uuid, fk → users.id)
- `agent_id` (uuid, fk → agents.id)
- `started_at` (timestamptz)
- `ended_at` (timestamptz)
- `status` (text: running|ok|failed)
- `stats` (jsonb)                 # counts, errors

### health_checks
- `id` (uuid, pk)
- `track_id` (uuid, fk → tracks.id)
- `status` (text: ok|warning|error)
- `details` (jsonb)
- `checked_at` (timestamptz)

### track_analysis
Server-side audio analysis results.

- `track_id` (uuid, pk, fk → tracks.id)
- `analysis_version` (int)
- `tempo_bpm` (numeric)
- `key` (text)                 # e.g. C, C#, D
- `mode` (text)                # major|minor
- `energy` (numeric)           # 0..1
- `valence` (numeric)          # 0..1
- `danceability` (numeric)     # 0..1
- `loudness_lufs` (numeric)
- `spectral_centroid` (numeric)
- `analysis_json` (jsonb)      # raw features for future use
- `created_at` (timestamptz)
- `updated_at` (timestamptz)

## Indexes (initial)

- `tracks (user_id, hidden, favorite)`
- `track_sources (user_id, agent_id, rel_path)`
- `track_sources (user_id, mtime)`
- `track_sources (track_id, is_available, source_rank)`
- `tracks_norm (artist_id)`
- `tracks_norm (album_id)`
- `stations (user_id)`
- `station_tracks (station_id, position)`

## Plan Gating Examples

Free plan limits:
- max tracks: 300
- max stations: 1

Enforce at:
1. Device linking
2. Scan ingestion
3. Station creation

## Next Steps

1. Implement migrations.
2. Add server scan ingest to write `track_sources` + `source_tags`.
3. Add canonicalization job to choose/create `tracks` from source instances.
4. Add normalization job to populate artists/albums/tracks_norm.
5. Add server-side analysis worker to populate `track_analysis`.

## Transition Notes

1. Existing MySQL `tracks.canonical_json` can act as a temporary canonical-track cache during rollout.
2. JSON per-user libraries are fallback/cache only; DB should become the system of record.
3. Multi-user onboarding requires all dedupe keys and source-instance rows to be scoped by `user_id`.
4. A single song may have multiple file instances for one user; do not model path/mtime as the logical track identity.
