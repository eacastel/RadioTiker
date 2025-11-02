from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
import json
from ..storage import load_lib

router = APIRouter(prefix="/api", tags=["ui"])

# ---------------- UI: full player ----------------
@router.get("/user/{user_id}/play", response_class=HTMLResponse)
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

// ---------- table model ----------
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
let sortKey = 'title', sortDir = 1;
function sortBy(k) {{
  if (sortKey === k) sortDir = -sortDir; else {{ sortKey = k; sortDir = 1; }}
  order.sort((i, j) => {{
    const a = (ROWS[i][sortKey] || "").toLowerCase();
    const b = (ROWS[j][sortKey] || "").toLowerCase();
    if (a < b) return -1 * sortDir;
    if (a > b) return  1 * sortDir;
    return 0;
  }});
  renderRows(); updateSortArrows();
}}
function updateSortArrows() {{
  for (const k of ['title','artist','album']) {{
    const el = document.getElementById('ar-' + k);
    if (!el) continue;
    el.textContent = (k === sortKey) ? (sortDir > 0 ? '▲' : '▼') : '';
  }}
}}
updateSortArrows();

function trackIdAtRow(i) {{
  const row = document.querySelectorAll('tbody tr')[i];
  return row ? row.getAttribute('data-tid') : null;
}}

