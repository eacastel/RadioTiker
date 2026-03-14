from typing import Dict, Any, Optional
import json, time, requests
import os, subprocess, re
import hashlib
import uuid
import random
from difflib import SequenceMatcher
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
    MetadataResetPayload,
    MetadataAlbumResetPayload,
    MobileNextPayload,
    TrackHealthScanPayload,
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
    db_upsert_track_sources,
    db_mark_all_track_sources_unavailable,
    db_upsert_track_health,
    db_list_track_health,
    db_insert_provider_snapshot,
    db_upsert_override,
    db_find_metadata_seed,
    db_delete_overrides,
    db_delete_provider_snapshots,
    db_delete_tracks,
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

LEGACY_AUDIO_EXTS = {
    ".flac", ".wav", ".aif", ".aiff", ".ape", ".alac", ".wv",
    ".ogg", ".opus", ".wma", ".dsf", ".dff",
}

PLAYABILITY_BAD_THRESHOLD = max(1, int(os.getenv("RT_PLAYABILITY_BAD_THRESHOLD", "3") or "3"))
TRACK_HEALTH_FFPROBE_TIMEOUT_SEC = max(5, int(os.getenv("RT_TRACK_HEALTH_FFPROBE_TIMEOUT_SEC", "20") or "20"))
TRACK_HEALTH_DECODE_SEC = max(0, int(os.getenv("RT_TRACK_HEALTH_DECODE_SEC", "8") or "8"))
TRACK_HEALTH_DECODE_TIMEOUT_SEC = max(5, int(os.getenv("RT_TRACK_HEALTH_DECODE_TIMEOUT_SEC", "30") or "30"))


def _track_needs_mp3_proxy(track: Dict[str, Any]) -> bool:
    rel = str(track.get("rel_path") or track.get("path") or "")
    if not rel:
        return False
    leaf = rel.rsplit("/", 1)[-1].split("?", 1)[0]
    dot = leaf.rfind(".")
    if dot < 0:
        return False
    return leaf[dot:].lower() in LEGACY_AUDIO_EXTS


def _mark_track_playability(user_id: str, track_id: str, ok: bool, reason: Optional[str] = None):
    """
    Persist best-effort playback health per track to avoid repeatedly selecting broken tracks.
    """
    try:
        lib = load_lib(user_id)
        track = (lib.get("tracks") or {}).get(track_id)
        if not track:
            return
        now_ts = int(time.time())
        if ok:
            track["playability_status"] = "ok"
            track["playability_fail_count"] = 0
            track["playability_last_ok_at"] = now_ts
            track.pop("playability_last_error", None)
        else:
            try:
                fail_count = int(track.get("playability_fail_count") or 0) + 1
            except Exception:
                fail_count = 1
            track["playability_fail_count"] = fail_count
            track["playability_last_fail_at"] = now_ts
            track["playability_last_error"] = str(reason or "playback-error")[:240]
            track["playability_status"] = "bad" if fail_count >= PLAYABILITY_BAD_THRESHOLD else "flaky"
        save_lib(user_id, lib)
        db_upsert_tracks(user_id, [track])
    except Exception as e:
        print(f"[playability] update failed user={user_id} track={track_id} err={e}")


def _is_weak_text(v: Any) -> bool:
    s = str(v or "").strip()
    if not s:
        return True
    k = normalize_text_key(s)
    return k in {"unknown", "unknown artist", "unknown album", "n a", "na"}


def _is_generic_title(v: Any) -> bool:
    s = str(v or "").strip().lower()
    return bool(re.fullmatch(r"(track|song)\s*\d{1,3}", s))


def _sim_text(a: Any, b: Any) -> float:
    x = normalize_text_key(a)
    y = normalize_text_key(b)
    if not x and not y:
        return 1.0
    if not x or not y:
        return 0.0
    return SequenceMatcher(None, x, y).ratio()


def _provider_min_score(provider: str, default_min: float) -> float:
    p = str(provider or "").strip().lower()
    if p == "musicbrainz":
        try:
            return max(default_min, float(os.getenv("RT_AUTO_ENRICH_MIN_SCORE_MUSICBRAINZ", "0.0")))
        except Exception:
            return default_min
    if p == "discogs":
        try:
            return max(default_min, float(os.getenv("RT_AUTO_ENRICH_MIN_SCORE_DISCOGS", "0.0")))
        except Exception:
            return default_min
    if p == "acoustid":
        try:
            return max(default_min, float(os.getenv("RT_AUTO_ENRICH_MIN_SCORE_ACOUSTID", "0.90")))
        except Exception:
            return max(default_min, 0.90)
    return default_min


def _source_provider(source: Any) -> str:
    s = str(source or "").strip().lower()
    if ":" in s:
        return s.split(":", 1)[1]
    return s


def _has_rich_media(track: Dict[str, Any]) -> bool:
    return bool(
        track.get("artwork_url")
        or (track.get("artwork_urls") or [])
        or (track.get("artist_image_urls") or [])
        or str(track.get("artist_bio") or "").strip()
        or str(track.get("album_bio") or "").strip()
    )


