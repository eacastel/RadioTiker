from typing import Dict, Any, Optional
import json, time, requests
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from ..models import ScanPayload, AnnouncePayload
from ..storage import load_lib, save_lib, load_agent, save_agent_stable
from ..utils import normalize_rel_path, build_stream_url

router = APIRouter(prefix="/api", tags=["core"])

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

    for t in payload.library:
        d = t.model_dump()
        if d.get("rel_path"):
            d["rel_path"] = normalize_rel_path(d["rel_path"])
        tracks[d["track_id"]] = d

    lib["version"] = session_ver
    save_lib(payload.user_id, lib)

    preview = []
    for i, (_, v) in enumerate(tracks.items()):
        if i >= 3:
            break
        preview.append({k: v.get(k) for k in ("title", "artist", "album", "track_id", "rel_path")})

    st = load_agent(payload.user_id)
    return {
        "ok": True,
        "user_id": payload.user_id,
        "count": len(tracks),
        "version": lib["version"],
        "agent_base_url": st.get("base_url"),
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
        probe_h = dict(base_headers); probe_h["Range"] = "bytes=0-0"
        probe = requests.head(url, timeout=(5, 10), headers=probe_h, allow_redirects=True)
        if probe.status_code == 206 or \
           probe.headers.get("Accept-Ranges", "").lower() == "bytes" or \
           probe.headers.get("Content-Range"):
            upstream_accepts_ranges = True
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
            else requests.get(url, stream=True, timeout=(5, 300), headers=headers)
        )
    except requests.RequestException as e:
        print(f"[relay] upstream error user={user_id} track={track_id} url={url} err={e}")
        raise HTTPException(status_code=502, detail=f"Upstream fetch failed: {e}")

    status = upstream.status_code
    if status >= 400:
        body_preview = None
        try:
            body_preview = upstream.text[:400]
        except Exception:
            pass
        print(f"[relay] upstream HTTP {status} user={user_id} track={track_id} url={url} body={body_preview!r}")
        raise HTTPException(status_code=status, detail=f"Upstream returned {status}")

    passthrough: Dict[str, str] = {}
    for k in ["Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Cache-Control", "ETag", "Last-Modified"]:
        v = upstream.headers.get(k)
        if v:
            passthrough[k] = v

    passthrough.setdefault("Accept-Ranges", "bytes")
    passthrough.setdefault("Cache-Control", "no-store")

    requested_range = ("Range" in headers) or (client_range is not None)
    total_len = upstream.headers.get("Content-Length")
    if requested_range and status == 200 and total_len and not passthrough.get("Content-Range"):
        try:
            total = int(total_len)
            passthrough["Content-Range"] = f"bytes 0-{total-1}/{total}"
            status = 206
        except Exception:
            pass

    media = upstream.headers.get("Content-Type") or "audio/mpeg"

    if request.method == "HEAD":
        return Response(status_code=status, headers=passthrough, media_type=media)

    def gen():
        for chunk in upstream.iter_content(chunk_size=256 * 1024):
            if chunk:
                yield chunk

    return StreamingResponse(gen(), media_type=media, headers=passthrough, status_code=status)
