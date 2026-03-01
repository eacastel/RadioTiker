# Server Analysis Pipeline (Draft)

This document describes server-side analysis that runs after scan ingest.
The user can start playback immediately while background jobs complete.

## Goals

1. Detect file errors early (decode failures, corruption).
2. Compute loudness for volume normalization.
3. Extract features for AI playlists (tempo, key, mood).

## Flow

1. Agent scan is ingested and stored in the DB.
2. A background worker processes tracks in a slow, steady queue.
3. Each track is analyzed and results are stored in:
   - `health_checks`
   - `track_analysis`

## Health Checks

Checks to run:
1. Decode test (ffprobe/ffmpeg).
2. Duration sanity (non-zero, matches metadata).
3. Readability (file accessible via tunnel).

## Volume Normalization

Compute loudness (LUFS) on the server:
1. Store in `track_analysis.loudness_lufs`
2. Use at playback time to normalize volume

## Background Scheduling

- Queue jobs on scan ingest.
- Process in small batches to avoid server overload.
- Track progress per user in `scans.stats`.

## Next Steps

1. Implement analysis worker.
2. Add admin UI status: "analysis pending / complete".
