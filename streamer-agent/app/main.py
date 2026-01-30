# app/main.py
from pathlib import Path
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
from urllib.parse import quote, unquote
import json, time, requests

app = FastAPI(title="RadioTiker Streamer API")

# === storage ===
ROOT = Path(__file__).resolve().parent.parent.parent  # radio-tiker-core/
DATA_DIR = ROOT / "data" / "user-libraries"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# === state ===
# LIBS[user] -> {"tracks": {track_id: track_dict}, "version": int, "_cleared_for": int}
# AGENTS[user] -> {"base_url": str, "last_seen": int}
LIBS: Dict[str, Dict[str, Any]] = {}
AGENTS: Dict[str, Dict[str, Any]] = {}

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
    LIBS[user_id] = {"tracks": {}, "version": int(time.time()), "_cleared_for": 0}
    return LIBS[user_id]

def save_lib(user_id: str, lib: Dict[str, Any]):
    LIBS[user_id] = lib
    lib_path(user_id).write_text(json.dumps(lib, indent=2))

def load_agent(user_id: str) -> Dict[str, Any]:
    if user_id in AGENTS:
        return AGENTS[user_id]
    p = agent_path(user_id)
    st: Dict[str, Any] = {}
    if p.exists():
        try:
            # file only contains stable keys (e.g., base_url)
            st = json.loads(p.read_text())
        except Exception:
            st = {}
    st.setdefault("last_seen", 0)  # volatile default (in-memory only)
    AGENTS[user_id] = st
    return st

def save_agent_stable(user_id: str, st: Dict[str, Any]):
    """Persist only stable fields (like base_url). Do not persist last_seen."""
    stable = {"base_url": st.get("base_url")}
    AGENTS[user_id] = {**st}  # keep full state in memory
    agent_path(user_id).write_text(json.dumps(stable, indent=2))

def normalize_rel_path(rel: str) -> str:
    """Normalize rel_path: ensure exactly one level of URL-encoding per segment."""
    rel = rel.lstrip("/")
    segs = [quote(unquote(s), safe="@!$&'()*+,;=:_-.") for s in rel.split("/")]
    return "/".join(segs)

def build_stream_url(user_id: str, t: Dict[str, Any]) -> Optional[str]:
    """Rebuild file URL using agent's base_url + stored rel_path (no re-encode here)."""
    st = load_agent(user_id)
    base = st.get("base_url")
    rel  = t.get("rel_path")
    if not base or not rel:
        return None
    return f"{base.rstrip('/')}/{rel.lstrip('/')}"

# -------- debug --------
@app.get("/api/debug/peek/{user_id}/{track_id}")
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

# -------- endpoints (all under /api/*) --------
@app.get("/api/health")
def health():
    return {"ok": True}

@app.post("/api/library/{user_id}/clear")
def clear_library(user_id: str):
    lib = load_lib(user_id)
    lib["tracks"] = {}
    lib["version"] = int(time.time())
    lib["_cleared_for"] = 0
    save_lib(user_id, lib)
    return {"ok": True, "cleared": True, "version": lib["version"]}

@app.post("/api/library/{user_id}/migrate-relpaths")
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

@app.post("/api/agent/announce")
def agent_announce(payload: AnnouncePayload):
    """
    Persist only when base_url changes; update last_seen in memory every call.
    """
    st = load_agent(payload.user_id)
    new_base = payload.base_url.rstrip("/")
    changed = (st.get("base_url") != new_base)

    st["base_url"] = new_base
    st["last_seen"] = int(time.time())
    AGENTS[payload.user_id] = st  # in-memory heartbeat

    if changed:
        save_agent_stable(payload.user_id, st)

    return {"ok": True, "base_url": st["base_url"], "persisted": changed}

@app.post("/api/submit-scan")
def submit_scan(payload: ScanPayload):
    """
    Idempotent replace:
      - If replace=True AND we haven't cleared for this library_version, clear once and remember it.
      - Normalize rel_path for every incoming track.
    """
    lib = load_lib(payload.user_id)
    tracks = lib["tracks"]

    # One-time clear per version when replace=True
    session_ver = int(payload.library_version or int(time.time()))
    if payload.replace and lib.get("_cleared_for") != session_ver:
        tracks.clear()
        lib["_cleared_for"] = session_ver

    # Upsert tracks from batch with normalized rel_path
    for t in payload.library:
        d = t.model_dump()
        if d.get("rel_path"):
            d["rel_path"] = normalize_rel_path(d["rel_path"])
        tracks[d["track_id"]] = d

    # Keep a consistent version (the session’s version wins)
    lib["version"] = session_ver
    save_lib(payload.user_id, lib)

    # Tiny preview for sanity
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

