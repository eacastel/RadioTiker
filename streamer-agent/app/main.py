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

@app.post("/api/library/{user_id}/clear")
def clear_library(user_id: str):
    lib = load_lib(user_id)
    lib["tracks"] = {}
    lib["version"] = int(time.time())
    save_lib(user_id, lib)
    return {"ok": True, "cleared": True, "version": lib["version"]}

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

@app.get("/api/agent/{user_id}/status")
def agent_status(user_id: str):
    st = load_agent(user_id)
    last_seen = int(st.get("last_seen", 0) or 0)
    base_url = st.get("base_url")
    online = bool(base_url) and (int(time.time()) - last_seen < 120)
    return {
        "online": online,
        "base_url": base_url,
        "last_seen": last_seen,
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
        tid    = t.get("track_id")
        title  = t.get("title") or "Unknown Title"
        artist = t.get("artist") or "Unknown Artist"
        album  = t.get("album") or ""
        mark   = "✅" if t.get("rel_path") else "—"
        rows.append(f"""
          <tr data-tid="{tid}" data-title="{title}" data-artist="{artist}" data-album="{album}">
            <td>{title}</td>
            <td>{artist}</td>
            <td style="text-align:center">{mark}</td>
            <td><button onclick="playId('{tid}')">Play</button></td>
          </tr>
        """)
    rows_html = "\n".join(rows) or "<tr><td colspan='4'>No tracks yet.</td></tr>"

    html = f"""<!doctype html>
<html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>RadioTiker – {user_id}</title>
<style>
 body{{font-family:system-ui,Segoe UI,Roboto,Arial;margin:0}}
 .playerbar{{position:sticky;top:0;z-index:10;background:#fff;border-bottom:1px solid #eee;padding:12px}}
 .row{{display:flex;gap:12px;align-items:center;flex-wrap:wrap}}
 .pill{{border:1px solid #ddd;border-radius:999px;padding:4px 10px}}
 .btn{{padding:6px 10px;border-radius:6px;border:1px solid #ddd;background:#fafafa;cursor:pointer}}
 .btn:hover{{filter:brightness(1.03)}}
 .danger{{background:#b91c1c;color:#fff;border:none}}
 .dot{{width:10px;height:10px;border-radius:50%;display:inline-block;vertical-align:middle;margin-right:6px;background:#bbb}}
 .dot.ok{{background:#16a34a}}
 table{{width:100%;border-collapse:collapse;margin-top:12px}}
 th,td{{border-bottom:1px solid #eee;padding:8px;text-align:left}}
 thead th{{background:#fafafa}}
 main{{padding:16px 24px 40px}}
</style></head>
<body>

<div class="playerbar">
  <div class="row">
    <span id="stDot" class="dot"></span>
    <strong>RadioTiker — {user_id}</strong>
    <button class="btn" onclick="playPrev()">⏮️ Prev</button>
    <button class="btn" onclick="playNext()">⏭️ Next</button>
    <label class="pill"><input type="checkbox" id="autoplay" checked> Autoplay</label>
    <label class="pill"><input type="checkbox" id="shuffle"> Shuffle</label>
    <button class="btn danger" onclick="confirmClear()">Clear library</button>
  </div>
  <div class="row" style="margin-top:8px">
    <audio id="player" controls preload="none" style="width:100%">
      <source id="src" src="" type="audio/mpeg"/>
    </audio>
  </div>
  <div style="margin-top:6px"><b>Now Playing:</b> <span id="now">—</span></div>
</div>

<main>
  <p>User: <b>{user_id}</b> · Library version: <b>{lib['version']}</b></p>
  <table>
    <thead><tr><th>Title</th><th>Artist</th><th>OK?</th><th>Action</th></tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
</main>

<script>
function libver() {{ return Math.floor(Date.now()/1000); }}
const userId = "{user_id}";

let order = Array.from(document.querySelectorAll('tbody tr')).map(function(_, i) {{ return i; }});
let current = -1; // index into 'order'

function metaFor(tid) {{
  const row = Array.from(document.querySelectorAll('tbody tr'))
             .find(function(r) {{ return r.getAttribute('data-tid') === tid; }});
  if (!row) return {{title:tid, artist:"", album:""}};
  return {{
    title: row.getAttribute('data-title') || tid,
    artist: row.getAttribute('data-artist') || "",
    album: row.getAttribute('data-album') || ""
  }};
}}

function trackIdAtRow(i) {{
  const row = document.querySelectorAll('tbody tr')[i];
  return row ? row.getAttribute('data-tid') : null;
}}

function rowIndexOfTid(tid) {{
  const rows = Array.from(document.querySelectorAll('tbody tr'));
  for (let i=0;i<rows.length;i++) if (rows[i].getAttribute('data-tid')===tid) return i;
  return -1;
}}

function playId(tid) {{
  const url = "/streamer/api/relay/" + userId + "/" + tid + "?v=" + libver();
  const audio = document.getElementById('player');
  const src = document.getElementById('src');
  src.src = url;
  audio.load();
  audio.play().catch(function(){{}});

  const m = metaFor(tid);
  const albumText = m.album ? " (" + m.album + ")" : "";
  document.getElementById('now').innerText = m.artist + " — " + m.title + albumText;

  const ri = rowIndexOfTid(tid);
  if (ri >= 0) {{
    const pos = order.indexOf(ri);
    if (pos >= 0) current = pos;
  }}
}}

function pickNextIndex() {{
  const shuffle = document.getElementById('shuffle').checked;
  if (order.length === 0) return -1;
  if (shuffle) {{
    return Math.floor(Math.random() * order.length);
  }} else {{
    return (current + 1) % order.length;
  }}
}}

function pickPrevIndex() {{
  if (order.length === 0) return -1;
  return (current - 1 + order.length) % order.length;
}}

function playNext() {{
  if (order.length === 0) return;
  current = pickNextIndex();
  const tid = trackIdAtRow(order[current]);
  if (tid) playId(tid);
}}

function playPrev() {{
  if (order.length === 0) return;
  current = pickPrevIndex();
  const tid = trackIdAtRow(order[current]);
  if (tid) playId(tid);
}}

document.getElementById('player').addEventListener('ended', function() {{
  const autoplay = document.getElementById('autoplay').checked;
  if (autoplay) playNext();
}});

// Agent status dot
async function refreshStatus() {{
  try {{
    const res = await fetch("/streamer/api/agent/" + userId + "/status");
    if (!res.ok) throw new Error(await res.text());
    const j = await res.json();
    const dot = document.getElementById('stDot');
    if (j.online) dot.classList.add('ok'); else dot.classList.remove('ok');
  }} catch (e) {{
    document.getElementById('stDot').classList.remove('ok');
  }}
}}
refreshStatus();
setInterval(refreshStatus, 10000);


// Clear library
async function confirmClear() {{
  if (!confirm("Are you sure you want to CLEAR your library on the server?")) return;
  try {{
    const res = await fetch("/streamer/api/library/" + userId + "/clear", {{method:'POST'}});
    if (!res.ok) throw new Error(await res.text());
    location.reload();
  }} catch (e) {{
    alert("Clear failed: " + e.message);
  }}
}}
</script>
</body></html>"""
    return HTMLResponse(html, status_code=200)


@app.get("/api/radio/{user_id}", response_class=HTMLResponse)
def radio_page(user_id: str):
    lib = load_lib(user_id)
    tracks = []
    for t in lib["tracks"].values():
        tracks.append({
            "track_id": t.get("track_id"),
            "title": t.get("title") or "Unknown Title",
            "artist": t.get("artist") or "Unknown Artist",
            "album": t.get("album") or "",
        })
    tracks_json = json.dumps(tracks)

    html = f"""<!doctype html>
<html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{user_id} — Radio</title>
<style>
 body{{font-family:system-ui,Segoe UI,Roboto,Arial;margin:0;padding:20px}}
 .bar{{max-width:900px;margin:0 auto}}
 .dot{{width:10px;height:10px;border-radius:50%;display:inline-block;vertical-align:middle;margin-right:6px;background:#bbb}}
 .dot.ok{{background:#16a34a}}
 .row{{display:flex;align-items:center;gap:12px;flex-wrap:wrap}}
 .now{{margin-top:10px}}
 .hint{{color:#666;font-size:14px;margin-top:6px}}
</style></head>
<body>
<div class="bar">
  <div class="row">
    <span id="stDot" class="dot"></span>
    <strong>{user_id} — Radio</strong>
  </div>
  <div class="row" style="margin-top:8px">
    <audio id="player" controls preload="none" style="width:100%">
      <source id="src" src="" type="audio/mpeg"/>
    </audio>
  </div>
  <div class="now"><b>Now Playing:</b> <span id="now">—</span></div>
  <div class="hint">Tip: tap ▶️ to start. The dot is green when your agent is online.</div>
</div>

<script>
const userId = "{user_id}";
const TRACKS = {tracks_json};

function libver() {{ return Math.floor(Date.now()/1000); }}

function pickIndex() {{
  if (TRACKS.length === 0) return -1;
  return Math.floor(Math.random() * TRACKS.length); // shuffle by default
}}

function playIndex(i) {{
  const m = TRACKS[i];
  if (!m) return;
  const url = "/streamer/api/relay/" + userId + "/" + m.track_id + "?v=" + libver();
  const audio = document.getElementById('player');
  const src = document.getElementById('src');
  src.src = url;
  audio.load();
  audio.play().catch(function(){{}});
  const albumText = m.album ? " (" + m.album + ")" : "";
  document.getElementById('now').innerText = m.artist + " — " + m.title + albumText;
}}

let current = -1;
function playNext() {{
  current = pickIndex();
  if (current >= 0) playIndex(current);
}}

// 1) If user hits ▶️ with no source yet, kick off playback, then keep native controls.
const audio = document.getElementById('player');
function firstPlayKick() {{
  if (!audio.currentSrc || audio.currentSrc === "" || audio.currentSrc === window.location.href) {{
    playNext();
    // Try again after setting the src (some browsers need a 0-tick delay)
    setTimeout(function(){{ audio.play().catch(function(){{}}); }}, 0);
  }}
}}
audio.addEventListener('play', firstPlayKick, {{ once:false }});

// 2) Keep going when a track ends
audio.addEventListener('ended', function() {{ playNext(); }});

// 3) Agent status dot
async function refreshStatus() {{
  try {{
    const res = await fetch("/streamer/api/agent/" + userId + "/status");
    if (!res.ok) throw new Error(await res.text());
    const j = await res.json();
    const dot = document.getElementById('stDot');
    if (j.online) dot.classList.add('ok'); else dot.classList.remove('ok');
  }} catch (e) {{
    document.getElementById('stDot').classList.remove('ok');
  }}
}}
refreshStatus();
setInterval(refreshStatus, 10000);
</script>
</body></html>"""
    return HTMLResponse(html, status_code=200)