def _overwrite_allowed(current: Dict[str, Any], best: Dict[str, Any], mode: str) -> bool:
    """
    Score-aware overwrite policy:
    - manual mode always allowed
    - auto/batch do not overwrite manual edits
    - auto/batch overwrite only when clearly better or current data is weak
    """
    if mode == "manual":
        return True

    current_source = str(current.get("metadata_source") or "").strip().lower()
    if current_source.startswith("manual:"):
        return False
    if not current_source or current_source.startswith("seed:"):
        return True

    if not _has_rich_media(current):
        return True

    try:
        margin = float(os.getenv("RT_AUTO_ENRICH_OVERWRITE_MARGIN", "0.04"))
    except Exception:
        margin = 0.04
    margin = max(0.0, min(margin, 0.25))

    try:
        current_score = float(current.get("metadata_source_score"))
    except Exception:
        current_score = 0.0
    new_score = float(best.get("score") or 0.0)

    cur_provider = _source_provider(current_source)
    new_provider = str(best.get("provider") or "").strip().lower()
    if new_provider and new_provider == cur_provider:
        return new_score >= (current_score + margin)
    return new_score >= (current_score + margin + 0.03)


def _candidate_passes_sanity(track: Dict[str, Any], best: Dict[str, Any]) -> bool:
    """
    Block obviously wrong substitutions even when provider score is high.
    """
    patch = best.get("patch") or {}
    cur_title = track.get("title")
    cur_artist = track.get("artist")
    cur_album = track.get("album")
    cand_title = patch.get("title")
    cand_artist = patch.get("artist")
    cand_album = patch.get("album")

    if not _is_weak_text(cur_artist) and cand_artist and _sim_text(cur_artist, cand_artist) < 0.58:
        return False
    if not _is_weak_text(cur_album) and cand_album and _sim_text(cur_album, cand_album) < 0.45:
        return False
    if (not _is_weak_text(cur_title)) and (not _is_generic_title(cur_title)) and cand_title and _sim_text(cur_title, cand_title) < 0.52:
        return False

    # AcoustID can produce odd collisions on weak tags; require basic non-empty patch text.
    provider = str(best.get("provider") or "").lower()
    if provider == "acoustid":
        if not str(cand_title or "").strip():
            return False
        if not str(cand_artist or "").strip():
            return False
    return True


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


def _ffprobe_media(url: str) -> Dict[str, Any]:
    try:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration:stream=codec_name,codec_type",
            "-of", "json",
            url,
        ]
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=TRACK_HEALTH_FFPROBE_TIMEOUT_SEC,
            check=False,
            text=True,
        )
        stderr = (proc.stderr or "").strip()
        if proc.returncode != 0:
            return {"ok": False, "returncode": proc.returncode, "stderr": stderr}
        data = {}
        try:
            data = json.loads(proc.stdout or "{}")
        except Exception:
            data = {}
        codec = None
        for stream in data.get("streams") or []:
            if str(stream.get("codec_type") or "") == "audio":
                codec = stream.get("codec_name")
                break
        duration = None
        try:
            duration = float(((data.get("format") or {}).get("duration")) or 0.0)
        except Exception:
            duration = None
        return {"ok": True, "duration_sec": duration, "codec": codec, "raw": data}
    except Exception as e:
        return {"ok": False, "stderr": str(e)}


def _ffmpeg_decode_probe(url: str, decode_sec: int) -> Dict[str, Any]:
    if decode_sec <= 0:
        return {"ok": True, "skipped": True}
    try:
        cmd = [
            "ffmpeg",
            "-nostdin",
            "-v", "error",
            "-xerror",
            "-t", str(int(decode_sec)),
            "-i", url,
            "-map", "0:a:0",
            "-f", "null",
            "-",
        ]
        proc = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=TRACK_HEALTH_DECODE_TIMEOUT_SEC,
            check=False,
            text=True,
        )
        stderr = (proc.stderr or "").strip()
        return {"ok": proc.returncode == 0, "returncode": proc.returncode, "stderr": stderr}
    except Exception as e:
        return {"ok": False, "stderr": str(e)}


def _track_health_entry(user_id: str, track: Dict[str, Any]) -> Dict[str, Any]:
    source_path = track.get("source_path") or track.get("rel_path") or track.get("path")
    url = build_stream_url(user_id, track)
    details: Dict[str, Any] = {"url": url, "source_path": source_path}
    entry: Dict[str, Any] = {
        "track_uid": str(track.get("track_id") or ""),
        "source_path": source_path,
        "status": "warning",
        "source_reachable": False,
        "probe_ok": False,
        "decode_ok": False,
        "duration_sec": None,
        "codec": track.get("codec"),
        "error_reason": "unknown",
        "details": details,
    }
    if not url:
        entry["error_reason"] = "no-base-url-or-rel-path"
        return entry
    try:
        head = requests.head(url, timeout=(5, 15), allow_redirects=True, headers={"User-Agent": "RadioTiker-Health/0.1"})
        details["head_status"] = int(head.status_code)
        if head.status_code >= 400:
            entry["error_reason"] = f"source-unreachable:http-{head.status_code}"
            return entry
        entry["source_reachable"] = True
    except requests.RequestException as e:
        entry["error_reason"] = f"source-unreachable:{e}"
        return entry

    probe = _ffprobe_media(url)
    details["probe"] = {k: v for k, v in probe.items() if k != "raw"}
    if not probe.get("ok"):
        entry["status"] = "error"
        entry["error_reason"] = f"ffprobe-failed:{probe.get('stderr') or 'unknown'}"
        return entry

    entry["probe_ok"] = True
    if probe.get("duration_sec") is not None:
        entry["duration_sec"] = probe.get("duration_sec")
    if probe.get("codec"):
        entry["codec"] = probe.get("codec")

    if not entry.get("duration_sec") or float(entry.get("duration_sec") or 0.0) <= 0.0:
        entry["status"] = "error"
        entry["error_reason"] = "invalid-duration"
        return entry

    decode = _ffmpeg_decode_probe(url, TRACK_HEALTH_DECODE_SEC)
    details["decode"] = decode
    if not decode.get("ok"):
        entry["status"] = "error"
        entry["error_reason"] = f"decode-failed:{decode.get('stderr') or 'unknown'}"
        return entry

    entry["decode_ok"] = True
    entry["status"] = "ok"
    entry["error_reason"] = ""
    return entry

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


