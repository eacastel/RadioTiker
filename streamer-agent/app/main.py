# app/main.py
from pathlib import Path
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
from urllib.parse import urlparse, unquote
import json, time, requests

app = FastAPI(title="RadioTiker Streamer API")

# === storage ===
ROOT = Path(__file__).resolve().parent.parent.parent  # radio-tiker-core/
DATA_DIR = ROOT / "data" / "user-libraries"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# === state ===
LIBS: Dict[str, Dict[str, Any]] = {}    # user_id -> {"tracks": {track_id: track}, "version": int}
AGENTS: Dict[str, Dict[str, Any]] = {}  # user_id -> {"base_url": str, "last_seen": int}

# -------- models --------
class Track(BaseModel):
    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    path: Optional[str] = None
    rel_path: Optional[str] = None   # canonical relative path under agent root
    file_size: Optional[int] = None
    mtime: Optional[int] = None
    duration_sec: Optional[float] = None
    track_id: str

class ScanPayload(BaseModel):
    user_id: str
    library: List[Track]
    library_version: Optional[int] = None
    replace: Optional[bool] = False

class AnnouncePayload(BaseModel):
    user_id: str
    base_url: str

# -------- helpers --------
def _safe_name(s: str) -> str:
    return "".join(ch for ch in s if ch.isalnum() or ch in ("-", "_", "."))

def lib_path(user_id: str) -> Path:
    return DATA_DIR / f"{_safe_name(user_id)}.json"

def agent_path(user_id: str) -> Path:
    return DATA_DIR / f"{_safe_name(user_id)}.agent.json"

def load_lib(user_id: str) -> Dict[str, Any]:
    if user_id in LIBS:
        return LIBS[user_id]
    p = lib_path(user_id)
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
    lib_path(user_id).write_text(json.dumps(lib, indent=2))

def load_agent(user_id: str) -> Dict[str, Any]:
    if user_id in AGENTS:
        return AGENTS[user_id]
    p = agent_path(user_id)
    if p.exists():
        try:
            AGENTS[user_id] = json.loads(p.read_text())
            return AGENTS[user_id]
        except Exception:
            pass
    AGENTS[user_id] = {}
    return AGENTS[user_id]

def save_agent(user_id: str, st: Dict[str, Any]):
    AGENTS[user_id] = st
    agent_path(user_id).write_text(json.dumps(st, indent=2))

def build_stream_url(user_id: str, t: Dict[str, Any]) -> Optional[str]:
    """Rebuild the file URL using the agent's current base_url + stored rel_path."""
    st = load_agent(user_id)
    base = st.get("base_url")
    rel = t.get("rel_path")
    if not base or not rel:
        return None
    # client already URL-encodes path segments; send as-is
    return f"{base.rstrip('/')}/{rel.lstrip('/')}"

# -------- endpoints (all under /api/*) --------

@app.get("/api/health")
def health():
    return {"ok": True}

@app.post("/api/agent/announce")
def agent_announce(payload: AnnouncePayload):
    st = load_agent(payload.user_id)
    st["base_url"] = payload.base_url.rstrip("/")
    st["last_seen"] = int(time.time())
    save_agent(payload.user_id, st)
    return {"ok": True, "base_url": st["base_url"]}

@app.post("/api/submit-scan")
def submit_scan(payload: ScanPayload):
    lib = load_lib(payload.user_id)
    if payload.replace:
        lib["tracks"] = {}  
    tracks = lib["tracks"]

     # NEW: honor optional replace flag (clear once on the first batch)
    try:
        body = payload.model_dump()
        if body.get("replace"):
            tracks.clear()
    except Exception:
        pass

    for t in payload.library:
        d = t.model_dump()
        # migrate stream_url->rel_path if you still support it
        tracks[d["track_id"]] = d

    # Keep a consistent version for the whole upload session
    lib["version"] = payload.library_version or int(time.time())
    save_lib(payload.user_id, lib)

    preview = []
    for i, (_, v) in enumerate(tracks.items()):
        if i >= 3: break
        preview.append({k: v.get(k) for k in ("title", "artist", "track_id", "rel_path")})

    st = load_agent(payload.user_id)
    return {
        "ok": True,
        "user_id": payload.user_id,
        "count": len(tracks),
        "version": lib["version"],
        "agent_base_url": st.get("base_url"),
        "preview": preview
    }


@app.get("/api/library/{user_id}")
def get_library(user_id: str):
    lib = load_lib(user_id)
    return {"version": lib["version"], "tracks": list(lib["tracks"].values())}