@app.get("/api/agent/{user_id}/status")
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

@app.get("/api/library/{user_id}")
def get_library(user_id: str):
    lib = load_lib(user_id)
    return {"version": lib["version"], "tracks": list(lib["tracks"].values())}

@app.api_route("/api/relay/{user_id}/{track_id}", methods=["GET", "HEAD"])
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

    # --- Probe upstream range capability (quick HEAD) ---
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

    # --- Decide Range we send upstream ---
    headers = dict(base_headers)
    if request.method == "GET":
        if client_range:
            headers["Range"] = client_range                         # honor client seek
        elif upstream_accepts_ranges:
            headers["Range"] = "bytes=0-"                           # coax 206 for playback
    else:  # HEAD
        if client_range:
            headers["Range"] = client_range
        elif upstream_accepts_ranges:
            headers["Range"] = "bytes=0-0"

    # --- Fetch upstream ---
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

    # --- Build response headers ---
    passthrough: Dict[str, str] = {}
    for k in ["Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Cache-Control", "ETag", "Last-Modified"]:
        v = upstream.headers.get(k)
        if v:
            passthrough[k] = v

    # Always advertise byte serving to the browser
    passthrough.setdefault("Accept-Ranges", "bytes")
    # Ensure fresh after rescans
    passthrough.setdefault("Cache-Control", "no-store")

    # If a range was requested (by client or by us) but upstream replied 200 without Content-Range,
    # synthesize a proper 206 with a full-range Content-Range header.
    requested_range = ("Range" in headers) or (client_range is not None)
    total_len = upstream.headers.get("Content-Length")
    if requested_range and status == 200 and total_len and not passthrough.get("Content-Range"):
        try:
            total = int(total_len)
            passthrough["Content-Range"] = f"bytes 0-{total-1}/{total}"
            status = 206
        except Exception:
            pass  # leave as-is

    media = upstream.headers.get("Content-Type") or "audio/mpeg"

    if request.method == "HEAD":
        return Response(status_code=status, headers=passthrough, media_type=media)

    def gen():
        for chunk in upstream.iter_content(chunk_size=256 * 1024):
            if chunk:
                yield chunk

    return StreamingResponse(gen(), media_type=media, headers=passthrough, status_code=status)