async function confirmClear() {{
  if (!window.confirm("Are you sure you want to CLEAR your library on the server?")) return;
  try {{
    const res = await fetch(API + "/library/" + userId + "/clear", {{ method: "POST" }});
    if (!res.ok) throw new Error(await res.text());
    // Reload after successful clear so the table reflects empty state
    location.reload();
  }} catch (e) {{
    alert("Clear failed: " + (e?.message || e));
  }}
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

// ---------- audio plumbing ----------
const a1 = document.getElementById('player');   // visible + UI
const a2 = document.getElementById('player2');  // hidden helper

// Web Audio graph (built only if crossfade is ON)
let AC = null, g1 = null, g2 = null, n1 = null, n2 = null;
let ACTIVE = 1; // 1 = a1 drives audio, 2 = a2

// Wait for 'canplay' (or short timeout) to avoid the 1s “stall then resume”
function waitForCanPlay(el, timeoutMs = 8000) {{
  return new Promise((resolve, reject) => {{
    let done = false;
    const ok = () => {{ if (!done) {{ done = true; cleanup(); resolve(); }} }};
    const bad = (e) => {{ if (!done) {{ done = true; cleanup(); reject(e||new Error("audio error")); }} }};
    const t = setTimeout(() => bad(new Error("canplay timeout")), timeoutMs);
    function cleanup() {{
      clearTimeout(t);
      el.removeEventListener('canplay', ok);
      el.removeEventListener('error', bad);
      el.removeEventListener('stalled', bad);
      el.removeEventListener('abort', bad);
    }}
    el.addEventListener('canplay', ok, {{ once: true }});
    el.addEventListener('error',  bad, {{ once: true }});
    el.addEventListener('stalled',bad, {{ once: true }});
    el.addEventListener('abort',  bad, {{ once: true }});
    try {{ if (el.readyState >= HTMLMediaElement.HAVE_FUTURE_DATA) ok(); }} catch {{}}
  }});
}}

async function ensureAudioGraph() {{
  if (AC) return true;
  try {{
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx || !a1 || !a2) return false;
    AC = new Ctx();
    n1 = AC.createMediaElementSource(a1);
    n2 = AC.createMediaElementSource(a2);
    g1 = AC.createGain(); g2 = AC.createGain();
    g1.gain.value = 1.0; g2.gain.value = 0.0;
    n1.connect(g1).connect(AC.destination);
    n2.connect(g2).connect(AC.destination);
    ['click','touchstart','keydown'].forEach(evt => {{
      window.addEventListener(evt, async () => {{
        if (AC && AC.state === 'suspended') {{ try {{ await AC.resume(); }} catch {{}} }}
      }});
    }});
    return true;
  }} catch (e) {{
    console.warn("[xfade] graph init failed:", e);
    AC = null; g1 = g2 = n1 = n2 = null;
    return false;
  }}
}}

// Persist crossfade toggle and lazily build graph
document.addEventListener('DOMContentLoaded', () => {{
  const xcb = document.getElementById('xfade');
  if (!xcb) return;
    xcb.checked = (localStorage.getItem('rt-xfade') === '1') && !/iPhone|iPad|Android/i.test(navigator.userAgent);
  xcb.addEventListener('change', async () => {{
    localStorage.setItem('rt-xfade', xcb.checked ? '1' : '0');
    if (xcb.checked) {{
      const ok = await ensureAudioGraph();
      if (ok && AC && AC.state === 'suspended') {{ try {{ await AC.resume(); }} catch {{}} }}
    }}
  }});
  if (xcb.checked) xcb.dispatchEvent(new Event('change'));
}});

function setNowPlaying(tid) {{
  const m = metaFor(tid);
  const albumText = m.album ? " (" + m.album + ")" : "";
  document.getElementById('now').innerText = m.artist + " — " + m.title + albumText;
}}
function urlFor(tid) {{ return API + "/relay/" + userId + "/" + tid + "?v=" + libver(); }}

// --- plain play (always routes playback back to a1) ---
async function playId(tid) {{
  const url = urlFor(tid);
  a1.src = url; a1.load();
  try {{ await waitForCanPlay(a1, 8000); }} catch {{}}
  try {{ await a1.play(); }} catch {{}}
  try {{ a2.pause(); }} catch {{}}
  ACTIVE = 1;
  setNowPlaying(tid);
  const ri = rowIndexOfTid(tid); if (ri >= 0) current = ri;
  lastTid = tid;
}}

// --- crossfade + *handoff back to a1* after the fade ---
async function xfadeTo(tid) {{
  const toggle = document.getElementById('xfade');
  const secsEl = document.getElementById('xfadeSecs');
  const secs = Math.max(0, Math.min(10, Number(secsEl ? secsEl.value : 0) || 0));
  if (!toggle || !toggle.checked || secs <= 0) {{ await playId(tid); return; }}

  const ok = await ensureAudioGraph();
  if (!ok || !g1 || !g2) {{ await playId(tid); return; }}
  try {{ if (AC.state === 'suspended') await AC.resume(); }} catch {{}}

  const active = (ACTIVE === 1 ? a1 : a2);
  const helper = (ACTIVE === 1 ? a2 : a1);
  const gActive = (ACTIVE === 1 ? g1 : g2);
  const gHelper = (ACTIVE === 1 ? g2 : g1);

  // 1) Prepare helper and prebuffer
  const url = urlFor(tid);
  helper.src = url; helper.load();
  try {{ await waitForCanPlay(helper, 8000); }} catch (e) {{ console.warn("[xfade] helper timeout:", e); await playId(tid); return; }}
  try {{ await helper.play(); }} catch {{ await playId(tid); return; }}

  // 2) Run gain ramps
  const now = AC.currentTime;
  gActive.gain.cancelScheduledValues(now);
  gHelper.gain.cancelScheduledValues(now);
  gActive.gain.setValueAtTime(gActive.gain.value, now);
  gHelper.gain.setValueAtTime(gHelper.gain.value, now);
  gActive.gain.linearRampToValueAtTime(0.0, now + secs);
  gHelper.gain.linearRampToValueAtTime(1.0, now + secs);

  setNowPlaying(tid);
  const ri = rowIndexOfTid(tid); if (ri >= 0) current = ri;
  lastTid = tid;

  // 3) After fade, HANDOFF to a1 so the UI bar controls the real audio.
  setTimeout(async () => {{
    try {{
      // If helper already IS a1, we're done
      if (helper === a1) {{
        ACTIVE = 1;
        if (g1 && g2) {{ g1.gain.value = 1.0; g2.gain.value = 0.0; }}
        try {{ active.pause(); }} catch {{}}
        return;
      }}

      // Sync a1 to helper without audible gap
      const tcur = (helper.currentTime || 0);
      a1.src = helper.src;
      a1.load();
      try {{ a1.currentTime = tcur; }} catch {{}}
      try {{ await waitForCanPlay(a1, 8000); }} catch {{}}
      try {{ await a1.play(); }} catch {{ }}

      // Now stop helper and normalize gains
      try {{ helper.pause(); }} catch {{}}
      ACTIVE = 1;
      if (g1 && g2) {{ g1.gain.value = 1.0; g2.gain.value = 0.0; }}
    }} catch (e) {{
      console.warn("[xfade] handoff failed, staying on helper:", e);
      ACTIVE = (helper === a1) ? 1 : 2;
    }}
  }}, Math.ceil((secs + 0.05) * 1000));
}}

// ---------- queue navigation ----------
function pickNextIndex() {{
  const shuffle = document.getElementById('shuffle').checked;
  if (ROWS.length === 0) return -1;
  if (shuffle) return Math.floor(Math.random() * ROWS.length);
  // sequential
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

// Ensure native ▶️ kicks off a valid source and respects the crossfade setting
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

// Helpful surfacing
a1.addEventListener('error', () => {{
  const src = a1.currentSrc || "(no source)";
  alert("Audio error. Could not load: " + src);
}});

// Autoplay next when finished
a1.addEventListener('ended', () => {{
  const autoplay = document.getElementById('autoplay').checked;
  if (autoplay) playNext();
}});

// Agent status dot
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

// Init arrows once more after render
updateSortArrows();
</script>


</body>
</html>"""
    return HTMLResponse(html, status_code=200)

# ---------------- UI: tiny radio page ----------------
@router.get("/radio/{user_id}", response_class=HTMLResponse)
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
  body{{{{font-family:system-ui,Segoe UI,Roboto,Arial;margin:0;padding:20px}}}}
  .bar{{{{max-width:900px;margin:0 auto}}}}
  .dot{{{{width:10px;height:10px;border-radius:50%;display:inline-block;vertical-align:middle;margin-right:6px;background:#bbb}}}}
  .dot.ok{{{{background:#16a34a}}}}
  .row{{{{display:flex;align-items:center;gap:12px;flex-wrap:wrap}}}}
  .now{{{{margin-top:10px}}}}
  .hint{{{{color:#666;font-size:14px;margin-top:6px}}}}
  .btn{{{{padding:6px 10px;border:1px solid #ddd;border-radius:6px;background:#fafafa;cursor:pointer}}}}
  .btn:hover{{{{filter:brightness(1.03)}}}}
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
// NOTE: Inside a Python f-string — ALL braces are doubled {{{{ }}}}.

function libver() {{{{ return Math.floor(Date.now()/1000); }}}}
const userId = "{user_id}";
const TRACKS = {tracks_json};

// Robust API base: if path contains /streamer/ anywhere, use /streamer/api
const API = (window.location.pathname.indexOf("/streamer/") !== -1) ? "/streamer/api" : "/api";

// Small logger for visibility
function logAudioState(prefix, el) {{{{
  const states = ["HAVE_NOTHING","HAVE_METADATA","HAVE_CURRENT_DATA","HAVE_FUTURE_DATA","HAVE_ENOUGH_DATA"];
  console.log(`[audio] ${{{{prefix}}}}`, {{{{
    src: el.currentSrc,
    readyState: states[el.readyState] || el.readyState,
    paused: el.paused, ended: el.ended, networkState: el.networkState,
    error: el.error && {{{{code: el.error.code, msg: el.error.message}}}}
  }}}});
}}}}

// Pick a random track
function pickIndex() {{{{
  if (TRACKS.length === 0) return -1;
  return Math.floor(Math.random() * TRACKS.length);
}}}}

function urlFor(tid) {{{{
  return API + "/relay/" + userId + "/" + tid + "?v=" + libver();
}}}}

// Use a <source type="audio/mpeg"> to help stricter browsers
function setAudioSrc(audio, url) {{{{
  while (audio.firstChild) audio.removeChild(audio.firstChild);
  const src = document.createElement('source');
  src.id = "src";
  src.src = url;
  src.type = "audio/mpeg";
  audio.appendChild(src);
}}}}

function playIndex(i) {{{{
  const m = TRACKS[i]; if (!m) return;
  const audio = document.getElementById('player');
  const url = urlFor(m.track_id);
  setAudioSrc(audio, url);
  audio.load();
  audio.play().then(() => logAudioState('playing-started', audio))
              .catch(() => logAudioState('play-catch', audio));
  const albumText = m.album ? " (" + m.album + ")" : "";
  document.getElementById('now').innerText = m.artist + " — " + m.title + albumText;
}}}}
let current = -1;
function playNext() {{{{ current = pickIndex(); if (current >= 0) playIndex(current); }}}}

// Wire the explicit Next button (gives guaranteed user gesture)
document.getElementById('btnNext').addEventListener('click', () => playNext());

// Also allow the native ▶️ to kick the first track
const audio = document.getElementById('player');
function firstPlayKick() {{{{
  if (!audio.currentSrc || audio.currentSrc === "" || audio.currentSrc === window.location.href) {{{{
    playNext();
    setTimeout(() => audio.play().catch(() => {{{{}}}}), 0);
  }}}}
}}}}
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
audio.addEventListener('error', () => {{{{
  const src = audio.currentSrc || "(no source)";
  console.error("[audio] error", audio.error, "src=", src);
  alert("Audio error. Could not load: " + src);
}}}});
audio.addEventListener('ended', () => playNext());

// Agent status
async function refreshStatus() {{{{
  try {{{{
    const res = await fetch(API + "/agent/" + userId + "/status");
    const j = await res.json();
    const dot = document.getElementById('stDot');
    if (j.online) dot.classList.add('ok'); else dot.classList.remove('ok');
  }}}} catch (e) {{{{
    document.getElementById('stDot').classList.remove('ok');
  }}}}
}}}}
refreshStatus();
setInterval(refreshStatus, 10000);
</script>
</body>
</html>"""
    return HTMLResponse(html, status_code=200)