def _ffmpeg_cmd_to_file(url: str, out_path: str, abr_kbps: int = 192) -> list[str]:
    cmd = _ffmpeg_cmd_for_http_input(url, abr_kbps=abr_kbps, start_sec=0.0)
    # Replace stdout sink with explicit output file.
    if cmd and cmd[-1] == "-":
        cmd = cmd[:-1]
    cmd += ["-y", out_path]
    return cmd


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "1" if default else "0")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _cache_dir() -> str:
    return os.getenv("RT_MP3_CACHE_DIR", "/tmp/radiotiker_mp3_cache")


def _cache_key(user_id: str, track_id: str, src_url: str, abr_kbps: int) -> str:
    base = f"{user_id}|{track_id}|{src_url}|{abr_kbps}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def _cache_paths(user_id: str, track_id: str, src_url: str, abr_kbps: int) -> tuple[str, str]:
    key = _cache_key(user_id=user_id, track_id=track_id, src_url=src_url, abr_kbps=abr_kbps)
    root = _cache_dir()
    os.makedirs(root, exist_ok=True)
    mp3_path = os.path.join(root, f"{key}.mp3")
    lock_path = mp3_path + ".lock"
    return mp3_path, lock_path


def _try_build_cached_mp3(user_id: str, track_id: str, src_url: str, abr_kbps: int = 192) -> Optional[str]:
    """
    Best-effort cache build. Returns mp3 path if available, else None.
    Uses a lock file so concurrent requests do not launch duplicate ffmpeg jobs.
    """
    mp3_path, lock_path = _cache_paths(user_id=user_id, track_id=track_id, src_url=src_url, abr_kbps=abr_kbps)
    expected_duration_sec = _probe_duration_sec(src_url)

    def cache_usable(path: str) -> bool:
        try:
            if not os.path.exists(path):
                return False
            if os.path.getsize(path) < 128 * 1024:
                return False
            if not expected_duration_sec:
                return True
            got = _probe_duration_sec(path)
            if not got:
                return False
            # Reject truncated files that are far shorter than source duration.
            return got >= (expected_duration_sec * 0.90)
        except Exception:
            return False

    if cache_usable(mp3_path):
        return mp3_path
    if os.path.exists(mp3_path):
        try:
            os.unlink(mp3_path)
        except Exception:
            pass

    got_lock = False
    lock_fd = None
    lock_deadline = time.time() + 90.0
    while time.time() < lock_deadline:
        try:
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.write(lock_fd, str(os.getpid()).encode("ascii"))
            got_lock = True
            break
        except FileExistsError:
            if cache_usable(mp3_path):
                return mp3_path
            time.sleep(0.25)
        except Exception:
            break

    if not got_lock:
        if cache_usable(mp3_path):
            return mp3_path
        return None

    tmp_path = mp3_path + ".tmp"
    try:
        cmd = _ffmpeg_cmd_to_file(src_url, tmp_path, abr_kbps=abr_kbps)
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=1800,
            check=False,
        )
        if cache_usable(tmp_path):
            os.replace(tmp_path, mp3_path)
            return mp3_path
    except Exception:
        pass
    finally:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except Exception:
            pass
        try:
            if lock_fd is not None:
                os.close(lock_fd)
        except Exception:
            pass
        try:
            if os.path.exists(lock_path):
                os.unlink(lock_path)
        except Exception:
            pass
    return None


def _parse_single_range(range_header: Optional[str], total_size: int) -> Optional[tuple[int, int]]:
    """
    Parse HTTP Range header for single-byte ranges.
    Returns (start, end) inclusive or None if no usable header.
    Raises ValueError for invalid/unsatisfiable ranges.
    """
    if not range_header:
        return None
    s = str(range_header).strip().lower()
    if not s.startswith("bytes="):
        raise ValueError("invalid range unit")
    part = s[6:].strip()
    if "," in part:
        raise ValueError("multiple ranges not supported")
    if "-" not in part:
        raise ValueError("invalid range format")
    a, b = part.split("-", 1)
    if a == "":
        # Suffix range: bytes=-N
        n = int(b)
        if n <= 0:
            raise ValueError("invalid suffix length")
        start = max(0, total_size - n)
        end = total_size - 1
        return (start, end)
    start = int(a)
    end = total_size - 1 if b == "" else int(b)
    if start < 0 or end < start or start >= total_size:
        raise ValueError("unsatisfiable range")
    end = min(end, total_size - 1)
    return (start, end)


