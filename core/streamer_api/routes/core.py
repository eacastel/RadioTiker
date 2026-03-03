from typing import Dict, Any, Optional
import json, time, requests
import os, subprocess, re
import uuid
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse, RedirectResponse, Response
from ..models import (
    ScanPayload,
    AnnouncePayload,
    PlaylistCreatePayload,
    PlaylistTrackUpdatePayload,
    MetadataLibraryUpsertPayload,
    MetadataEnrichPayload,
    MetadataEnrichLibraryPayload,
)
from ..storage import (
    load_lib,
    save_lib,
    load_agent,
    save_agent_stable,
    load_playlists,
    save_playlists,
    load_metadata_library,
    save_metadata_library,
    db_upsert_tracks,
    db_insert_provider_snapshot,
    db_upsert_override,
    db_find_metadata_seed,
)
from ..utils import normalize_rel_path, build_stream_url, enrich_track_metadata, normalize_text_key
from ..metadata_providers import search_candidates


router = APIRouter(prefix="/api", tags=["core"])

METADATA_PATCH_FIELDS = {
    "title", "artist", "album", "album_artist", "genre",
    "year", "track_no", "disc_no", "composer", "bpm", "musical_key",
    "artwork_url", "artwork_urls", "artist_image_urls",
    "artist_bio", "album_bio",
}


def _is_weak_text(v: Any) -> bool:
    s = str(v or "").strip()
    if not s:
        return True
    k = normalize_text_key(s)
    return k in {"unknown", "unknown artist", "unknown album", "n a", "na"}


def _is_generic_title(v: Any) -> bool:
    s = str(v or "").strip().lower()
    return bool(re.fullmatch(r"(track|song)\s*\d{1,3}", s))


def _apply_seed_metadata(current: Dict[str, Any], seed: Dict[str, Any]) -> Dict[str, Any]:
    """
    Use same-user historical metadata as a conservative seed.
    """
    out = dict(current)
    if not seed:
        return out

    for fld in ("title", "artist", "album"):
        cur = out.get(fld)
        src = seed.get(fld)
        if fld == "title":
            if (_is_weak_text(cur) or _is_generic_title(cur)) and src and not _is_weak_text(src):
                out[fld] = src
        else:
            if _is_weak_text(cur) and src and not _is_weak_text(src):
                out[fld] = src

    for fld in ("year", "genre"):
        if not out.get(fld) and seed.get(fld):
            out[fld] = seed.get(fld)

    if not out.get("artwork_url") and seed.get("artwork_url"):
        out["artwork_url"] = seed.get("artwork_url")
    if not (out.get("artist_image_urls") or []) and (seed.get("artist_image_urls") or []):
        out["artist_image_urls"] = seed.get("artist_image_urls")
    if not str(out.get("artist_bio") or "").strip() and str(seed.get("artist_bio") or "").strip():
        out["artist_bio"] = seed.get("artist_bio")
    if not str(out.get("album_bio") or "").strip() and str(seed.get("album_bio") or "").strip():
        out["album_bio"] = seed.get("album_bio")

    # Pull additional media arrays from canonical JSON when available.
    cj = seed.get("canonical_json") if isinstance(seed.get("canonical_json"), dict) else {}
    if isinstance(cj, dict):
        if not (out.get("artwork_urls") or []) and (cj.get("artwork_urls") or []):
            out["artwork_urls"] = cj.get("artwork_urls")
        if not (out.get("artist_image_urls") or []) and (cj.get("artist_image_urls") or []):
            out["artist_image_urls"] = cj.get("artist_image_urls")
    return out


def _track_matches_entry(track: Dict[str, Any], entry: Dict[str, Any]) -> bool:
    mt = normalize_text_key(entry.get("match_title"))
    ma = normalize_text_key(entry.get("match_artist"))
    mal = normalize_text_key(entry.get("match_album"))
    if mt and normalize_text_key(track.get("title")) != mt:
        return False
    if ma and normalize_text_key(track.get("artist")) != ma:
        return False
    if mal and normalize_text_key(track.get("album")) != mal:
        return False
    return bool(mt or ma or mal)


def _apply_metadata_library_patch(track: Dict[str, Any], user_id: str) -> Dict[str, Any]:
    lib = load_metadata_library(user_id)
    entries = lib.get("entries", [])
    out = dict(track)
    for entry in entries:
        if not _track_matches_entry(out, entry):
            continue
        patch = entry.get("patch") or {}
        for k, v in patch.items():
            if k in METADATA_PATCH_FIELDS and v is not None:
                out[k] = v
    return out