@app.get("/api/relay/{user_id}/{track_id}")
def relay(user_id: str, track_id: str, request: Request):
    lib = load_lib(user_id)
    track = lib["tracks"].get(track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Unknown track_id")

    url = build_stream_url(user_id, track)
    if not url:
        raise HTTPException(status_code=503, detail="Agent offline or base_url/rel_path unknown")

    headers = {"User-Agent": "RadioTiker-Relay/0.3"}
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
        if v:
            passthrough[k] = v
    # ensure fresh after rescans
    passthrough.setdefault("Cache-Control", "no-store")

    def gen():
        for chunk in upstream.iter_content(chunk_size=64 * 1024):
            if chunk:
                yield chunk

    status = upstream.status_code  # 200 or 206
    media = upstream.headers.get("Content-Type") or "audio/mpeg"
    return StreamingResponse(gen(), media_type=media, headers=passthrough, status_code=status)

@app.get("/api/user/{user_id}/play", response_class=HTMLResponse)
def player(user_id: str):
    lib = load_lib(user_id)
    rows = []
    for t in lib["tracks"].values():
        title = t.get("title") or "Unknown Title"
        artist = t.get("artist") or "Unknown Artist"
        mark = "✅" if t.get("rel_path") else "—"
        rows.append(f"""
          <tr>
            <td>{title}</td>
            <td>{artist}</td>
            <td style="text-align:center">{mark}</td>
            <td><button onclick="playId('{t['track_id']}')">Play</button></td>
          </tr>
        """)
    rows_html = "\n".join(rows) or "<tr><td colspan='4'>No tracks yet.</td></tr>"

    # Note every { and } in CSS/JS is doubled {{ }}, and JS template literals use ${{...}}
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>RadioTiker – {user_id}</title>
<style>
 body{{{{font-family:system-ui,Segoe UI,Roboto,Arial;padding:24px}}}}
 table{{{{width:100%;border-collapse:collapse;margin-top:12px}}}}
 th,td{{{{border-bottom:1px solid #eee;padding:8px;text-align:left}}}}
 thead th{{{{background:#fafafa}}}}
 .controls{{{{margin:10px 0;display:flex;gap:12px;align-items:center}}}}
</style></head>
<body>
<h1>RadioTiker – Player</h1>
<p>User: <b>{user_id}</b> &middot; Library version: <b>{lib['version']}</b></p>

<div class="controls">
  <label><input type="checkbox" id="autoplay" checked> Autoplay next</label>
  <label><input type="checkbox" id="shuffle"> Shuffle</label>
</div>

<audio id="player" controls preload="none" style="width:100%">
  <source id="src" src="" type="audio/mpeg"/>
</audio>
<div style="margin-top:10px"><b>Now Playing:</b> <span id="now">—</span></div>

<table>
<thead><tr><th>Title</th><th>Artist</th><th>OK?</th><th>Action</th></tr></thead>
<tbody>{rows_html}</tbody>
</table>

<script>
function libver() {{ return Math.floor(Date.now()/1000); }}
const userId = "{user_id}";

let order = Array.from(document.querySelectorAll('tbody tr')).map((_, i) => i);
let current = -1;

function playId(tid) {{
  const url = `/streamer/api/relay/${{userId}}/${{tid}}?v=${{libver()}}`;
  const audio = document.getElementById('player');
  const src = document.getElementById('src');
  src.src = url;
  audio.load();
  audio.play().catch(()=>{{}});
  document.getElementById('now').innerText = tid;
}}

function trackIdAtRow(i) {{
  const btn = document.querySelectorAll('tbody tr')[i]?.querySelector('button');
  if (!btn) return null;
  const m = btn.getAttribute('onclick').match(/playId\\('([^']+)'\\)/);
  return m ? m[1] : null;
}}

function pickNext() {{
  const shuffle = document.getElementById('shuffle').checked;
  if (shuffle) {{
    return Math.floor(Math.random() * order.length);
  }} else {{
    return (current + 1) % order.length;
  }}
}}

function playNext() {{
  current = pickNext();
  const tid = trackIdAtRow(order[current]);
  if (tid) playId(tid);
}}

document.getElementById('player').addEventListener('ended', () => {{
  const autoplay = document.getElementById('autoplay').checked;
  if (autoplay) playNext();
}});
</script>
</body></html>"""
    return HTMLResponse(html, status_code=200)