def _serve_mp3_file(path: str, request: Request, duration_sec: Optional[float], abr_kbps: int, start_sec: float) -> Response:
    total = os.path.getsize(path)
    media_type = "audio/mpeg"
    if total <= 0:
        raise HTTPException(status_code=500, detail="Empty cached mp3")

    range_header = request.headers.get("range")
    try:
        rng = _parse_single_range(range_header, total)
    except ValueError:
        return Response(
            status_code=416,
            headers={
                "Content-Range": f"bytes */{total}",
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "no-store, must-revalidate",
                "Accept-Ranges": "bytes",
            },
        )

    if rng is None and start_sec > 0:
        # For CBR output this is a reliable seek approximation.
        approx = int(start_sec * (abr_kbps * 1000 / 8))
        start = min(max(0, approx), max(0, total - 1))
        rng = (start, total - 1)

    status_code = 200
    start = 0
    end = total - 1
    if rng is not None:
        start, end = rng
        status_code = 206

    length = end - start + 1
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "no-store, must-revalidate",
        "Accept-Ranges": "bytes",
        "X-Accel-Buffering": "no",
        "X-Relay-Mode": "cached-file",
        "Content-Type": media_type,
        "Content-Length": str(length),
    }
    if duration_sec:
        headers["X-Content-Duration"] = str(duration_sec)
        headers["Content-Duration"] = str(duration_sec)
    if start_sec > 0:
        headers["X-Start-Offset"] = f"{start_sec:.3f}"
    if status_code == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{total}"

    if request.method == "HEAD":
        return Response(status_code=status_code, headers=headers, media_type=media_type)

    def gen():
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(gen(), status_code=status_code, media_type=media_type, headers=headers)



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
    track_ids = list((lib.get("tracks") or {}).keys())
    lib["tracks"] = {}
    lib["version"] = int(time.time())
    lib["_cleared_for"] = 0
    save_lib(user_id, lib)
    if track_ids:
        db_delete_tracks(user_id, track_ids, purge_related=True)
    return {"ok": True, "cleared": True, "version": lib["version"]}


@router.get("/library/{user_id}/health")
def get_library_health(user_id: str, status: Optional[str] = None, limit: int = 100):
    rows = db_list_track_health(user_id, status=status, limit=limit)
    lib = load_lib(user_id)
    out = []
    for row in rows:
        tid = str(row.get("track_uid") or "")
        track = (lib.get("tracks") or {}).get(tid) or {}
        item = dict(row)
        item["track_id"] = tid
        item["title"] = track.get("title")
        item["artist"] = track.get("artist")
        item["album"] = track.get("album")
        raw = item.get("details_json")
        if isinstance(raw, (bytes, bytearray)):
            try:
                raw = raw.decode("utf-8")
            except Exception:
                raw = None
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                raw = None
        item["details"] = raw or {}
        item.pop("details_json", None)
        out.append(item)
    return {"ok": True, "user_id": user_id, "count": len(out), "items": out}


@router.post("/library/{user_id}/health-check")
def scan_library_health(user_id: str, payload: TrackHealthScanPayload):
    lib = load_lib(user_id)
    tracks = list((lib.get("tracks") or {}).values())
    by_id = {str(t.get("track_id") or ""): t for t in tracks if t.get("track_id")}
    requested = [str(tid) for tid in (payload.track_ids or []) if str(tid or "").strip()]
    if requested:
        candidates = [by_id[tid] for tid in requested if tid in by_id]
    else:
        candidates = sorted(
            tracks,
            key=lambda t: (
                str(t.get("playability_status") or "") != "bad",
                int(t.get("playability_fail_count") or 0),
                str(t.get("title") or ""),
            ),
            reverse=False,
        )
    limit = max(1, min(int(payload.limit or 25), 250))
    checked = []
    persisted = []
    for track in candidates[:limit]:
        entry = _track_health_entry(user_id, track)
        persisted.append(entry)
        if payload.include_ok or entry.get("status") != "ok":
            checked.append(
                {
                    "track_id": str(track.get("track_id") or ""),
                    "title": track.get("title"),
                    "artist": track.get("artist"),
                    "album": track.get("album"),
                    **entry,
                }
            )
    db_upsert_track_health(user_id, persisted)
    return {
        "ok": True,
        "user_id": user_id,
        "scanned": len(persisted),
        "returned": len(checked),
        "items": checked,
    }


@router.post("/library/{user_id}/reset-enrichment")
def reset_library_enrichment(user_id: str, payload: MetadataResetPayload):
    """
    Reset enriched metadata either for selected tracks or entire library.
    Keeps scanned files and rel paths intact.
    """
    lib = load_lib(user_id)
    tracks = lib.get("tracks", {})
    target_ids = [str(tid) for tid in (payload.track_ids or []) if str(tid) in tracks]
    if not target_ids:
        target_ids = list(tracks.keys())
    if not target_ids:
        return {"ok": True, "changed": 0, "track_ids": [], "version": lib.get("version")}

    changed = 0
    db_rows = []
    for tid in target_ids:
        cur = tracks.get(tid)
        if not cur:
            continue
        nxt = dict(cur)
        # Restore scan-origin core tags when available.
        for fld in ("title", "artist", "album", "genre", "year"):
            scan_key = f"_scan_{fld}"
            if scan_key in nxt:
                nxt[fld] = nxt.get(scan_key)
        # Remove enriched media and source metadata.
        nxt["artwork_url"] = ""
        nxt["artwork_urls"] = []
        nxt["artist_image_urls"] = []
        nxt["artist_bio"] = ""
        nxt["album_bio"] = ""
        nxt["metadata_source"] = ""
        nxt["metadata_source_score"] = None
        nxt.pop("metadata_source_updated_at", None)
        nxt.pop("_auto_enrich_ts", None)
        nxt.update(enrich_track_metadata(nxt))

        if any(cur.get(k) != nxt.get(k) for k in nxt.keys()):
            tracks[tid] = nxt
            changed += 1
            db_rows.append(nxt)

    if db_rows:
        lib["version"] = int(time.time())
        save_lib(user_id, lib)
        db_upsert_tracks(user_id, db_rows)

    if payload.clear_overrides:
        db_delete_overrides(user_id, target_ids)
    if payload.clear_provider_snapshots:
        db_delete_provider_snapshots(target_ids)

    return {
        "ok": True,
        "changed": changed,
        "track_ids": target_ids,
        "version": lib["version"],
    }