def _store_metadata_patch_rule(
    user_id: str,
    src_track: Dict[str, Any],
    patch: Dict[str, Any],
    provider: str,
    score: float,
):
    store = load_metadata_library(user_id)
    now = int(time.time())
    rule = {
        "entry_id": str(uuid.uuid4()),
        "match_title": src_track.get("title"),
        "match_artist": src_track.get("artist"),
        "match_album": src_track.get("album"),
        "patch": {k: v for k, v in patch.items() if k in METADATA_PATCH_FIELDS and v is not None and v != ""},
        "source": {"provider": provider, "score": score},
        "created_at": now,
        "updated_at": now,
    }
    if not rule["patch"]:
        return None
    store.setdefault("entries", []).append(rule)
    store["version"] = now
    save_metadata_library(user_id, store)
    return rule

def _probe_duration_sec(url: str) -> Optional[float]:
    """
    Best-effort duration probe for a remote media URL.
    Returns seconds as float or None on failure.
    """
    try:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            url,
        ]
        out = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=12,
            check=False,
            text=True,
        ).stdout.strip()
        if not out:
            return None
        val = float(out)
        return val if val > 0 else None
    except Exception:
        return None

def _ffmpeg_cmd_for_http_input(url: str, abr_kbps: int = 192, start_sec: float = 0.0) -> list[str]:
    # 192 kbps CBR is a sweet spot for mobile/Bluetooth reliability.
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel", "warning",

        # INPUT resiliency (HTTP over variable links)
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_on_network_error", "1",
        "-reconnect_at_eof", "1",
        "-reconnect_delay_max", "2",

        # Lower end-to-end latency / avoid input buffering
        "-fflags", "+nobuffer",
    ]
    if start_sec > 0:
        # Input-side seek avoids restarting from 0 when player asks for timeline offsets.
        cmd += ["-ss", f"{start_sec:.3f}"]
    cmd += [
        "-i", url,
        "-map", "0:a:0",
        "-sn",
        "-dn",

        "-vn",
        "-ac", "2",
        "-ar", "44100",

        # Stable CBR stream for radios/BT stacks
        "-codec:a", "libmp3lame",
        "-b:a", f"{abr_kbps}k",
        "-maxrate", f"{abr_kbps}k",
        "-bufsize", f"{abr_kbps*2}k",
        "-write_xing", "0",        # don't wait to write VBR headers

        "-f", "mp3",
        "-"                        # stdout
    ]
    return cmd



# -------- debug --------
@router.get("/debug/peek/{user_id}/{track_id}")
def debug_peek(user_id: str, track_id: str):
    """
    Show the exact upstream URL and the result of a HEAD request.
    """
    lib = load_lib(user_id)
    t = lib["tracks"].get(track_id)
    if not t:
        raise HTTPException(status_code=404, detail="Unknown track_id")

    built = build_stream_url(user_id, t)
    if not built:
        return {
            "ok": False,
            "reason": "no_base_or_rel_path",
            "agent_state": load_agent(user_id),
            "track_rel_path": t.get("rel_path"),
        }

    result = {"ok": True, "url": built}
    try:
        r = requests.head(built, timeout=(5, 15), allow_redirects=True,
                          headers={"User-Agent": "RadioTiker-Relay/peek"})
        result.update({
            "head_status": r.status_code,
            "head_headers": dict(r.headers),
        })
    except Exception as e:
        result.update({
            "head_status": None,
            "error": f"{type(e).__name__}: {e}",
        })
    return result

# -------- health --------
@router.get("/health")
def health():
    return {"ok": True}

# -------- library mgmt --------
@router.post("/library/{user_id}/clear")
def clear_library(user_id: str):
    lib = load_lib(user_id)
    lib["tracks"] = {}
    lib["version"] = int(time.time())
    lib["_cleared_for"] = 0
    save_lib(user_id, lib)
    return {"ok": True, "cleared": True, "version": lib["version"]}

@router.post("/library/{user_id}/migrate-relpaths")
def migrate_relpaths(user_id: str):
    """One-time normalization of existing rel_path values in the saved library."""
    lib = load_lib(user_id)
    tracks = lib.get("tracks", {})
    changed = 0
    for _, v in tracks.items():
        rel = v.get("rel_path")
        if not rel:
            continue
        new_rel = normalize_rel_path(rel)
        if new_rel != rel:
            v["rel_path"] = new_rel
            changed += 1
    if changed:
        lib["version"] = int(time.time())
        save_lib(user_id, lib)
    return {"ok": True, "changed": changed, "count": len(tracks), "version": lib["version"]}


