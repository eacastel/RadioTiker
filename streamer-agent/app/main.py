from pathlib import Path
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
import json, time, requests

app = FastAPI(title="RadioTiker Streamer API")

ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT / "data" / "user-libraries"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# in-memory
LIBS: Dict[str, Dict[str, Any]] = {}  # user_id -> {"tracks": {track_id: track}, "version": int}

class Track(BaseModel):
    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    path: Optional[str] = None
    stream_url: Optional[str] = None
    file_size: Optional[int] = None
    mtime: Optional[int] = None
    duration_sec: Optional[float] = None
    track_id: str

class ScanPayload(BaseModel):
    user_id: str
    library: List[Track]
    library_version: Optional[int] = None

def store_path(user_id: str) -> Path:
    safe = "".join(ch for ch in user_id if ch.isalnum() or ch in ("-", "_", "."))
    return DATA_DIR / f"{safe}.json"

def load_lib(user_id: str) -> Dict[str, Any]:
    if user_id in LIBS:
        return LIBS[user_id]
    p = store_path(user_id)
    if p.exists():
        try:
            obj = json.loads(p.read_text())
            if isinstance(obj, dict) and "tracks" in obj:
                LIBS[user_id] = obj
                return obj
        except Exception:
            pass
    LIBS[user_id] = {"tracks": {}, "version": int(time.time())}
    return LIBS[user_id]

def save_lib(user_id: str, lib: Dict[str, Any]):
    LIBS[user_id] = lib
    store_path(user_id).write_text(json.dumps(lib, indent=2))

@app.get("/api/health")
def health():
    return {"ok": True}

@app.post("/api/submit-scan")
def submit_scan(payload: ScanPayload):
    lib = load_lib(payload.user_id)
    tracks = lib["tracks"]

    # Upsert each by track_id
    for t in payload.library:
        d = t.model_dump()
        tracks[d["track_id"]] = d

    # bump version each submit (or use provided version)
    lib["version"] = payload.library_version or int(time.time())
    save_lib(payload.user_id, lib)

    preview = []
    for i, (_, v) in enumerate(tracks.items()):
        if i >= 3: break
        preview.append({k: v.get(k) for k in ("title", "artist", "track_id", "stream_url")})

    return {"ok": True, "user_id": payload.user_id, "count": len(tracks), "version": lib["version"], "preview": preview}

@app.get("/api/library/{user_id}")
def get_library(user_id: str):
    lib = load_lib(user_id)
    return {"version": lib["version"], "tracks": list(lib["tracks"].values())}

@app.get("/relay/{user_id}/{track_id}")
def relay(user_id: str, track_id: str, request: Request):
    lib = load_lib(user_id)
    track = lib["tracks"].get(track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Unknown track_id")

    url = track.get("stream_url")
    if not url:
        raise HTTPException(status_code=400, detail="No stream_url for this track")

    headers = {"User-Agent": "RadioTiker-Relay/0.2"}
    rng = request.headers.get("range")
    if rng:
        headers["Range"] = rng

    try:
        upstream = requests.get(url, stream=True, timeout=(5, 30), headers=headers)
        upstream.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Upstream fetch failed: {e}")

    passthrough = {}
    for k in ["Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Cache-Control", "ETag", "Last-Modified"]:
        v = upstream.headers.get(k)
        if v: passthrough[k] = v

    # discourage client/proxy caching so new scans take effect immediately
    passthrough.setdefault("Cache-Control", "no-store")

    def gen():
        for chunk in upstream.iter_content(chunk_size=64 * 1024):
            if chunk:
                yield chunk

    status = upstream.status_code  # 200 or 206
    media = upstream.headers.get("Content-Type") or "audio/mpeg"
    return StreamingResponse(gen(), media_type=media, headers=passthrough, status_code=status)

@app.get("/user/{user_id}/play", response_class=HTMLResponse)
def player(user_id: str):
    lib = load_lib(user_id)
    rows = []
    for t in lib["tracks"].values():
        title = t.get("title") or "Unknown Title"
        artist = t.get("artist") or "Unknown Artist"
        mark = "✅" if t.get("stream_url") else "—"
        rows.append(f"""
          <tr>
            <td>{title}</td>
            <td>{artist}</td>
            <td style="text-align:center">{mark}</td>
            <td><button onclick="playId('{t['track_id']}')">Play</button></td>
          </tr>
        """)
    rows_html = "\n".join(rows) or "<tr><td colspan='4'>No tracks yet.</td></tr>"

    html = f"""<!doctype html>
<html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>RadioTiker – {user_id}</title>
<style>
 body{{font-family:system-ui,Segoe UI,Roboto,Arial;padding:24px}}
 table{{width:100%;border-collapse:collapse;margin-top:12px}}
 th,td{{border-bottom:1px solid #eee;padding:8px;text-align:left}}
 thead th{{background:#fafafa}}
</style></head>
<body>
<h1>RadioTiker – Player</h1>
<p>User: <b>{user_id}</b> &middot; Library version: <b>{lib['version']}</b></p>

<audio id="player" controls preload="none" style="width:100%">
  <source id="src" src="" type="audio/mpeg"/>
</audio>
<div style="margin-top:10px"><b>Now Playing:</b> <span id="now">—</span></div>

<table>
<thead><tr><th>Title</th><th>Artist</th><th>URL?</th><th>Action</th></tr></thead>
<tbody>{rows_html}</tbody>
</table>

<script>
const userId = {json.dumps(user_id)};
function playId(tid){{
  const url = `/streamer/api/relay/${{userId}}/${{tid}}?v=${libver()}`;
  const audio = document.getElementById('player');
  const src = document.getElementById('src');
  src.src = url;
  audio.load();
  audio.play().catch(()=>{{}});
  document.getElementById('now').innerText = tid;
}}
function libver(){{
  // Cheap cache-bust: current seconds
  return Math.floor(Date.now()/1000);
}}
</script>
</body></html>"""
    return HTMLResponse(html, status_code=200)