# ---------------- UI: full player ----------------
@app.get("/api/user/{user_id}/play", response_class=HTMLResponse)
def player(user_id: str):
    lib = load_lib(user_id)
    rows = []
    for t in lib["tracks"].values():
        tid    = t.get("track_id")
        title  = t.get("title")  or "Unknown Title"
        artist = t.get("artist") or "Unknown Artist"
        album  = t.get("album")  or ""
        ok     = 1 if t.get("rel_path") else 0
        mark   = "✅" if ok else "—"
        rows.append(f"""
          <tr data-tid="{tid}" data-title="{title}" data-artist="{artist}" data-album="{album}" data-ok="{ok}">
            <td>{title}</td>
            <td>{artist}</td>
            <td>{album}</td>
            <td style="text-align:center">{mark}</td>
            <td><button onclick="playId('{tid}')">Play</button></td>
          </tr>
        """)
    rows_html = "\n".join(rows) or "<tr><td colspan='5'>No tracks yet.</td></tr>"

    html = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
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
  thead th{{position: sticky; top: 0; z-index: 1; background: #fafafa}}
  th .arrow{{opacity:.6;margin-left:6px}}
  main{{padding:16px 24px 40px}}
</style>
</head>
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
    <!-- Visible player -->
    <audio id="player" controls preload="none" style="width:100%"></audio>
    <!-- Hidden helper for crossfade -->
    <audio id="player2" preload="auto" style="display:none"></audio>
  </div>
  <div class="row" style="gap:16px;margin-top:6px">
    <label class="pill"><input type="checkbox" id="xfade"> Crossfade</label>
    <label class="pill" title="Seconds to crossfade">
      Fade (s): <input id="xfadeSecs" type="number" min="0" max="10" value="3" style="width:56px;margin-left:6px">
    </label>
  </div>
  <div style="margin-top:6px"><b>Now Playing:</b> <span id="now">—</span></div>
</div>

<main>
  <p>User: <b>{user_id}</b> · Library version: <b>{lib['version']}</b></p>
  <table>
    <thead>
      <tr>
        <th onclick="sortBy('title')"  style="cursor:pointer">Title  <span id="ar-title"  class="arrow"></span></th>
        <th onclick="sortBy('artist')" style="cursor:pointer">Artist <span id="ar-artist" class="arrow"></span></th>
        <th onclick="sortBy('album')"  style="cursor:pointer">Album  <span id="ar-album"  class="arrow"></span></th>
        <th>OK?</th>
        <th>Action</th>
      </tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>
</main>

<script>
// NOTE: This block is inside a Python f-string. JS braces are doubled {{ }}.

function libver() {{ return Math.floor(Date.now()/1000); }}
const userId = "{user_id}";
const API = (window.location.pathname.startsWith("/streamer/")) ? "/streamer/api" : "/api";

// ---------- table snapshot for sorting ----------
function snapshotRows() {{
  const rows = Array.from(document.querySelectorAll('tbody tr'));
  return rows.map(r => ({{
    tid:    r.getAttribute('data-tid')    || "",
    title:  r.getAttribute('data-title')  || "",
    artist: r.getAttribute('data-artist') || "",
    album:  r.getAttribute('data-album')  || "",
    ok:     Number(r.getAttribute('data-ok') || "0") === 1
  }}));
}}
let ROWS = snapshotRows();
let order = ROWS.map((_, i) => i);
let current = -1;
let lastTid = null;

// ---------- render helpers ----------
function rowHtml(t) {{
  const mark = t.ok ? "✅" : "—";
  return (
    "<tr data-tid=\\"" + t.tid + "\\" data-title=\\"" + t.title + "\\" data-artist=\\"" + t.artist + "\\" data-album=\\"" + t.album + "\\" data-ok=\\"" + (t.ok?1:0) + "\\">" +
      "<td>" + t.title  + "</td>" +
      "<td>" + t.artist + "</td>" +
      "<td>" + t.album  + "</td>" +
      "<td style=\\"text-align:center\\">" + mark + "</td>" +
      "<td><button onclick=\\"playId('" + t.tid + "')\\">Play</button></td>" +
    "</tr>"
  );
}}
function renderRows() {{
  const tbody = document.querySelector('tbody');
  tbody.innerHTML = order.map(i => rowHtml(ROWS[i])).join('');
  ROWS = snapshotRows();
  order = ROWS.map((_, i) => i);
  if (lastTid) {{
    const ri = rowIndexOfTid(lastTid);
    if (ri >= 0) current = ri;
  }}
}}

// ---------- sorting ----------
let sortKey = 'title';
let sortDir = 1;
function sortBy(k) {{
  if (sortKey === k) sortDir = -sortDir; else {{ sortKey = k; sortDir = 1; }}
  order.sort((i, j) => {{
    const a = (ROWS[i][sortKey] || "").toLowerCase();
    const b = (ROWS[j][sortKey] || "").toLowerCase();
    if (a < b) return -1 * sortDir;
    if (a > b) return  1 * sortDir;
    return 0;
  }});
  renderRows();
  updateSortArrows();
}}
function updateSortArrows() {{
  for (const k of ['title','artist','album']) {{
    const el = document.getElementById('ar-' + k);
    if (!el) continue;
    el.textContent = (k === sortKey) ? (sortDir > 0 ? '▲' : '▼') : '';
  }}
}}
updateSortArrows();

// ---------- helpers for rows/meta ----------
function trackIdAtRow(i) {{
  const row = document.querySelectorAll('tbody tr')[i];
  return row ? row.getAttribute('data-tid') : null;
}}
function rowIndexOfTid(tid) {{
  const rows = Array.from(document.querySelectorAll('tbody tr'));
  for (let i=0;i<rows.length;i++) if (rows[i].getAttribute('data-tid')===tid) return i;
  return -1;
}}
function metaFor(tid) {{
  const row = Array.from(document.querySelectorAll('tbody tr')).find(r => r.getAttribute('data-tid') === tid);
  if (!row) return {{title:tid, artist:"", album:""}};
  return {{
    title:  row.getAttribute('data-title')  || tid,
    artist: row.getAttribute('data-artist') || "",
    album:  row.getAttribute('data-album')  || ""
  }};
}}

// ---------- audio plumbing (with optional crossfade) ----------
// ---------- audio plumbing (with optional crossfade) ----------
const a1 = document.getElementById('player');   // visible
const a2 = document.getElementById('player2');  // hidden helper

// Web Audio (optional, lazy-initialized when Crossfade is enabled)
let AC = null, g1 = null, g2 = null, n1 = null, n2 = null;
async function ensureAudioGraph() {{
  // Resume AudioContext on any user gesture (click/touch/keydown)
  ['click','touchstart','keydown'].forEach(evt => {{
    window.addEventListener(evt, async () => {{
      if (AC && AC.state === 'suspended') {{
        try {{ await AC.resume(); }} catch {{}}
      }}
    }}, {{ once: false, passive: true }});
  }});

  if (AC) return true;
  try {{
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx || !a1 || !a2) return false;
    AC = new Ctx();
    n1 = AC.createMediaElementSource(a1);
    n2 = AC.createMediaElementSource(a2);
    g1 = AC.createGain();
    g2 = AC.createGain();
    g1.gain.value = 1.0;
    g2.gain.value = 0.0;
    n1.connect(g1).connect(AC.destination);
    n2.connect(g2).connect(AC.destination);
    return true;
  }} catch (e) {{
    console.warn("[xfade] graph init failed:", e);
    AC = null; g1 = g2 = n1 = n2 = null;
    return false;
  }}
}}


// Persist crossfade toggle and lazily build audio graph when enabled
document.addEventListener('DOMContentLoaded', () => {{
  const xcb = document.getElementById('xfade');
  if (!xcb) return;

  xcb.checked = localStorage.getItem('rt-xfade') === '1';

  xcb.addEventListener('change', async () => {{
    localStorage.setItem('rt-xfade', xcb.checked ? '1' : '0');
    if (xcb.checked) {{
      const ok = await ensureAudioGraph();
      if (ok) {{
        try {{ if (AC && AC.state === 'suspended') await AC.resume(); }} catch {{}}
      }}
    }}
  }});

  if (xcb.checked) {{
    xcb.dispatchEvent(new Event('change'));
  }}
}});

function setNowPlaying(tid) {{
  const m = metaFor(tid);
  const albumText = m.album ? " (" + m.album + ")" : "";
  document.getElementById('now').innerText = m.artist + " — " + m.title + albumText;
}}

function urlFor(tid) {{
  return API + "/relay/" + userId + "/" + tid + "?v=" + libver();
}}

// Visible element play (no WebAudio required)
function playId(tid) {{
  const url = urlFor(tid);
  a1.src = url;
  a1.load();
  a1.play().catch(() => {{}});

  // stop helper if it was in use
  if (a2) try {{ a2.pause(); }} catch (e) {{}}

  setNowPlaying(tid);
  const ri = rowIndexOfTid(tid);
  if (ri >= 0) current = ri;
  lastTid = tid;
}}

// Crossfade helper (uses WebAudio only if toggle is ON and graph is available)
async function xfadeTo(tid) {{
  const toggle = document.getElementById('xfade');
  const secsEl = document.getElementById('xfadeSecs');
  const secs = Math.max(0, Math.min(10, Number(secsEl ? secsEl.value : 0) || 0));

  // Fall back to simple play if crossfade is off, graph not ready, or invalid seconds
  if (!toggle || !toggle.checked || secs <= 0 || !a2) {{
    playId(tid);
    return;
  }}

  const ok = await ensureAudioGraph();
  if (!ok || !g1 || !g2) {{
    playId(tid);
    return;
  }}

  try {{ if (AC.state === 'suspended') await AC.resume(); }} catch (e) {{}}

  // Decide which element is currently active
  const active = (!a1.paused && a1.currentSrc) ? a1 : ((!a2.paused && a2.currentSrc) ? a2 : a1);
  const helper = (active === a1) ? a2 : a1;
  const gActive = (active === a1) ? g1 : g2;
  const gHelper = (helper === a1) ? g1 : g2;

  // Prepare helper with next track
  helper.src = urlFor(tid);
  try {{ helper.load(); }} catch (e) {{}}
  helper.currentTime = 0;

  try {{
    gHelper.gain.setValueAtTime(0, AC.currentTime);
    // Start helper; ignore autoplay/gesture errors (user already clicked)
    helper.play().catch(() => {{}}); 
  }} catch (e) {{
    // If anything goes wrong, just do a hard switch
    playId(tid);
    return;
  }}

  // Schedule crossfade
  const now = AC.currentTime;
  gActive.gain.cancelScheduledValues(now);
  gHelper.gain.cancelScheduledValues(now);
  gActive.gain.setValueAtTime(gActive.gain.value, now);
  gHelper.gain.setValueAtTime(gHelper.gain.value, now);
  gActive.gain.linearRampToValueAtTime(0.0, now + secs);
  gHelper.gain.linearRampToValueAtTime(1.0, now + secs);

  // After fade completes, make helper the visible element source (keep UI consistent)
  setTimeout(() => {{
    try {{ active.pause(); }} catch (e) {{}}
    if (helper !== a1) {{
      a1.src = helper.src;
      a1.load();
      a1.currentTime = helper.currentTime;
      a1.play().catch(() => {{}});

      // Reset gains for next transition
      if (g1 && g2) {{ g1.gain.value = 1.0; g2.gain.value = 0.0; }}
      try {{ helper.pause(); }} catch (e) {{}}
    }} else {{
      if (g1 && g2) {{ g1.gain.value = 1.0; g2.gain.value = 0.0; }}
      try {{ a2.pause(); }} catch (e) {{}}
    }}
  }}, Math.ceil((secs + 0.05) * 1000));

  setNowPlaying(tid);
  const ri = rowIndexOfTid(tid);
  if (ri >= 0) current = ri;
  lastTid = tid;
}}


// ---------- queue navigation ----------
// ---------- shuffle bag (no repeats until exhausted) ----------
const SHUFFLE_KEY = "rt_shufflebag_v1";

function _fyShuffle(a) {{
  for (let i = a.length - 1; i > 0; i--) {{
    const j = Math.floor(Math.random() * (i + 1));
    const tmp = a[i]; a[i] = a[j]; a[j] = tmp;
  }}
  return a;
}}

function _loadBag() {{
  try {{
    const raw = localStorage.getItem(SHUFFLE_KEY);
    if (!raw) return null;
    const j = JSON.parse(raw);
    if (j && Array.isArray(j.bag)) return j.bag;
  }} catch (e) {{}}
  return null;
}}

function _saveBag(bag) {{
  try {{ localStorage.setItem(SHUFFLE_KEY, JSON.stringify({{ bag: bag }})); }} catch (e) {{}}
}}

function _allTids() {{
  // current table order (after sorting) is what user expects
  const tids = [];
  const trs = document.querySelectorAll('tbody tr');
  for (let i = 0; i < trs.length; i++) {{
    const tid = trs[i].getAttribute('data-tid');
    if (tid) tids.push(tid);
  }}
  return tids;
}}

function _freshBag() {{
  const tids = _allTids();
  return _fyShuffle(tids.slice());
}}

function _nextFromBag() {{
  let bag = _loadBag();
  const tids = _allTids();
  const all = new Set(tids);

  // init / empty
  if (!bag || bag.length === 0) bag = _freshBag();

  // drop tids that no longer exist (library changed)
  bag = bag.filter(t => all.has(t));

  // add new tids not present in bag (library grew)
  const inBag = new Set(bag);
  for (let i = 0; i < tids.length; i++) {{
    const t = tids[i];
    if (!inBag.has(t)) bag.push(t);
  }}

  // still empty? rebuild
  if (bag.length === 0) bag = _freshBag();

  // pop next
  const nextTid = bag.shift();
  _saveBag(bag);

  // map tid -> current row index
  const idx = rowIndexOfTid(nextTid);
  return idx >= 0 ? idx : 0;
}}

function pickNextIndex() {{
  const shuffle = document.getElementById('shuffle').checked;
  if (ROWS.length === 0) return -1;

  if (shuffle) {{
    return _nextFromBag();
  }}

  // sequential mode
  return (current + 1) % ROWS.length;
}}
function pickPrevIndex() {{
  if (ROWS.length === 0) return -1;
  return (current - 1 + ROWS.length) % ROWS.length;
}}
function playNext() {{
  if (ROWS.length === 0) {{ alert("No tracks found."); return; }}
  const i = pickNextIndex(); if (i < 0) return;
  current = i;
  const tid = trackIdAtRow(current); if (!tid) return;
  const useX = document.getElementById('xfade') && document.getElementById('xfade').checked;
  useX ? xfadeTo(tid) : playId(tid);
}}
function playPrev() {{
  if (ROWS.length === 0) {{ alert("No tracks found."); return; }}
  const i = pickPrevIndex(); if (i < 0) return;
  current = i;
  const tid = trackIdAtRow(current); if (!tid) return;
  const useX = document.getElementById('xfade') && document.getElementById('xfade').checked;
  useX ? xfadeTo(tid) : playId(tid);
}}

// ---------- make native ▶️ start playback even with no src ----------
function kickIfEmpty() {{
  if (!a1.currentSrc || a1.currentSrc === "" || a1.currentSrc === window.location.href) {{
    if (current >= 0) {{
      const tid = trackIdAtRow(current);
      if (tid) {{
        const useX = document.getElementById('xfade') && document.getElementById('xfade').checked;
        useX ? xfadeTo(tid) : playId(tid);
      }}
    }} else {{
      playNext();
    }}
    setTimeout(() => a1.play().catch(() => {{}}), 0);
  }}
}}
a1.addEventListener('play',  kickIfEmpty);
a1.addEventListener('click', kickIfEmpty);
a1.addEventListener('touchstart', kickIfEmpty);

// Helpful error surfacing if a stream fails to load
a1.addEventListener('error', () => {{
  const src = a1.currentSrc || "(no source)";
  alert("Audio error. Could not load: " + src);
}});

// ---------- autoplay next on end ----------
a1.addEventListener('ended', () => {{
  const autoplay = document.getElementById('autoplay').checked;
  if (autoplay) playNext();
}});

// ---------- Agent status dot ----------
async function refreshStatus() {{
  try {{
    const res = await fetch(API + "/agent/" + userId + "/status");
    const j = await res.json();
    const dot = document.getElementById('stDot');
    if (j.online) dot.classList.add('ok'); else dot.classList.remove('ok');
  }} catch (e) {{
    document.getElementById('stDot').classList.remove('ok');
  }}
}}
refreshStatus();
setInterval(refreshStatus, 10000);

// ---------- Clear library ----------
async function confirmClear() {{
  if (!confirm("Are you sure you want to CLEAR your library on the server?")) return;
  try {{
    const res = await fetch(API + "/library/" + userId + "/clear", {{ method: "POST" }});
    if (!res.ok) throw new Error(await res.text());
    location.reload();
  }} catch (e) {{
    alert("Clear failed: " + e.message);
  }}
}}
</script>

</body>
</html>"""
    return HTMLResponse(html, status_code=200)

# ---------------- UI: tiny radio page ----------------
@app.get("/api/radio/{user_id}", response_class=HTMLResponse)
def radio_page(user_id: str):
    lib = load_lib(user_id)
    tracks = []
    for t in lib["tracks"].values():
        tracks.append({
            "track_id": t.get("track_id"),
            "title": t.get("title") or "Unknown Title",
            "artist": t.get("artist") or "Unknown Artist",
            "album":  t.get("album")  or "",
        })
    tracks_json = json.dumps(tracks)

    html = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{user_id} — Radio</title>
<style>
  body{{font-family:system-ui,Segoe UI,Roboto,Arial;margin:0;padding:20px}}
  .bar{{max-width:900px;margin:0 auto}}
  .dot{{width:10px;height:10px;border-radius:50%;display:inline-block;vertical-align:middle;margin-right:6px;background:#bbb}}
  .dot.ok{{background:#16a34a}}
  .row{{display:flex;align-items:center;gap:12px;flex-wrap:wrap}}
  .now{{margin-top:10px}}
  .hint{{color:#666;font-size:14px;margin-top:6px}}
  .btn{{padding:6px 10px;border:1px solid #ddd;border-radius:6px;background:#fafafa;cursor:pointer}}
  .btn:hover{{filter:brightness(1.03)}}
</style>
</head>
<body>
<div class="bar">
  <div class="row">
    <span id="stDot" class="dot"></span>
    <strong>{user_id} — Radio</strong>
    <button class="btn" id="btnNext" type="button">▶️ Next</button>
  </div>
  <div class="row" style="margin-top:8px">
    <!-- IMPORTANT: playsinline + preload="auto" -->
    <audio id="player" controls preload="auto" playsinline style="width:100%"></audio>
  </div>
  <div class="now"><b>Now Playing:</b> <span id="now">—</span></div>
  <div class="hint">Tip: press ▶️ or “Next”. The dot is green when your agent is online.</div>
</div>

<script>
// NOTE: Inside a Python f-string — ALL braces are doubled {{ }}.

function libver() {{ return Math.floor(Date.now()/1000); }}
const userId = "{user_id}";
const TRACKS = {tracks_json};

// Robust API base: if path contains /streamer/ anywhere, use /streamer/api
const API = (window.location.pathname.indexOf("/streamer/") !== -1) ? "/streamer/api" : "/api";

// Small logger for visibility
function logAudioState(prefix, el) {{
  const states = ["HAVE_NOTHING","HAVE_METADATA","HAVE_CURRENT_DATA","HAVE_FUTURE_DATA","HAVE_ENOUGH_DATA"];
  console.log("[audio] " + prefix, {{
    src: el.currentSrc,
    readyState: states[el.readyState] || el.readyState,
    paused: el.paused, ended: el.ended, networkState: el.networkState,
    error: el.error && {{code: el.error.code, msg: el.error.message}}
  }});
}}

// Pick a random track
let current = -1;

function pickNextIndex() {{
  if (!TRACKS || TRACKS.length === 0) return -1;

  // simple random
  let i = Math.floor(Math.random() * TRACKS.length);

  // avoid immediate repeat when possible
  if (TRACKS.length > 1 && i === current) i = (i + 1) % TRACKS.length;

  return i;
}}

function urlFor(tid) {{
  return API + "/relay/" + userId + "/" + tid + "?v=" + libver();
}}

// Use a <source type="audio/mpeg"> to help stricter browsers
function setAudioSrc(audio, url) {{
  audio.removeAttribute('src'); // avoids weird state in some browsers
  while (audio.firstChild) audio.removeChild(audio.firstChild);
  audio.src = url;              // direct src, no <source> type
}}

function playIndex(i) {{
  const m = TRACKS[i];
  if (!m) return;

  const audio = document.getElementById('player');
  const url = urlFor(m.track_id);

  setAudioSrc(audio, url);
  audio.load();
  audio.play().catch(() => {{}});

  const albumText = m.album ? " (" + m.album + ")" : "";
  document.getElementById('now').innerText =
    m.artist + " — " + m.title + albumText;
}}

function playNext() {{
  const i = pickNextIndex();
  if (i < 0) {{
    alert("No tracks found.");
    return;
  }}
  current = i;
  playIndex(current);
}}


// Wire the explicit Next button (gives guaranteed user gesture)
document.getElementById('btnNext').addEventListener('click', () => playNext());

// Also allow the native ▶️ to kick the first track
const audio = document.getElementById('player');
function firstPlayKick() {{
  if (!audio.currentSrc || audio.currentSrc === "" || audio.currentSrc === window.location.href) {{
    playNext();
    setTimeout(() => audio.play().catch(() => {{}}), 0);
  }}
}}
audio.addEventListener('play', firstPlayKick);
audio.addEventListener('click', firstPlayKick);
audio.addEventListener('touchstart', firstPlayKick);

// Helpful events
audio.addEventListener('loadedmetadata', () => logAudioState('loadedmetadata', audio));
audio.addEventListener('canplay',        () => logAudioState('canplay', audio));
audio.addEventListener('canplaythrough', () => logAudioState('canplaythrough', audio));
audio.addEventListener('playing',        () => logAudioState('playing', audio));
audio.addEventListener('stalled',        () => logAudioState('stalled', audio));
audio.addEventListener('suspend',        () => logAudioState('suspend', audio));
audio.addEventListener('pause',          () => logAudioState('pause', audio));
audio.addEventListener('error', () => {{
  const src = audio.currentSrc || "(no source)";
  console.error("[audio] error", audio.error, "src=", src);
  alert("Audio error. Could not load: " + src);
}});
audio.addEventListener('ended', () => playNext());

// Agent status
async function refreshStatus() {{
  try {{
    const res = await fetch(API + "/agent/" + userId + "/status");
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
</body>
</html>"""
    return HTMLResponse(html, status_code=200)