@router.post("/library/{user_id}/rebuild-metadata")
def rebuild_metadata(user_id: str):
    """
    Recompute normalized metadata fields for already-ingested tracks.
    """
    lib = load_lib(user_id)
    tracks = lib.get("tracks", {})
    changed = 0
    for _, v in tracks.items():
        enriched = enrich_track_metadata(v)
        if any(v.get(k) != val for k, val in enriched.items()):
            v.update(enriched)
            changed += 1
    if changed:
        lib["version"] = int(time.time())
        save_lib(user_id, lib)
    return {"ok": True, "changed": changed, "count": len(tracks), "version": lib["version"]}


@router.get("/metadata-library/{user_id}")
def list_metadata_library(user_id: str):
    store = load_metadata_library(user_id)
    return {"entries": store.get("entries", []), "version": store.get("version")}


@router.post("/metadata-library/{user_id}/upsert")
def upsert_metadata_library(user_id: str, payload: MetadataLibraryUpsertPayload):
    store = load_metadata_library(user_id)
    now = int(time.time())
    entry = {
        "entry_id": str(uuid.uuid4()),
        "match_title": payload.match_title,
        "match_artist": payload.match_artist,
        "match_album": payload.match_album,
        "patch": {k: v for k, v in (payload.patch or {}).items() if k in METADATA_PATCH_FIELDS},
        "created_at": now,
        "updated_at": now,
    }
    if not entry["patch"]:
        raise HTTPException(status_code=400, detail="Patch must include at least one supported metadata field")
    if not (entry["match_title"] or entry["match_artist"] or entry["match_album"]):
        raise HTTPException(status_code=400, detail="At least one match field is required")
    store.setdefault("entries", []).append(entry)
    store["version"] = now
    save_metadata_library(user_id, store)
    return {"ok": True, "entry": entry}


@router.post("/library/{user_id}/apply-metadata-library")
def apply_metadata_library(user_id: str):
    lib = load_lib(user_id)
    tracks = lib.get("tracks", {})
    changed = 0
    for tid, track in tracks.items():
        patched = _apply_metadata_library_patch(track, user_id)
        patched.update(enrich_track_metadata(patched))
        if any(track.get(k) != patched.get(k) for k in patched.keys()):
            tracks[tid] = patched
            changed += 1
    if changed:
        lib["version"] = int(time.time())
        save_lib(user_id, lib)
    return {"ok": True, "changed": changed, "count": len(tracks), "version": lib["version"]}