@router.post("/library/{user_id}/reset-enrichment-album")
def reset_library_enrichment_album(user_id: str, payload: MetadataAlbumResetPayload):
    """
    Reset enriched metadata for all tracks matching a given album,
    optionally constrained by artist. This stays valid even when the UI
    does not group tracks by album.
    """
    lib = load_lib(user_id)
    tracks = lib.get("tracks", {})
    album_key = normalize_text_key(payload.album)
    artist_key = normalize_text_key(payload.artist) if str(payload.artist or "").strip() else ""
    if not album_key:
        raise HTTPException(status_code=400, detail="Album is required")

    target_ids = []
    for tid, t in tracks.items():
        t_album = normalize_text_key(t.get("album"))
        t_artist = normalize_text_key(t.get("artist"))
        if t_album != album_key:
            continue
        if artist_key and t_artist != artist_key:
            continue
        target_ids.append(str(tid))

    if not target_ids:
        return {"ok": True, "changed": 0, "track_ids": [], "version": lib.get("version")}

    return reset_library_enrichment(
        user_id,
        MetadataResetPayload(
            track_ids=target_ids,
            clear_overrides=bool(payload.clear_overrides),
            clear_provider_snapshots=bool(payload.clear_provider_snapshots),
        ),
    )


@router.post("/library/{user_id}/track/{track_id}/auto-enrich")
def set_track_auto_enrich(user_id: str, track_id: str, payload: Dict[str, Any]):
    """
    Per-track lock to avoid automatic enrichment (quota protection / bad matches).
    """
    lib = load_lib(user_id)
    track = lib.get("tracks", {}).get(track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Unknown track_id")
    enabled = bool(payload.get("enabled", True))
    track["auto_enrich_disabled"] = not enabled
    lib["version"] = int(time.time())
    save_lib(user_id, lib)
    db_upsert_tracks(user_id, [track])
    return {
        "ok": True,
        "track_id": track_id,
        "auto_enrich_enabled": enabled,
        "version": lib["version"],
    }


@router.post("/library/{user_id}/track/{track_id}/hide")
def set_track_hidden(user_id: str, track_id: str, payload: Dict[str, Any]):
    lib = load_lib(user_id)
    track = lib.get("tracks", {}).get(track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Unknown track_id")
    hidden = bool(payload.get("hidden", True))
    reason = str(payload.get("reason") or "").strip()
    track["is_hidden"] = hidden
    if hidden:
        track["hidden_at"] = int(time.time())
        if reason:
            track["hidden_reason"] = reason
    else:
        track.pop("hidden_at", None)
        track.pop("hidden_reason", None)
    lib["version"] = int(time.time())
    save_lib(user_id, lib)
    db_upsert_tracks(user_id, [track])
    return {"ok": True, "track_id": track_id, "is_hidden": hidden, "version": lib["version"]}


@router.delete("/library/{user_id}/track/{track_id}")
def remove_track(user_id: str, track_id: str):
    lib = load_lib(user_id)
    if track_id not in lib.get("tracks", {}):
        raise HTTPException(status_code=404, detail="Unknown track_id")
    lib["tracks"].pop(track_id, None)
    lib["version"] = int(time.time())
    save_lib(user_id, lib)
    db_delete_tracks(user_id, [track_id], purge_related=True)
    return {"ok": True, "removed": True, "track_id": track_id, "version": lib["version"]}

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

    providers = payload.providers or ["musicbrainz", "discogs", "acoustid"]
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
    accepted = best.get("score", 0) >= _provider_min_score(str(best.get("provider") or ""), min_score)
    if accepted and not _candidate_passes_sanity(track, best):
        accepted = False
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
        patched["metadata_source"] = f"manual:{str(best.get('provider') or 'unknown')}"
        patched["metadata_source_score"] = float(best.get("score") or 0.0)
        patched["metadata_source_updated_at"] = int(time.time())
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
    providers = payload.providers or ["musicbrainz", "discogs", "acoustid"]

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
        score = float(best.get("score") or 0.0)
        if score < _provider_min_score(str(best.get("provider") or ""), min_score):
            details.append({
                "track_id": t.get("track_id"),
                "title": t.get("title"),
                "artist": t.get("artist"),
                "best": best,
                "provider_errors": provider_errors,
            })
            continue
        if not _candidate_passes_sanity(t, best):
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
            patched["metadata_source"] = f"batch:{str(best.get('provider') or 'unknown')}"
            patched["metadata_source_score"] = float(best.get("score") or 0.0)
            patched["metadata_source_updated_at"] = int(time.time())
            patched.update(enrich_track_metadata(patched))
            tid = str(t.get("track_id"))
            original = lib["tracks"].get(tid)
            overwrite_ok = _overwrite_allowed(original or {}, best, mode="batch")
            item["overwrite_allowed"] = overwrite_ok
            if overwrite_ok and original and any(original.get(k) != patched.get(k) for k in patched.keys()):
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


@router.get("/mobile/bootstrap/{user_id}")
def get_mobile_bootstrap(user_id: str):
    """
    Tiny/mobile bootstrap.
    Returns only the minimum catalog needed to choose a track without
    shipping full metadata for the entire library.
    """
    lib = load_lib(user_id)
    tracks = []
    mobile_force_mp3 = str(os.getenv("RT_MOBILE_FORCE_MP3", "1")).strip().lower() not in {"0", "false", "off", "no"}
    for t in lib["tracks"].values():
        tid = str(t.get("track_id") or "")
        if not tid:
            continue
        tracks.append({
            "track_id": tid,
            "playability_status": str(t.get("playability_status") or ""),
            "playability_fail_count": int(t.get("playability_fail_count") or 0),
            "playability_last_error": str(t.get("playability_last_error") or ""),
        })

    tracks.sort(key=lambda x: x["track_id"])
    return {
        "ok": True,
        "user_id": user_id,
        "version": int(lib.get("version") or 0),
        "api_base_hint": "/streamer/api",
        "mobile_force_mp3": bool(mobile_force_mp3),
        "generated_at": int(time.time()),
        "tracks": tracks,
    }


@router.get("/mobile/track/{user_id}/{track_id}")
def get_mobile_track_detail(user_id: str, track_id: str):
    lib = load_lib(user_id)
    t = (lib.get("tracks") or {}).get(track_id)
    if not t:
        raise HTTPException(status_code=404, detail="Unknown track_id")
    art_urls = t.get("artwork_urls") or []
    artist_urls = t.get("artist_image_urls") or []
    force_mp3 = _track_needs_mp3_proxy(t)
    mobile_force_mp3 = str(os.getenv("RT_MOBILE_FORCE_MP3", "1")).strip().lower() not in {"0", "false", "off", "no"}
    stream_path_mobile = f"/streamer/api/relay-mp3/{user_id}/{track_id}" if mobile_force_mp3 else (
        f"/streamer/api/{'relay-mp3' if force_mp3 else 'relay'}/{user_id}/{track_id}"
    )
    return {
        "ok": True,
        "track_id": track_id,
        "title": str(t.get("title") or "Unknown Title"),
        "artist": str(t.get("artist") or "Unknown Artist"),
        "album": str(t.get("album") or ""),
        "genre": str(t.get("genre") or ""),
        "year": t.get("year"),
        "duration_sec": float(t.get("duration_sec") or 0),
        "needs_transcode": bool(force_mp3),
        "stream_path_mobile": stream_path_mobile,
        "stream_path_mp3": f"/streamer/api/relay-mp3/{user_id}/{track_id}",
        "stream_path_native": f"/streamer/api/relay/{user_id}/{track_id}",
        "metadata_source": str(t.get("metadata_source") or ""),
        "metadata_source_score": t.get("metadata_source_score"),
        "artwork_url": str(t.get("artwork_url") or (art_urls[0] if art_urls else "") or ""),
        "artwork_urls": art_urls,
        "artist_image_url": str((artist_urls[0] if artist_urls else "") or ""),
        "artist_image_urls": artist_urls,
        "artist_bio": str(t.get("artist_bio") or ""),
        "album_bio": str(t.get("album_bio") or ""),
        "playability_status": str(t.get("playability_status") or ""),
        "playability_fail_count": int(t.get("playability_fail_count") or 0),
        "playability_last_error": str(t.get("playability_last_error") or ""),
    }


@router.post("/mobile/next/{user_id}")
def get_mobile_next_track(user_id: str, payload: MobileNextPayload):
    lib = load_lib(user_id)
    tracks = list((lib.get("tracks") or {}).values())
    if not tracks:
        return {"ok": True, "track": None, "reason": "empty-library"}

    recent = {str(tid or "").strip() for tid in (payload.recent_track_ids or []) if str(tid or "").strip()}
    current_tid = str(payload.current_track_id or "").strip()
    if current_tid:
        recent.add(current_tid)

    playable = []
    fallback = []
    for t in tracks:
        tid = str(t.get("track_id") or "")
        if not tid:
            continue
        status = str(t.get("playability_status") or "").strip().lower()
        fail_count = int(t.get("playability_fail_count") or 0)
        if status == "bad" or fail_count >= PLAYABILITY_BAD_THRESHOLD:
            continue
        fallback.append(tid)
        if tid not in recent:
            playable.append(tid)

    chosen = None
    if playable:
        chosen = random.choice(playable)
    elif fallback:
        chosen = random.choice(fallback)
    if not chosen:
        return {"ok": True, "track": None, "reason": "no-playable-tracks"}
    return {"ok": True, "track": get_mobile_track_detail(user_id, chosen)}


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
    batch_size = len(payload.library or [])
    bulk_scan_payload = batch_size > 1
    ingest_write_only = str(os.getenv("RT_SCAN_INGEST_WRITE_ONLY", "1")).strip().lower() not in {"0", "false", "off", "no"}
    skip_hot_path_enrich = ingest_write_only and bulk_scan_payload

    session_ver = int(payload.library_version or int(time.time()))
    if payload.replace and lib.get("_cleared_for") != session_ver:
        tracks.clear()
        lib["_cleared_for"] = session_ver
        db_mark_all_track_sources_unavailable(payload.user_id)

    db_rows = []
    auto_enrich_candidates: list[str] = []
    try:
        auto_cooldown_sec = int(os.getenv("RT_AUTO_ENRICH_COOLDOWN_SEC", "43200"))
    except Exception:
        auto_cooldown_sec = 43200
    auto_cooldown_sec = max(300, min(auto_cooldown_sec, 86400 * 7))
    now_ts = int(time.time())
    seed_config_enabled = str(os.getenv("RT_DB_SEED_ENABLED", "1")).strip().lower() not in {"0", "false", "off", "no"}
    seed_enabled = seed_config_enabled and not skip_hot_path_enrich
    for t in payload.library:
        d = t.model_dump()
        # Persist scanner-origin core fields so bad enrichments can be rolled back later.
        for fld in ("title", "artist", "album", "genre", "year"):
            d[f"_scan_{fld}"] = d.get(fld)
        if d.get("rel_path"):
            d["rel_path"] = normalize_rel_path(d["rel_path"])
        d = _apply_metadata_library_patch(d, payload.user_id)
        d.update(enrich_track_metadata(d))
        seed = db_find_metadata_seed(payload.user_id, d) if seed_enabled else None
        seed_applied = False
        if seed:
            d = _apply_seed_metadata(d, seed)
            if any(seed.get(k) and d.get(k) == seed.get(k) for k in ("artwork_url", "artist_bio", "album_bio")):
                seed_applied = True
            d.update(enrich_track_metadata(d))
        existing = tracks.get(d["track_id"]) or {}
        # Preserve internal enrichment markers across rescans.
        if existing.get("_auto_enrich_ts"):
            d["_auto_enrich_ts"] = existing.get("_auto_enrich_ts")
        if existing.get("metadata_source") and not d.get("metadata_source"):
            d["metadata_source"] = existing.get("metadata_source")
        if existing.get("metadata_source_score") is not None and d.get("metadata_source_score") is None:
            d["metadata_source_score"] = existing.get("metadata_source_score")
        if existing.get("metadata_source_updated_at") and not d.get("metadata_source_updated_at"):
            d["metadata_source_updated_at"] = existing.get("metadata_source_updated_at")
        if existing.get("auto_enrich_disabled"):
            d["auto_enrich_disabled"] = True
        if existing.get("is_hidden"):
            d["is_hidden"] = True
            if existing.get("hidden_at"):
                d["hidden_at"] = existing.get("hidden_at")
            if existing.get("hidden_reason"):
                d["hidden_reason"] = existing.get("hidden_reason")
        if seed_applied:
            d["metadata_source"] = "seed:db"
            d["metadata_source_score"] = 1.0
        tracks[d["track_id"]] = d
        db_rows.append(d)
        # Incremental enrichment target: new/changed tracks still missing rich media metadata.
        changed_key_fields = any(
            str(existing.get(k) or "").strip() != str(d.get(k) or "").strip()
            for k in ("title", "artist", "album", "rel_path")
        )
        lacks_rich_meta = not _has_rich_media(d)
        try:
            last_attempt_ts = int(existing.get("_auto_enrich_ts") or 0)
        except Exception:
            last_attempt_ts = 0
        cooldown_ok = (now_ts - last_attempt_ts) >= auto_cooldown_sec
        if (
            lacks_rich_meta
            and not bool(d.get("auto_enrich_disabled"))
            and not bool(d.get("is_hidden"))
            and (not last_attempt_ts or cooldown_ok or changed_key_fields)
        ):
            auto_enrich_candidates.append(str(d["track_id"]))

    lib["version"] = session_ver
    save_lib(payload.user_id, lib)
    db_upsert_tracks(payload.user_id, db_rows)
    db_upsert_track_sources(payload.user_id, db_rows)

    # Keep bulk ingest write-only by default. Enrichment should run after scan completion,
    # not inside the submit-scan request path where it causes timeouts and retry churn.
    auto_enrich_config_enabled = str(os.getenv("RT_AUTO_ENRICH_ON_SCAN", "1")).strip().lower() not in {"0", "false", "off", "no"}
    auto_enrich_enabled = auto_enrich_config_enabled and not skip_hot_path_enrich
    auto_scanned = 0
    auto_matched = 0
    auto_applied = 0
    auto_stage1_scanned = 0
    auto_stage1_matched = 0
    auto_stage1_applied = 0
    auto_stage2_scanned = 0
    auto_stage2_matched = 0
    auto_stage2_applied = 0
    provider_raw = str(os.getenv("RT_AUTO_ENRICH_PROVIDERS", "musicbrainz,discogs,acoustid"))
    auto_providers = [p.strip().lower() for p in provider_raw.split(",") if p.strip()]
    if not auto_providers:
        auto_providers = ["musicbrainz", "discogs", "acoustid"]
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
        # Stage 1: text metadata providers first (default: musicbrainz,discogs)
        # Stage 2: acoustid fallback only for stage-1 misses.
        stage1_providers = [p for p in auto_providers if p != "acoustid"]
        stage2_enabled = "acoustid" in auto_providers
        try:
            stage2_limit = int(os.getenv("RT_AUTO_ENRICH_ACOUSTID_LIMIT", "8"))
        except Exception:
            stage2_limit = 8
        stage2_limit = max(0, min(stage2_limit, auto_limit))

        stage1_ids = auto_enrich_candidates[:auto_limit]
        stage2_candidates: list[str] = []

        def _attempt_auto_apply(tid: str, providers: list[str], stage: str) -> bool:
            nonlocal auto_scanned, auto_matched, auto_applied
            nonlocal auto_stage1_scanned, auto_stage1_matched, auto_stage1_applied
            nonlocal auto_stage2_scanned, auto_stage2_matched, auto_stage2_applied
            tcur = tracks.get(tid)
            if not tcur:
                return False
            auto_scanned += 1
            if stage == "stage1":
                auto_stage1_scanned += 1
            else:
                auto_stage2_scanned += 1
            tcur["_auto_enrich_ts"] = now_ts
            searched = search_candidates(tcur, providers=providers, include_errors=True)
            candidates = searched.get("candidates", [])
            if not candidates:
                return False
            best = candidates[0]
            db_insert_provider_snapshot(tid, best)
            score = float(best.get("score") or 0.0)
            min_for_provider = _provider_min_score(str(best.get("provider") or ""), auto_min_score)
            if score < min_for_provider:
                return False
            if not _candidate_passes_sanity(tcur, best):
                return False
            if not _overwrite_allowed(tcur, best, mode="auto"):
                return False
            auto_matched += 1
            if stage == "stage1":
                auto_stage1_matched += 1
            else:
                auto_stage2_matched += 1
            patch = best.get("patch") or {}
            patched = dict(tcur)
            patched.update(patch)
            patched["metadata_source"] = f"auto:{str(best.get('provider') or 'unknown')}"
            patched["metadata_source_score"] = score
            patched["metadata_source_updated_at"] = now_ts
            patched.update(enrich_track_metadata(patched))
            if any(tcur.get(k) != patched.get(k) for k in patched.keys()):
                tracks[tid] = patched
                db_upsert_tracks(payload.user_id, [patched])
                db_upsert_override(tid, payload.user_id, patch)
                auto_applied += 1
                if stage == "stage1":
                    auto_stage1_applied += 1
                else:
                    auto_stage2_applied += 1
                return True
            return True

        for tid in stage1_ids:
            if stage1_providers:
                ok = _attempt_auto_apply(tid, stage1_providers, "stage1")
            else:
                ok = False
            if not ok and stage2_enabled:
                stage2_candidates.append(tid)

        if stage2_enabled:
            for tid in stage2_candidates[:stage2_limit]:
                _attempt_auto_apply(tid, ["acoustid"], "stage2")
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
            "config_enabled": auto_enrich_config_enabled,
            "write_only_skipped": skip_hot_path_enrich,
            "scanned": auto_scanned,
            "matched": auto_matched,
            "applied": auto_applied,
            "stage1": {
                "providers": [p for p in auto_providers if p != "acoustid"],
                "scanned": auto_stage1_scanned,
                "matched": auto_stage1_matched,
                "applied": auto_stage1_applied,
            },
            "stage2": {
                "providers": ["acoustid"] if "acoustid" in auto_providers else [],
                "scanned": auto_stage2_scanned,
                "matched": auto_stage2_matched,
                "applied": auto_stage2_applied,
            },
            "seed_enabled": seed_enabled,
            "seed_config_enabled": seed_config_enabled,
        },
        "ingest_mode": "write-only" if skip_hot_path_enrich else "inline-enrich",
        "preview": preview
    }