@router.post("/metadata/enrich/{user_id}/{track_id}")
def metadata_enrich_track(user_id: str, track_id: str, payload: MetadataEnrichPayload):
    lib = load_lib(user_id)
    track = lib.get("tracks", {}).get(track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Unknown track_id")

    providers = payload.providers or ["musicbrainz", "discogs"]
    searched = search_candidates(track, providers=providers, include_errors=True)
    candidates = searched.get("candidates", [])
    provider_errors = searched.get("errors", [])
    if not candidates:
        return {
            "ok": True,
            "matched": False,
            "track_id": track_id,
            "candidates": [],
            "provider_errors": provider_errors,
        }

    best = candidates[0]
    min_score = float(payload.min_score or 0.78)
    accepted = best.get("score", 0) >= min_score
    rule = None
    changed = False

    if payload.apply and accepted:
        patch = best.get("patch") or {}
        rule = _store_metadata_patch_rule(
            user_id=user_id,
            src_track=track,
            patch=patch,
            provider=str(best.get("provider") or "unknown"),
            score=float(best.get("score") or 0.0),
        )
        patched = dict(track)
        patched.update(patch)
        patched.update(enrich_track_metadata(patched))
        if any(track.get(k) != patched.get(k) for k in patched.keys()):
            lib["tracks"][track_id] = patched
            lib["version"] = int(time.time())
            save_lib(user_id, lib)
            db_upsert_tracks(user_id, [patched])
            db_upsert_override(track_id, user_id, patch)
            changed = True

    db_insert_provider_snapshot(track_id, best)

    return {
        "ok": True,
        "matched": accepted,
        "applied": bool(payload.apply and accepted and changed),
        "track_id": track_id,
        "best": best,
        "candidates": candidates[:5],
        "provider_errors": provider_errors,
        "rule": rule,
    }


@router.post("/metadata/enrich-library/{user_id}")
def metadata_enrich_library(user_id: str, payload: MetadataEnrichLibraryPayload):
    lib = load_lib(user_id)
    tracks = list(lib.get("tracks", {}).values())
    limit = max(1, min(int(payload.limit or 25), 500))
    min_score = float(payload.min_score or 0.78)
    providers = payload.providers or ["musicbrainz", "discogs"]

    # prioritize low-quality metadata first
    tracks.sort(key=lambda t: int(t.get("metadata_quality") or 0))
    scanned = 0
    matched = 0
    applied = 0
    details = []

    for t in tracks[:limit]:
        scanned += 1
        searched = search_candidates(t, providers=providers, include_errors=True)
        candidates = searched.get("candidates", [])
        provider_errors = searched.get("errors", [])
        if not candidates:
            details.append({
                "track_id": t.get("track_id"),
                "title": t.get("title"),
                "artist": t.get("artist"),
                "best": None,
                "provider_errors": provider_errors,
            })
            continue
        best = candidates[0]
        if float(best.get("score") or 0.0) < min_score:
            details.append({
                "track_id": t.get("track_id"),
                "title": t.get("title"),
                "artist": t.get("artist"),
                "best": best,
                "provider_errors": provider_errors,
            })
            continue
        matched += 1
        item = {
            "track_id": t.get("track_id"),
            "title": t.get("title"),
            "artist": t.get("artist"),
            "best": best,
            "provider_errors": provider_errors,
        }
        if payload.apply:
            patch = best.get("patch") or {}
            rule = _store_metadata_patch_rule(
                user_id=user_id,
                src_track=t,
                patch=patch,
                provider=str(best.get("provider") or "unknown"),
                score=float(best.get("score") or 0.0),
            )
            patched = dict(t)
            patched.update(patch)
            patched.update(enrich_track_metadata(patched))
            tid = str(t.get("track_id"))
            original = lib["tracks"].get(tid)
            if original and any(original.get(k) != patched.get(k) for k in patched.keys()):
                lib["tracks"][tid] = patched
                db_upsert_tracks(user_id, [patched])
                db_upsert_override(tid, user_id, patch)
                applied += 1
            item["rule"] = rule
        db_insert_provider_snapshot(str(t.get("track_id") or ""), best)
        details.append(item)

    if payload.apply and applied > 0:
        lib["version"] = int(time.time())
        save_lib(user_id, lib)

    return {
        "ok": True,
        "scanned": scanned,
        "matched": matched,
        "applied": applied,
        "details": details[:50],
    }

# -------- agent announce/status --------
@router.post("/agent/announce")
def agent_announce(payload: AnnouncePayload):
    """
    Persist only when base_url changes; update last_seen in memory every call.
    """
    st = load_agent(payload.user_id)
    new_base = payload.base_url.rstrip("/")
    changed = (st.get("base_url") != new_base)

    st["base_url"] = new_base
    st["last_seen"] = int(time.time())

    if changed:
        save_agent_stable(payload.user_id, st)
    else:
        # refresh in-memory copy
        from ..storage import AGENTS
        AGENTS[payload.user_id] = st

    return {"ok": True, "base_url": st["base_url"], "persisted": changed}

@router.get("/agent/{user_id}/status")
def agent_status(user_id: str):
    st = load_agent(user_id)
    last_seen = int(st.get("last_seen", 0) or 0)
    base_url = st.get("base_url")
    online = bool(base_url) and (int(time.time()) - last_seen < 600)
    return {
        "online": online,
        "base_url": base_url,
        "last_seen": last_seen,
    }

# -------- library get --------
@router.get("/library/{user_id}")
def get_library(user_id: str):
    lib = load_lib(user_id)
    return {"version": lib["version"], "tracks": list(lib["tracks"].values())}


@router.get("/library/{user_id}/metadata-summary")
def get_library_metadata_summary(user_id: str):
    lib = load_lib(user_id)
    values = list(lib["tracks"].values())
    total = len(values)
    if total == 0:
        return {
            "total_tracks": 0,
            "avg_metadata_quality": 0,
            "missing": {},
            "format_family": {},
            "top_flags": [],
        }

    missing = {
        "title": 0,
        "artist": 0,
        "album": 0,
        "duration": 0,
        "codec": 0,
        "artwork": 0,
        "artist_bio": 0,
        "album_bio": 0,
    }
    format_family: Dict[str, int] = {}
    flags_counter: Dict[str, int] = {}
    quality_total = 0

    for t in values:
        flags = t.get("metadata_flags") or []
        if not isinstance(flags, list):
            flags = []
        for flag in flags:
            flags_counter[flag] = flags_counter.get(flag, 0) + 1
        if "missing_title" in flags:
            missing["title"] += 1
        if "missing_artist" in flags:
            missing["artist"] += 1
        if "missing_album" in flags:
            missing["album"] += 1
        if "invalid_duration" in flags:
            missing["duration"] += 1
        if "missing_codec" in flags:
            missing["codec"] += 1
        if not (t.get("artwork_url") or (t.get("artwork_urls") or [])):
            missing["artwork"] += 1
        if not str(t.get("artist_bio") or "").strip():
            missing["artist_bio"] += 1
        if not str(t.get("album_bio") or "").strip():
            missing["album_bio"] += 1

        family = str(t.get("format_family") or "unknown")
        format_family[family] = format_family.get(family, 0) + 1

        try:
            quality_total += int(t.get("metadata_quality") or 0)
        except Exception:
            pass

    top_flags = sorted(flags_counter.items(), key=lambda x: x[1], reverse=True)[:10]
    return {
        "total_tracks": total,
        "avg_metadata_quality": round(quality_total / total, 2),
        "missing": missing,
        "format_family": format_family,
        "top_flags": [{"flag": flag, "count": count} for flag, count in top_flags],
    }


@router.get("/playlists/{user_id}")
def list_playlists(user_id: str):
    payload = load_playlists(user_id)
    lib = load_lib(user_id)
    track_ids = set(lib.get("tracks", {}).keys())

    out = []
    for p in payload.get("playlists", []):
        tids = [tid for tid in (p.get("track_ids") or []) if tid in track_ids]
        out.append({
            "playlist_id": p.get("playlist_id"),
            "name": p.get("name"),
            "track_count": len(tids),
            "created_at": p.get("created_at"),
            "updated_at": p.get("updated_at"),
        })
    return {"playlists": out, "version": payload.get("version")}


@router.get("/playlists/{user_id}/{playlist_id}")
def get_playlist(user_id: str, playlist_id: str):
    payload = load_playlists(user_id)
    lib = load_lib(user_id)
    playlist = next((p for p in payload.get("playlists", []) if p.get("playlist_id") == playlist_id), None)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    tracks = []
    for tid in playlist.get("track_ids", []):
        t = lib["tracks"].get(tid)
        if t:
            tracks.append(t)
    return {
        "playlist_id": playlist.get("playlist_id"),
        "name": playlist.get("name"),
        "tracks": tracks,
        "created_at": playlist.get("created_at"),
        "updated_at": playlist.get("updated_at"),
    }


@router.post("/playlists/{user_id}")
def create_playlist(user_id: str, payload: PlaylistCreatePayload):
    name = str(payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Playlist name is required")
    store = load_playlists(user_id)
    now = int(time.time())
    playlist = {
        "playlist_id": str(uuid.uuid4()),
        "name": name,
        "track_ids": [],
        "created_at": now,
        "updated_at": now,
    }
    store.setdefault("playlists", []).append(playlist)
    store["version"] = now
    save_playlists(user_id, store)
    return {"ok": True, "playlist": playlist}


@router.post("/playlists/{user_id}/{playlist_id}/add")
def add_tracks_to_playlist(user_id: str, playlist_id: str, payload: PlaylistTrackUpdatePayload):
    store = load_playlists(user_id)
    lib = load_lib(user_id)
    track_ids = set(lib.get("tracks", {}).keys())
    playlist = next((p for p in store.get("playlists", []) if p.get("playlist_id") == playlist_id), None)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    existing = list(playlist.get("track_ids") or [])
    existing_set = set(existing)
    added = 0
    for tid in payload.track_ids:
        if tid in track_ids and tid not in existing_set:
            existing.append(tid)
            existing_set.add(tid)
            added += 1

    now = int(time.time())
    playlist["track_ids"] = existing
    playlist["updated_at"] = now
    store["version"] = now
    save_playlists(user_id, store)
    return {"ok": True, "added": added, "track_count": len(existing)}


@router.post("/playlists/{user_id}/{playlist_id}/remove")
def remove_tracks_from_playlist(user_id: str, playlist_id: str, payload: PlaylistTrackUpdatePayload):
    store = load_playlists(user_id)
    playlist = next((p for p in store.get("playlists", []) if p.get("playlist_id") == playlist_id), None)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    to_remove = set(payload.track_ids)
    before = list(playlist.get("track_ids") or [])
    after = [tid for tid in before if tid not in to_remove]
    removed = len(before) - len(after)

    now = int(time.time())
    playlist["track_ids"] = after
    playlist["updated_at"] = now
    store["version"] = now
    save_playlists(user_id, store)
    return {"ok": True, "removed": removed, "track_count": len(after)}


@router.post("/playlists/{user_id}/{playlist_id}/clear")
def clear_playlist(user_id: str, playlist_id: str):
    store = load_playlists(user_id)
    playlist = next((p for p in store.get("playlists", []) if p.get("playlist_id") == playlist_id), None)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    removed = len(list(playlist.get("track_ids") or []))
    now = int(time.time())
    playlist["track_ids"] = []
    playlist["updated_at"] = now
    store["version"] = now
    save_playlists(user_id, store)
    return {"ok": True, "removed": removed, "track_count": 0}


@router.delete("/playlists/{user_id}/{playlist_id}")
def delete_playlist(user_id: str, playlist_id: str):
    store = load_playlists(user_id)
    before = len(store.get("playlists", []))
    store["playlists"] = [p for p in store.get("playlists", []) if p.get("playlist_id") != playlist_id]
    if len(store["playlists"]) == before:
        raise HTTPException(status_code=404, detail="Playlist not found")
    store["version"] = int(time.time())
    save_playlists(user_id, store)
    return {"ok": True}

# -------- submit scan --------
@router.post("/submit-scan")
def submit_scan(payload: ScanPayload):
    """
    Idempotent replace:
      - If replace=True AND we haven't cleared for this library_version, clear once and remember it.
      - Normalize rel_path for every incoming track.
    """
    lib = load_lib(payload.user_id)
    tracks = lib["tracks"]

    session_ver = int(payload.library_version or int(time.time()))
    if payload.replace and lib.get("_cleared_for") != session_ver:
        tracks.clear()
        lib["_cleared_for"] = session_ver

    db_rows = []
    auto_enrich_candidates: list[str] = []
    try:
        auto_cooldown_sec = int(os.getenv("RT_AUTO_ENRICH_COOLDOWN_SEC", "43200"))
    except Exception:
        auto_cooldown_sec = 43200
    auto_cooldown_sec = max(300, min(auto_cooldown_sec, 86400 * 7))
    now_ts = int(time.time())
    for t in payload.library:
        d = t.model_dump()
        if d.get("rel_path"):
            d["rel_path"] = normalize_rel_path(d["rel_path"])
        d = _apply_metadata_library_patch(d, payload.user_id)
        d.update(enrich_track_metadata(d))
        seed = db_find_metadata_seed(payload.user_id, d)
        if seed:
            d = _apply_seed_metadata(d, seed)
            d.update(enrich_track_metadata(d))
        existing = tracks.get(d["track_id"]) or {}
        # Preserve internal enrichment markers across rescans.
        if existing.get("_auto_enrich_ts"):
            d["_auto_enrich_ts"] = existing.get("_auto_enrich_ts")
        tracks[d["track_id"]] = d
        db_rows.append(d)
        # Incremental enrichment target: new/changed tracks still missing rich media metadata.
        changed_key_fields = any(
            str(existing.get(k) or "").strip() != str(d.get(k) or "").strip()
            for k in ("title", "artist", "album", "rel_path")
        )
        lacks_rich_meta = not (
            d.get("artwork_url")
            or (d.get("artwork_urls") or [])
            or (d.get("artist_image_urls") or [])
            or str(d.get("artist_bio") or "").strip()
            or str(d.get("album_bio") or "").strip()
        )
        try:
            last_attempt_ts = int(existing.get("_auto_enrich_ts") or 0)
        except Exception:
            last_attempt_ts = 0
        cooldown_ok = (now_ts - last_attempt_ts) >= auto_cooldown_sec
        if lacks_rich_meta and (not last_attempt_ts or cooldown_ok or changed_key_fields):
            auto_enrich_candidates.append(str(d["track_id"]))

    lib["version"] = session_ver
    save_lib(payload.user_id, lib)
    db_upsert_tracks(payload.user_id, db_rows)

    # Optional incremental enrichment on scan so player gets images/bios without manual backfill.
    auto_enrich_enabled = str(os.getenv("RT_AUTO_ENRICH_ON_SCAN", "1")).strip().lower() not in {"0", "false", "off", "no"}
    auto_scanned = 0
    auto_matched = 0
    auto_applied = 0
    if auto_enrich_enabled and auto_enrich_candidates:
        try:
            auto_limit = int(os.getenv("RT_AUTO_ENRICH_SCAN_LIMIT", "12"))
        except Exception:
            auto_limit = 12
        auto_limit = max(0, min(auto_limit, 100))
        try:
            auto_min_score = float(os.getenv("RT_AUTO_ENRICH_MIN_SCORE", "0.72"))
        except Exception:
            auto_min_score = 0.72
        auto_min_score = max(0.0, min(auto_min_score, 1.0))
        provider_raw = str(os.getenv("RT_AUTO_ENRICH_PROVIDERS", "musicbrainz,discogs,acoustid"))
        auto_providers = [p.strip().lower() for p in provider_raw.split(",") if p.strip()]
        if not auto_providers:
            auto_providers = ["musicbrainz", "discogs", "acoustid"]

        for tid in auto_enrich_candidates[:auto_limit]:
            tcur = tracks.get(tid)
            if not tcur:
                continue
            auto_scanned += 1
            tcur["_auto_enrich_ts"] = now_ts
            searched = search_candidates(tcur, providers=auto_providers, include_errors=True)
            candidates = searched.get("candidates", [])
            if not candidates:
                continue
            best = candidates[0]
            db_insert_provider_snapshot(tid, best)
            score = float(best.get("score") or 0.0)
            if score < auto_min_score:
                continue
            auto_matched += 1
            patch = best.get("patch") or {}
            patched = dict(tcur)
            patched.update(patch)
            patched.update(enrich_track_metadata(patched))
            if any(tcur.get(k) != patched.get(k) for k in patched.keys()):
                tracks[tid] = patched
                db_upsert_tracks(payload.user_id, [patched])
                db_upsert_override(tid, payload.user_id, patch)
                auto_applied += 1
        if auto_applied > 0:
            save_lib(payload.user_id, lib)

    preview = []
    for i, (_, v) in enumerate(tracks.items()):
        if i >= 3:
            break
        preview.append({k: v.get(k) for k in ("title", "artist", "album", "track_id", "rel_path", "metadata_quality", "metadata_flags")})

    st = load_agent(payload.user_id)
    return {
        "ok": True,
        "user_id": payload.user_id,
        "count": len(tracks),
        "version": lib["version"],
        "agent_base_url": st.get("base_url"),
        "auto_enrich": {
            "enabled": auto_enrich_enabled,
            "scanned": auto_scanned,
            "matched": auto_matched,
            "applied": auto_applied,
        },
        "preview": preview
    }

# -------- relay (GET/HEAD) --------
@router.api_route("/relay/{user_id}/{track_id}", methods=["GET", "HEAD"])
def relay(user_id: str, track_id: str, request: Request):
    lib = load_lib(user_id)
    track = lib["tracks"].get(track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Unknown track_id")

    url = build_stream_url(user_id, track)
    if not url:
        raise HTTPException(status_code=503, detail="Agent offline or base_url/rel_path unknown")

    client_range = request.headers.get("range")
    base_headers = {"User-Agent": "RadioTiker-Relay/0.3"}

    # Probe upstream range capability
    upstream_accepts_ranges = False
    try:
        probe_h = dict(base_headers)
        probe_h["Range"] = "bytes=0-0"
        probe = requests.get(url, stream=True, timeout=(5, 15), headers=probe_h, allow_redirects=True)
        if probe.status_code == 206 or probe.headers.get("Content-Range") or probe.headers.get("Accept-Ranges","").lower() == "bytes":
            upstream_accepts_ranges = True
        try:
            probe.close()
        except Exception:
            pass
    except requests.RequestException as e:
        print(f"[relay] probe failed user={user_id} track={track_id} url={url} err={e}")

    headers = dict(base_headers)
    if request.method == "GET":
        if client_range:
            headers["Range"] = client_range
        elif upstream_accepts_ranges:
            headers["Range"] = "bytes=0-"
    else:  # HEAD
        if client_range:
            headers["Range"] = client_range
        elif upstream_accepts_ranges:
            headers["Range"] = "bytes=0-0"
    try:
        upstream = (
            requests.head(url, timeout=(5, 15), headers=headers, allow_redirects=True)
            if request.method == "HEAD"
            else requests.get(url, stream=True, timeout=(5, 3600), headers=headers, allow_redirects=True)
        )
    except requests.RequestException as e:
        print(f"[relay] upstream error user={user_id} track={track_id} url={url} err={e}")
        raise HTTPException(status_code=502, detail=f"Upstream fetch failed: {e}")
    
    # If client sent Range but upstream rejects it (416), retry once without Range.
    if request.method == "GET" and upstream.status_code == 416 and client_range:
        try:
            no_range_headers = dict(base_headers)
            upstream = requests.get(
                url,
                stream=True,
                timeout=(5, 3600),
                headers=no_range_headers,
                allow_redirects=True,
            )
        except requests.RequestException as e:
            raise HTTPException(status_code=502, detail=f"Upstream retry (no-range) failed: {e}")


    status = upstream.status_code
    if status >= 400:
        body_preview = None
        try:
            body_preview = upstream.text[:400]
        except Exception:
            pass
        try:
            upstream.close()
        except Exception:
            pass
        print(f"[relay] upstream HTTP {status} user={user_id} track={track_id} url={url} body={body_preview!r}")
        raise HTTPException(status_code=status, detail=f"Upstream returned {status}")

    passthrough: Dict[str, str] = {}
    for k in ["Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Cache-Control", "ETag", "Last-Modified"]:
        v = upstream.headers.get(k)
        if v:
            passthrough[k] = v

    passthrough["Access-Control-Allow-Origin"] = "*"
    passthrough.setdefault("Cache-Control", "no-store")
    passthrough["Connection"] = "close"

    media = upstream.headers.get("Content-Type") or "application/octet-stream"
    media_lc = media.lower()

    # If it's FLAC, don't risk it: many browsers choke on FLAC-with-picture streams.
    if media_lc.startswith("audio/flac") or media_lc.startswith("audio/x-flac"):
        try:
            upstream.close()
        except Exception:
            pass
        target_url = str(request.url_for("relay_mp3", user_id=user_id, track_id=track_id))
        if request.url.query:
            target_url = f"{target_url}?{request.url.query}"
        return RedirectResponse(url=target_url, status_code=302)

    # Otherwise pass through (mp3/aac/etc)
    IOS_OK = (
        media_lc.startswith("audio/mpeg") or
        media_lc.startswith("audio/aac")  or
        media_lc.startswith("audio/mp4")  or
        media_lc.startswith("audio/x-m4a")
    )

    if not IOS_OK:
        try:
            upstream.close()
        except Exception:
            pass
        target_url = str(request.url_for("relay_mp3", user_id=user_id, track_id=track_id))
        if request.url.query:
            target_url = f"{target_url}?{request.url.query}"
        return RedirectResponse(url=target_url, status_code=302)

        # ---- end auto-switch ----

    if request.method == "HEAD":
        try:
            upstream.close()
        except Exception:
            pass
        return Response(status_code=status, headers=passthrough, media_type=media)

    def gen():
        try:
            for chunk in upstream.iter_content(chunk_size=256 * 1024):
                if chunk:
                    yield chunk
        finally:
            try:
                upstream.close()
            except Exception:
                pass

    return StreamingResponse(gen(), media_type=media, headers=passthrough, status_code=status)

@router.api_route("/relay-mp3/{user_id}/{track_id}", methods=["GET", "HEAD"], name="relay_mp3")
def relay_mp3(user_id: str, track_id: str, request: Request):
    lib = load_lib(user_id)
    track = lib["tracks"].get(track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Unknown track_id")

    url = build_stream_url(user_id, track)
    if not url:
        raise HTTPException(status_code=503, detail="Agent offline or base_url/rel_path unknown")

    # Best-effort duration (helps iOS display track length)
    duration_sec: Optional[float] = None
    try:
        raw = track.get("duration_sec")
        if raw:
            duration_sec = float(raw)
    except Exception:
        duration_sec = None
    if duration_sec is None:
        duration_sec = _probe_duration_sec(url)

    # Optional start offset (seconds) enables server-side seek for live transcode.
    start_sec = 0.0
    try:
        raw_start = request.query_params.get("start")
        if raw_start:
            start_sec = max(0.0, float(raw_start))
    except Exception:
        start_sec = 0.0
    if duration_sec:
        start_sec = min(start_sec, max(0.0, float(duration_sec) - 1.0))

    abr_kbps = 192
    est_len = None
    if duration_sec:
        # Approximate size for CBR MP3: seconds * (kbps * 1000 / 8)
        est_len = int(duration_sec * (abr_kbps * 1000 / 8))

    if request.method == "HEAD":
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-store, must-revalidate, no-transform",
            "Content-Type": "audio/mpeg",
            "Accept-Ranges": "none",
        }
        if duration_sec:
            headers["X-Content-Duration"] = str(duration_sec)
            headers["Content-Duration"] = str(duration_sec)
        if est_len:
            headers["Content-Length"] = str(est_len)
        return Response(status_code=200, headers=headers)


    cmd = _ffmpeg_cmd_for_http_input(url, abr_kbps=abr_kbps, start_sec=start_sec)
    try:
        p = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ffmpeg spawn failed: {e}")

    def gen():
        try:
            assert p.stdout is not None
            while True:
                chunk = p.stdout.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            try: p.kill()
            except Exception: pass

    headers = {
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "no-store, must-revalidate",
        "Connection": "close",
        "Accept-Ranges": "none",
        "X-Accel-Buffering": "no",
    }
    if start_sec > 0:
        headers["X-Start-Offset"] = f"{start_sec:.3f}"
    if duration_sec:
        headers["X-Content-Duration"] = str(duration_sec)
        headers["Content-Duration"] = str(duration_sec)
    # Do not set guessed Content-Length on live transcode GET responses.
    # Estimated lengths can cause premature end behavior on some clients.

    return StreamingResponse(gen(), media_type="audio/mpeg", headers=headers)