# -------- relay (GET/HEAD) --------
@router.api_route("/relay/{user_id}/{track_id}", methods=["GET", "HEAD"])
def relay(user_id: str, track_id: str, request: Request):
    lib = load_lib(user_id)
    track = lib["tracks"].get(track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Unknown track_id")

    # Legacy fast path for FLAC/legacy codecs: avoid extra upstream probe/redirect hops.
    if _track_needs_mp3_proxy(track):
        target_url = str(request.url_for("relay_mp3", user_id=user_id, track_id=track_id))
        if request.url.query:
            target_url = f"{target_url}?{request.url.query}"
        return RedirectResponse(url=target_url, status_code=302)

    url = build_stream_url(user_id, track)
    if not url:
        _mark_track_playability(user_id, track_id, ok=False, reason="no-base-url-or-rel-path")
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
        _mark_track_playability(user_id, track_id, ok=False, reason=f"upstream-fetch-failed:{e}")
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
        _mark_track_playability(user_id, track_id, ok=False, reason=f"upstream-http-{status}")
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

    _mark_track_playability(user_id, track_id, ok=True)

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
        _mark_track_playability(user_id, track_id, ok=False, reason="no-base-url-or-rel-path")
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

    cache_enabled = _env_bool("RT_MP3_CACHE_ENABLED", default=False)
    cache_strict = _env_bool("RT_MP3_CACHE_STRICT", default=False)
    if cache_enabled:
        cached_path = _try_build_cached_mp3(
            user_id=user_id,
            track_id=track_id,
            src_url=url,
            abr_kbps=abr_kbps,
        )
        if cached_path:
            print(f"[relay-mp3] mode=cached user={user_id} track={track_id} file={cached_path}")
            if request.method != "HEAD":
                _mark_track_playability(user_id, track_id, ok=True)
            return _serve_mp3_file(
                path=cached_path,
                request=request,
                duration_sec=duration_sec,
                abr_kbps=abr_kbps,
                start_sec=start_sec,
            )
        print(f"[relay-mp3] mode=live-fallback user={user_id} track={track_id} reason=cache-miss-or-build-failed")
        if cache_strict:
            _mark_track_playability(user_id, track_id, ok=False, reason="cache-strict-no-cache")
            raise HTTPException(
                status_code=503,
                detail="cache-backed relay enabled but cache build unavailable; strict mode blocks live fallback",
            )

    if request.method == "HEAD":
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-store, must-revalidate, no-transform",
            "Content-Type": "audio/mpeg",
            "Accept-Ranges": "none",
            "X-Relay-Mode": "live-transcode",
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
        _mark_track_playability(user_id, track_id, ok=False, reason=f"ffmpeg-spawn-failed:{e}")
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

    _mark_track_playability(user_id, track_id, ok=True)
    return StreamingResponse(gen(), media_type="audio/mpeg", headers=headers)
