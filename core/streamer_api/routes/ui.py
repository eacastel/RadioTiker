from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
import json
from ..storage import load_lib

router = APIRouter(prefix="/api", tags=["ui"])

LEGACY_AUDIO_EXTS = {
    ".flac", ".wav", ".aif", ".aiff", ".ape", ".alac", ".wv",
    ".ogg", ".opus", ".wma", ".dsf", ".dff",
}


def _track_needs_mp3_proxy(track: dict) -> bool:
    rel = str(track.get("rel_path") or track.get("path") or "")
    if not rel:
        return False
    leaf = rel.rsplit("/", 1)[-1].split("?", 1)[0]
    dot = leaf.rfind(".")
    if dot < 0:
        return False
    return leaf[dot:].lower() in LEGACY_AUDIO_EXTS

# ---------------- UI: full player ----------------
@router.get("/user/{user_id}/play", response_class=HTMLResponse)
def player(user_id: str):
    lib = load_lib(user_id)
    tracks = []
    for t in lib["tracks"].values():
        tracks.append({
            "track_id": t.get("track_id"),
            "title": t.get("title") or "Unknown Title",
            "artist": t.get("artist") or "Unknown Artist",
            "album": t.get("album") or "Unknown Album",
            "artwork_url": t.get("artwork_url") or ((t.get("artwork_urls") or [""])[0] or ""),
            "artist_image_url": ((t.get("artist_image_urls") or [""])[0] or ""),
            "artist_bio": t.get("artist_bio") or "",
            "album_bio": t.get("album_bio") or "",
            "genre": t.get("genre") or "",
            "year": t.get("year") or "",
            "format_family": t.get("format_family") or "",
            "metadata_quality": t.get("metadata_quality") or 0,
            "ok": 1 if t.get("rel_path") else 0,
            "duration_sec": t.get("duration_sec") or 0,
            "force_mp3": _track_needs_mp3_proxy(t),
        })
    tracks_json = json.dumps(tracks)

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
  .album-card{{border:1px solid #e5e7eb;border-radius:10px;margin-top:12px;overflow:hidden}}
  .album-head{{display:flex;gap:10px;align-items:center;justify-content:space-between;padding:10px 12px;background:#fafafa;border-bottom:1px solid #eee}}
  .album-main{{display:flex;gap:10px;align-items:center;min-width:0}}
  .album-cover{{width:52px;height:52px;border-radius:6px;object-fit:cover;border:1px solid #ddd;background:#f3f4f6;flex:0 0 52px}}
  .album-title{{font-weight:600}}
  .album-meta{{font-size:13px;color:#666}}
  .album-actions{{display:flex;align-items:center;gap:8px;flex-wrap:wrap}}
  .album-tracks{{padding:8px 10px}}
  .track-row{{display:grid;grid-template-columns:1fr 170px 72px 140px 70px;gap:8px;align-items:center;padding:6px 4px;border-bottom:1px solid #f1f1f1}}
  .track-row:last-child{{border-bottom:none}}
  .track-title{{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
  .track-artist{{font-size:13px;color:#666;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
  .muted{{color:#777;font-size:13px}}
  .link-btn{{background:none;border:none;color:#2563eb;cursor:pointer;padding:0 2px;text-decoration:underline}}
  .link-btn[disabled]{{color:#9ca3af;cursor:default;text-decoration:none}}
  .seek-row{{display:flex;align-items:center;gap:8px;margin-top:8px;flex-wrap:wrap}}
  .seek-row input[type="range"]{{flex:1;min-width:180px}}
  .seek-time{{font-variant-numeric:tabular-nums;min-width:84px;text-align:right}}
  .seek-note{{font-size:12px;color:#777}}
  .now-meta{{margin-top:10px;padding:10px;border:1px solid #e5e7eb;border-radius:10px;background:#fafafa}}
  .now-meta-main{{display:flex;gap:12px;align-items:flex-start}}
  .now-meta-img{{width:72px;height:72px;border-radius:8px;object-fit:cover;border:1px solid #ddd;background:#f3f4f6;flex:0 0 72px}}
  .now-meta-head{{font-size:13px;color:#666;margin-bottom:6px}}
  .now-meta-bio-title{{font-size:12px;font-weight:700;color:#555;text-transform:uppercase;letter-spacing:.02em}}
  .now-meta-bio{{font-size:13px;line-height:1.35;color:#333;white-space:pre-wrap;margin-top:2px}}
  .now-meta-grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:8px}}
  .status-banner{{display:none;margin-top:8px;padding:8px 10px;border-radius:8px;border:1px solid #d1d5db;background:#f9fafb;font-size:13px}}
  .status-banner.show{{display:block}}
  .status-banner.error{{border-color:#fca5a5;background:#fef2f2;color:#991b1b}}
  .status-banner.warn{{border-color:#fcd34d;background:#fffbeb;color:#92400e}}
  .status-banner.info{{border-color:#93c5fd;background:#eff6ff;color:#1e3a8a}}
  .filters{{margin-top:10px;padding:10px;border:1px solid #ececec;border-radius:8px;background:#fcfcfc}}
  .filter-grid{{display:grid;grid-template-columns:2fr 1fr 1fr 1fr 1fr auto;gap:8px;align-items:center}}
  .filter-grid input,.filter-grid select{{padding:7px 8px;border:1px solid #d5d5d5;border-radius:6px;background:#fff}}
  .summary{{font-size:12px;color:#666;margin-top:8px}}
  .playlist-tools{{margin-top:10px;padding:10px;border:1px solid #ececec;border-radius:8px;background:#fcfcfc}}
  @media (max-width: 760px) {{
    .track-row{{grid-template-columns:1fr 60px 128px 66px}}
    .track-artist{{display:none}}
    .filter-grid{{grid-template-columns:1fr 1fr}}
    .now-meta-grid{{grid-template-columns:1fr}}
  }}
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
    <label class="pill" title="Keep screen awake during playback (where supported)">
      <input type="checkbox" id="keepAwake"> Keep awake
    </label>
  </div>
  <div style="margin-top:6px">
    <b>Now Playing:</b> <span id="now">—</span>
    · <button id="nowAlbumBtn" class="link-btn" type="button" onclick="goToNowPlayingAlbum()" disabled>Go to album</button>
  </div>
  <div class="now-meta" id="nowMetaPanel">
    <div class="now-meta-main">
      <img id="nowMetaImg" class="now-meta-img" src="" alt="Now playing artwork" loading="lazy" style="display:none">
      <div style="min-width:0;flex:1">
        <div id="nowMetaHead" class="now-meta-head">No track selected.</div>
      </div>
    </div>
    <div class="now-meta-grid">
      <div>
        <div class="now-meta-bio-title">Artist Story</div>
        <div id="nowArtistBio" class="now-meta-bio">No artist story yet.</div>
      </div>
      <div>
        <div class="now-meta-bio-title">Album Story</div>
        <div id="nowAlbumBio" class="now-meta-bio">No album story yet.</div>
      </div>
    </div>
  </div>
  <div class="seek-row">
    <button class="btn" type="button" onclick="skipBy(-15)">-15s</button>
    <input id="seekBar" type="range" min="0" max="100" value="0" step="1">
    <button class="btn" type="button" onclick="skipBy(15)">+15s</button>
    <span id="seekTime" class="seek-time">0:00 / 0:00</span>
  </div>
  <div id="seekNote" class="seek-note"></div>
  <div id="statusBanner" class="status-banner"></div>
</div>

<main>
  <p>User: <b>{user_id}</b> · Library version: <b>{lib['version']}</b></p>
  <div class="row">
    <b>Albums:</b>
    <button class="btn" type="button" onclick="setAllAlbums(true)">Select All</button>
    <button class="btn" type="button" onclick="setAllAlbums(false)">Unselect All</button>
    <span class="muted">Shuffle uses only selected albums.</span>
  </div>
  <section class="filters">
    <div class="filter-grid">
      <input id="fltQuery" type="text" placeholder="Search title, artist, album, genre">
      <select id="fltArtist"><option value="">All artists</option></select>
      <select id="fltGenre"><option value="">All genres</option></select>
      <select id="fltFormat">
        <option value="">All formats</option>
        <option value="lossless">Lossless</option>
        <option value="lossy">Lossy</option>
      </select>
      <select id="fltYear"><option value="">All years</option></select>
      <button class="btn" type="button" onclick="resetFilters()">Reset</button>
    </div>
    <div id="libSummary" class="summary"></div>
  </section>
  <section class="playlist-tools">
    <div class="row">
      <b>Playlists:</b>
      <input id="plName" type="text" placeholder="New playlist name" style="min-width:220px;padding:7px 8px;border:1px solid #d5d5d5;border-radius:6px">
      <button class="btn" type="button" onclick="createPlaylistFromInput()">Create</button>
      <select id="plSelect" style="min-width:220px;padding:7px 8px;border:1px solid #d5d5d5;border-radius:6px">
        <option value="">Select playlist</option>
      </select>
      <button class="btn" type="button" onclick="refreshPlaylists()">Refresh</button>
      <button class="btn" type="button" onclick="addCurrentTrackToPlaylist()">Add current track</button>
      <button class="btn" type="button" onclick="addFilteredToPlaylist()">Add filtered tracks</button>
      <button class="btn danger" type="button" onclick="clearSelectedPlaylist()">Clear playlist</button>
      <button class="btn" type="button" onclick="showSelectedPlaylist()">Show</button>
    </div>
    <div id="plSummary" class="summary"></div>
    <div class="summary">How to add tracks: 1) Create/select playlist, 2) filter library or start a track, 3) click "Add filtered tracks" or "Add current track".</div>
  </section>
  <div id="albumList" style="margin-top:8px"></div>
</main>

<script>
// NOTE: This block is inside a Python f-string. JS braces are doubled {{ }}.

function libver() {{ return Math.floor(Date.now()/1000); }}
const userId = "{user_id}";
const API = (window.location.pathname.startsWith("/streamer/")) ? "/streamer/api" : "/api";
const TRACKS = {tracks_json};
const TRACK_BY_ID = Object.fromEntries(TRACKS.map(t => [t.track_id, t]));
const ALBUM_STATE_KEY = "rt-album-enabled-" + userId;
const ALBUM_OPEN_KEY = "rt-album-open-" + userId;
const TRACK_STATE_KEY = "rt-track-enabled-" + userId;
const FILTER_STATE_KEY = "rt-library-filter-" + userId;
const KEEP_AWAKE_KEY = "rt-keep-awake-" + userId;
const MOBILE_UA = /iPhone|iPad|iPod|Android|Mobile/i.test(navigator.userAgent || "");
const CROSSFADE_ENABLED = !MOBILE_UA && !!(window.AudioContext || window.webkitAudioContext);
let currentTid = null;
let lastTid = null;
let currentTrackDurationSec = 0;
let currentStartOffsetSec = 0;
let seekDragging = false;
let wakeLock = null;
let recoveryInFlight = false;
let recoveryWindowStartMs = 0;
let recoveryCountInWindow = 0;
let lastProgressWallMs = 0;
let lastProgressPlaybackSec = 0;
let playHistory = [];
let historyIndex = -1;
let prefetchedTrackIds = new Set();
let prefetchInFlight = new Set();
function canPrefetch() {{
  if (MOBILE_UA) return false;
  const c = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
  if (!c) return true;
  if (c.saveData) return false;
  const et = String(c.effectiveType || "").toLowerCase();
  if (et.includes("2g") || et.includes("3g")) return false;
  return true;
}}

let statusTimer = null;
function showStatus(message, level = "info", timeoutMs = 4500) {{
  const el = document.getElementById("statusBanner");
  if (!el) return;
  el.className = "status-banner show " + level;
  el.textContent = message;
  if (statusTimer) clearTimeout(statusTimer);
  if (timeoutMs > 0) {{
    statusTimer = setTimeout(() => {{
      el.className = "status-banner";
      el.textContent = "";
      statusTimer = null;
    }}, timeoutMs);
  }}
}}

function albumKeyOf(name) {{
  return (name || "Unknown Album").trim().toLowerCase();
}}
function albumDomId(key) {{
  return "album-" + key.replace(/[^a-z0-9_-]/g, "-");
}}

function formatDuration(s) {{
  const n = Number(s) || 0;
  if (n <= 0) return "--:--";
  const total = Math.round(n);
  const mm = Math.floor(total / 60);
  const ss = String(total % 60).padStart(2, "0");
  return mm + ":" + ss;
}}
function clamp(n, lo, hi) {{
  return Math.min(hi, Math.max(lo, n));
}}

function escapeHtml(v) {{
  return String(v || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}}

const ALBUMS = (() => {{
  const byAlbum = new Map();
  for (const t of TRACKS) {{
    const name = t.album || "Unknown Album";
    const key = albumKeyOf(name);
    if (!byAlbum.has(key)) byAlbum.set(key, {{ key, name, tracks: [] }});
    byAlbum.get(key).tracks.push(t);
  }}
  const arr = Array.from(byAlbum.values());
  for (const a of arr) {{
    a.tracks.sort((x, y) => (x.title || "").localeCompare(y.title || ""));
    a.artist = a.tracks.find(t => t.artist)?.artist || "Unknown Artist";
    a.artwork_url = a.tracks.find(t => t.artwork_url)?.artwork_url || "";
  }}
  arr.sort((a, b) => a.name.localeCompare(b.name));
  return arr;
}})();

function _normText(v) {{
  return String(v || "").trim().toLowerCase();
}}
function _facetValues(extractor) {{
  const vals = new Set();
  for (const t of TRACKS) {{
    const v = String(extractor(t) || "").trim();
    if (v) vals.add(v);
  }}
  return Array.from(vals).sort((a, b) => a.localeCompare(b));
}}
const FACET_ARTISTS = _facetValues(t => t.artist);
const FACET_GENRES = _facetValues(t => t.genre);
const FACET_YEARS = _facetValues(t => t.year);

function _defaultFilters() {{
  return {{
    q: "",
    artist: "",
    genre: "",
    format: "",
    year: "",
  }};
}}
function loadFilters() {{
  try {{
    const raw = localStorage.getItem(FILTER_STATE_KEY);
    if (!raw) return _defaultFilters();
    const parsed = JSON.parse(raw);
    return {{
      q: String(parsed.q || ""),
      artist: String(parsed.artist || ""),
      genre: String(parsed.genre || ""),
      format: String(parsed.format || ""),
      year: String(parsed.year || ""),
    }};
  }} catch {{
    return _defaultFilters();
  }}
}}
function saveFilters() {{
  localStorage.setItem(FILTER_STATE_KEY, JSON.stringify(filters));
}}
let filters = loadFilters();
let playlistsCache = [];

function loadAlbumEnabled() {{
  const raw = localStorage.getItem(ALBUM_STATE_KEY);
  const state = raw ? JSON.parse(raw) : {{}};
  for (const a of ALBUMS) {{
    if (typeof state[a.key] !== "boolean") state[a.key] = true;
  }}
  return state;
}}
function saveAlbumEnabled() {{
  localStorage.setItem(ALBUM_STATE_KEY, JSON.stringify(albumEnabled));
}}
function loadAlbumOpen() {{
  const raw = localStorage.getItem(ALBUM_OPEN_KEY);
  const state = raw ? JSON.parse(raw) : {{}};
  for (const a of ALBUMS) {{
    if (typeof state[a.key] !== "boolean") state[a.key] = false;
  }}
  return state;
}}
function saveAlbumOpen() {{
  localStorage.setItem(ALBUM_OPEN_KEY, JSON.stringify(albumOpen));
}}
function loadTrackEnabled() {{
  const raw = localStorage.getItem(TRACK_STATE_KEY);
  const state = raw ? JSON.parse(raw) : {{}};
  for (const t of TRACKS) {{
    if (typeof state[t.track_id] !== "boolean") state[t.track_id] = true;
  }}
  return state;
}}
function saveTrackEnabled() {{
  localStorage.setItem(TRACK_STATE_KEY, JSON.stringify(trackEnabled));
}}
let albumEnabled = loadAlbumEnabled();
let albumOpen = loadAlbumOpen();
let trackEnabled = loadTrackEnabled();

async function confirmClear() {{
  if (!window.confirm("Are you sure you want to CLEAR your library on the server?")) return;
  try {{
    const res = await fetch(API + "/library/" + userId + "/clear", {{ method: "POST" }});
    if (!res.ok) throw new Error(await res.text());
    // Reload after successful clear so the table reflects empty state
    location.reload();
  }} catch (e) {{
    showStatus("Clear failed: " + (e?.message || e), "error", 6000);
  }}
}}

function renderAlbumList() {{
  const root = document.getElementById("albumList");
  if (!root) return;
  const albums = filteredAlbums();
  if (albums.length === 0) {{
    root.innerHTML = "<p class=\\"muted\\">No matching albums/tracks for current filters.</p>";
    updateLibrarySummary(albums);
    return;
  }}
  root.innerHTML = albums.map(a => {{
    const open = albumOpen[a.key];
    const enabled = albumEnabled[a.key];
    const tracksHtml = a.tracks.map(t => {{
      const mark = t.ok ? "OK" : "Missing";
      return (
        "<div class=\\"track-row\\">" +
          "<div class=\\"track-title\\" title=\\"" + escapeHtml(t.title) + "\\">" + escapeHtml(t.title) + "</div>" +
          "<div class=\\"track-artist\\" title=\\"" + escapeHtml(t.artist) + "\\">" + escapeHtml(t.artist) + "</div>" +
          "<div class=\\"muted\\">" + formatDuration(t.duration_sec) + "</div>" +
          "<label class=\\"pill\\"><input type=\\"checkbox\\" " + (trackEnabled[t.track_id] ? "checked" : "") + " onchange=\\"toggleTrackEnabled('" + t.track_id + "', this.checked)\\"> In Shuffle</label>" +
          "<div><button class=\\"btn\\" type=\\"button\\" onclick=\\"playById('" + t.track_id + "')\\">Play</button></div>" +
        "</div>"
      );
    }}).join("");
    return (
      "<section id=\\"" + albumDomId(a.key) + "\\" class=\\"album-card\\">" +
        "<div class=\\"album-head\\">" +
          "<div class=\\"album-main\\">" +
            "<img class=\\"album-cover\\" src=\\"" + escapeHtml(a.artwork_url || "") + "\\" alt=\\"Album cover\\" loading=\\"lazy\\" onerror=\\"this.style.display='none'\\">" +
            "<div>" +
              "<div class=\\"album-title\\">" + escapeHtml(a.name) + "</div>" +
              "<div class=\\"album-meta\\">" + escapeHtml(a.artist) + " · " + a.tracks.length + " tracks</div>" +
            "</div>" +
          "</div>" +
          "<div class=\\"album-actions\\">" +
            "<label class=\\"pill\\"><input type=\\"checkbox\\" " + (enabled ? "checked" : "") + " onchange=\\"toggleAlbumEnabled('" + a.key + "', this.checked)\\"> In Shuffle</label>" +
            "<button class=\\"btn\\" type=\\"button\\" onclick=\\"toggleAlbumOpen('" + a.key + "')\\">" + (open ? "Hide" : "Open") + "</button>" +
          "</div>" +
        "</div>" +
        "<div class=\\"album-tracks\\" style=\\"display:" + (open ? "block" : "none") + "\\">" + tracksHtml + "</div>" +
      "</section>"
    );
  }}).join("");
  updateLibrarySummary(albums);
}}

function _trackMatchesFilters(t) {{
  const q = _normText(filters.q);
  if (q) {{
    const hay = _normText([t.title, t.artist, t.album, t.genre].join(" "));
    if (!hay.includes(q)) return false;
  }}
  if (filters.artist && String(t.artist || "") !== filters.artist) return false;
  if (filters.genre && String(t.genre || "") !== filters.genre) return false;
  if (filters.format && String(t.format_family || "") !== filters.format) return false;
  if (filters.year && String(t.year || "") !== filters.year) return false;
  return true;
}}

function filteredAlbums() {{
  const out = [];
  for (const a of ALBUMS) {{
    const subset = a.tracks.filter(_trackMatchesFilters);
    if (!subset.length) continue;
    out.push({{
      ...a,
      tracks: subset,
    }});
  }}
  return out;
}}

function updateLibrarySummary(albums) {{
  const el = document.getElementById("libSummary");
  if (!el) return;
  const albumCount = albums.length;
  let trackCount = 0;
  for (const a of albums) trackCount += a.tracks.length;
  el.textContent = "Showing " + albumCount + " albums · " + trackCount + " tracks";
}}

function filteredTrackIds() {{
  const out = [];
  for (const a of filteredAlbums()) {{
    for (const t of a.tracks) out.push(t.track_id);
  }}
  return out;
}}

function updatePlaylistSummary(text) {{
  const el = document.getElementById("plSummary");
  if (el) el.textContent = text || "";
}}

async function refreshPlaylists() {{
  try {{
    const res = await fetch(API + "/playlists/" + userId, {{ cache: "no-store" }});
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    playlistsCache = data.playlists || [];
    const sel = document.getElementById("plSelect");
    if (!sel) return;
    const old = sel.value;
    sel.innerHTML = '<option value="">Select playlist</option>';
    for (const p of playlistsCache) {{
      const opt = document.createElement("option");
      opt.value = p.playlist_id;
      opt.textContent = p.name + " (" + p.track_count + ")";
      sel.appendChild(opt);
    }}
    if (old && playlistsCache.some(p => p.playlist_id === old)) sel.value = old;
    updatePlaylistSummary("Playlists: " + playlistsCache.length);
  }} catch (e) {{
    updatePlaylistSummary("Playlist refresh failed");
  }}
}}
window.refreshPlaylists = refreshPlaylists;

async function createPlaylistFromInput() {{
  const input = document.getElementById("plName");
  const name = input ? String(input.value || "").trim() : "";
  if (!name) {{
    showStatus("Enter a playlist name first.", "warn", 2500);
    return;
  }}
  try {{
    const res = await fetch(API + "/playlists/" + userId, {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{ name }}),
    }});
    if (!res.ok) throw new Error(await res.text());
    if (input) input.value = "";
    showStatus("Playlist created.", "info", 2500);
    await refreshPlaylists();
  }} catch (e) {{
    showStatus("Playlist create failed: " + (e?.message || e), "error", 4500);
  }}
}}
window.createPlaylistFromInput = createPlaylistFromInput;

async function addFilteredToPlaylist() {{
  const sel = document.getElementById("plSelect");
  const playlistId = sel ? sel.value : "";
  if (!playlistId) {{
    showStatus("Select a playlist first.", "warn", 2500);
    return;
  }}
  const track_ids = filteredTrackIds();
  if (!track_ids.length) {{
    showStatus("No tracks match current filters.", "warn", 2500);
    return;
  }}
  try {{
    const res = await fetch(API + "/playlists/" + userId + "/" + playlistId + "/add", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{ track_ids }}),
    }});
    if (!res.ok) throw new Error(await res.text());
    const j = await res.json();
    showStatus("Added " + j.added + " tracks to playlist.", "info", 3000);
    await refreshPlaylists();
  }} catch (e) {{
    showStatus("Add to playlist failed: " + (e?.message || e), "error", 4500);
  }}
}}
window.addFilteredToPlaylist = addFilteredToPlaylist;

async function addCurrentTrackToPlaylist() {{
  const sel = document.getElementById("plSelect");
  const playlistId = sel ? sel.value : "";
  if (!playlistId) {{
    showStatus("Select a playlist first.", "warn", 2500);
    return;
  }}
  if (!currentTid) {{
    showStatus("Play a track first (or use Add filtered tracks).", "warn", 2500);
    return;
  }}
  try {{
    const res = await fetch(API + "/playlists/" + userId + "/" + playlistId + "/add", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{ track_ids: [currentTid] }}),
    }});
    if (!res.ok) throw new Error(await res.text());
    const j = await res.json();
    showStatus("Added " + j.added + " current track.", "info", 2500);
    await refreshPlaylists();
  }} catch (e) {{
    showStatus("Add current track failed: " + (e?.message || e), "error", 4500);
  }}
}}
window.addCurrentTrackToPlaylist = addCurrentTrackToPlaylist;

async function clearSelectedPlaylist() {{
  const sel = document.getElementById("plSelect");
  const playlistId = sel ? sel.value : "";
  if (!playlistId) {{
    showStatus("Select a playlist first.", "warn", 2500);
    return;
  }}
  if (!window.confirm("Clear all tracks from selected playlist?")) return;
  try {{
    const res = await fetch(API + "/playlists/" + userId + "/" + playlistId + "/clear", {{
      method: "POST",
    }});
    if (!res.ok) throw new Error(await res.text());
    const j = await res.json();
    showStatus("Cleared playlist (" + j.removed + " removed).", "info", 3000);
    await refreshPlaylists();
    await showSelectedPlaylist();
  }} catch (e) {{
    showStatus("Clear playlist failed: " + (e?.message || e), "error", 4500);
  }}
}}
window.clearSelectedPlaylist = clearSelectedPlaylist;

async function showSelectedPlaylist() {{
  const sel = document.getElementById("plSelect");
  const playlistId = sel ? sel.value : "";
  if (!playlistId) {{
    showStatus("Select a playlist first.", "warn", 2500);
    return;
  }}
  try {{
    const res = await fetch(API + "/playlists/" + userId + "/" + playlistId, {{ cache: "no-store" }});
    if (!res.ok) throw new Error(await res.text());
    const p = await res.json();
    updatePlaylistSummary("Selected: " + p.name + " · " + (p.tracks || []).length + " tracks");
  }} catch (e) {{
    showStatus("Playlist load failed: " + (e?.message || e), "error", 4500);
  }}
}}
window.showSelectedPlaylist = showSelectedPlaylist;

function bindFilterUi() {{
  const artistSel = document.getElementById("fltArtist");
  const genreSel = document.getElementById("fltGenre");
  const yearSel = document.getElementById("fltYear");
  const qIn = document.getElementById("fltQuery");
  const formatSel = document.getElementById("fltFormat");

  if (artistSel) {{
    artistSel.innerHTML = '<option value="">All artists</option>';
    for (const v of FACET_ARTISTS) {{
      const opt = document.createElement("option");
      opt.value = v;
      opt.textContent = v;
      artistSel.appendChild(opt);
    }}
    artistSel.value = filters.artist || "";
  }}
  if (genreSel) {{
    genreSel.innerHTML = '<option value="">All genres</option>';
    for (const v of FACET_GENRES) {{
      const opt = document.createElement("option");
      opt.value = v;
      opt.textContent = v;
      genreSel.appendChild(opt);
    }}
    genreSel.value = filters.genre || "";
  }}
  if (yearSel) {{
    yearSel.innerHTML = '<option value="">All years</option>';
    for (const v of FACET_YEARS) {{
      const opt = document.createElement("option");
      opt.value = v;
      opt.textContent = v;
      yearSel.appendChild(opt);
    }}
    yearSel.value = filters.year || "";
  }}
  if (qIn) qIn.value = filters.q || "";
  if (formatSel) formatSel.value = filters.format || "";

  const onChange = () => {{
    filters = {{
      q: qIn ? qIn.value : "",
      artist: artistSel ? artistSel.value : "",
      genre: genreSel ? genreSel.value : "",
      format: formatSel ? formatSel.value : "",
      year: yearSel ? yearSel.value : "",
    }};
    saveFilters();
    renderAlbumList();
  }};
  if (qIn) qIn.addEventListener("input", onChange);
  if (artistSel) artistSel.addEventListener("change", onChange);
  if (genreSel) genreSel.addEventListener("change", onChange);
  if (formatSel) formatSel.addEventListener("change", onChange);
  if (yearSel) yearSel.addEventListener("change", onChange);
}}

function resetFilters() {{
  filters = _defaultFilters();
  saveFilters();
  const artistSel = document.getElementById("fltArtist");
  const genreSel = document.getElementById("fltGenre");
  const yearSel = document.getElementById("fltYear");
  const qIn = document.getElementById("fltQuery");
  const formatSel = document.getElementById("fltFormat");
  if (qIn) qIn.value = "";
  if (artistSel) artistSel.value = "";
  if (genreSel) genreSel.value = "";
  if (yearSel) yearSel.value = "";
  if (formatSel) formatSel.value = "";
  renderAlbumList();
}}
window.resetFilters = resetFilters;

function toggleAlbumEnabled(key, enabled) {{
  albumEnabled[key] = !!enabled;
  saveAlbumEnabled();
}}
window.toggleAlbumEnabled = toggleAlbumEnabled;

function toggleTrackEnabled(tid, enabled) {{
  trackEnabled[tid] = !!enabled;
  saveTrackEnabled();
}}
window.toggleTrackEnabled = toggleTrackEnabled;

function toggleAlbumOpen(key) {{
  const next = !albumOpen[key];
  for (const a of ALBUMS) albumOpen[a.key] = false;
  albumOpen[key] = next;
  saveAlbumOpen();
  renderAlbumList();
}}
window.toggleAlbumOpen = toggleAlbumOpen;

function setAllAlbums(enabled) {{
  for (const a of filteredAlbums()) albumEnabled[a.key] = !!enabled;
  saveAlbumEnabled();
  renderAlbumList();
}}
window.setAllAlbums = setAllAlbums;

function metaFor(tid) {{
  const t = TRACK_BY_ID[tid];
  if (!t) return {{title:tid, artist:"", album:"", force_mp3:false, duration_sec:0}};
  return t;
}}

function updateNowAlbumLink(albumName) {{
  const btn = document.getElementById("nowAlbumBtn");
  if (!btn) return;
  const key = albumKeyOf(albumName);
  const known = ALBUMS.some(a => a.key === key);
  if (!known) {{
    btn.disabled = true;
    btn.textContent = "Go to album";
    btn.dataset.albumKey = "";
    return;
  }}
  btn.disabled = false;
  btn.textContent = "Go to album";
  btn.dataset.albumKey = key;
}}

function goToNowPlayingAlbum() {{
  const btn = document.getElementById("nowAlbumBtn");
  const key = btn && btn.dataset ? btn.dataset.albumKey : "";
  if (!key) return;
  if (!albumOpen[key]) {{
    albumOpen[key] = true;
    saveAlbumOpen();
    renderAlbumList();
  }}
  const el = document.getElementById(albumDomId(key));
  if (!el) {{
    resetFilters();
  }}
  const el2 = document.getElementById(albumDomId(key));
  if (!el2) return;
  el2.scrollIntoView({{ behavior: "smooth", block: "start" }});
}}
window.goToNowPlayingAlbum = goToNowPlayingAlbum;

document.addEventListener('DOMContentLoaded', () => {{
  bindFilterUi();
  renderAlbumList();
  refreshPlaylists();
}});

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
  if (!CROSSFADE_ENABLED) {{
    xcb.checked = false;
    xcb.disabled = true;
    xcb.title = MOBILE_UA
      ? "Crossfade disabled on mobile for playback stability"
      : "Crossfade unavailable in this browser";
  }} else {{
    xcb.checked = (localStorage.getItem('rt-xfade') === '1') && !/iPhone|iPad|Android/i.test(navigator.userAgent);
  }}
  xcb.addEventListener('change', async () => {{
    if (!CROSSFADE_ENABLED) {{
      xcb.checked = false;
      return;
    }}
    localStorage.setItem('rt-xfade', xcb.checked ? '1' : '0');
    if (xcb.checked) {{
      const ok = await ensureAudioGraph();
      if (ok && AC && AC.state === 'suspended') {{ try {{ await AC.resume(); }} catch {{}} }}
    }}
  }});
  if (CROSSFADE_ENABLED && xcb.checked) xcb.dispatchEvent(new Event('change'));

  const ka = document.getElementById("keepAwake");
  if (ka) {{
    const stored = localStorage.getItem(KEEP_AWAKE_KEY);
    ka.checked = (stored == null) ? MOBILE_UA : (stored === "1");
    ka.addEventListener("change", async () => {{
      localStorage.setItem(KEEP_AWAKE_KEY, ka.checked ? "1" : "0");
      if (!ka.checked) await releaseWakeLock();
      else if (!a1.paused) await ensureWakeLock();
    }});
  }}
}});

function setNowPlaying(tid) {{
  const m = metaFor(tid);
  currentTrackDurationSec = Number(m.duration_sec) || 0;
  const albumText = m.album ? " (" + m.album + ")" : "";
  let durText = "";
  if (currentTrackDurationSec > 0) {{
    const total = Math.max(0, Math.round(currentTrackDurationSec));
    const mm = Math.floor(total / 60);
    const ss = String(total % 60).padStart(2, "0");
    durText = " [" + mm + ":" + ss + "]";
  }}
  document.getElementById('now').innerText = m.artist + " — " + m.title + albumText + durText;
  renderNowMeta(m);
  updateNowAlbumLink(m.album);
  refreshSeekUi();
}}

function renderNowMeta(m) {{
  const img = document.getElementById("nowMetaImg");
  const head = document.getElementById("nowMetaHead");
  const artistBio = document.getElementById("nowArtistBio");
  const albumBio = document.getElementById("nowAlbumBio");
  if (!img || !head || !artistBio || !albumBio) return;

  const artwork = String(m?.artist_image_url || m?.artwork_url || "").trim();
  if (artwork) {{
    img.src = artwork;
    img.style.display = "";
    img.onerror = () => {{ img.style.display = "none"; }};
  }} else {{
    img.style.display = "none";
    img.removeAttribute("src");
  }}

  const bits = [];
  if (m?.artist) bits.push(m.artist);
  if (m?.album) bits.push(m.album);
  if (m?.year) bits.push(String(m.year));
  if (m?.genre) bits.push(String(m.genre));
  head.textContent = bits.length ? bits.join(" · ") : "No track selected.";

  const artistTxt = String(m?.artist_bio || "").trim();
  const albumTxt = String(m?.album_bio || "").trim();
  artistBio.textContent = artistTxt || "No artist story yet.";
  albumBio.textContent = albumTxt || "No album story yet.";
}}
function urlFor(tid, forceMp3, startSec = 0) {{
  const base = forceMp3 ? "/relay-mp3/" : "/relay/";
  let u = API + base + userId + "/" + tid + "?v=" + libver();
  if (forceMp3 && startSec > 0) u += "&start=" + encodeURIComponent(String(startSec));
  return u;
}}

function orderedTracks() {{
  return ALBUMS.flatMap(a => a.tracks);
}}

function shuffleEligibleTracks() {{
  return TRACKS.filter(t => albumEnabled[albumKeyOf(t.album)] && !!trackEnabled[t.track_id]);
}}

function currentPlaybackSec() {{
  const active = (ACTIVE === 2 ? a2 : a1);
  return Math.max(0, currentStartOffsetSec + (Number(active.currentTime) || 0));
}}

function noteProgress() {{
  lastProgressWallMs = Date.now();
  lastProgressPlaybackSec = currentPlaybackSec();
}}

function effectiveTrackDurationSec() {{
  const active = (ACTIVE === 2 ? a2 : a1);
  const metaDur = Math.max(0, Number(currentTrackDurationSec) || 0);
  const nativeDur = Number(active.duration);
  if (Number.isFinite(nativeDur) && nativeDur > 0) {{
    return Math.max(metaDur, currentStartOffsetSec + nativeDur);
  }}
  return metaDur;
}}

function refreshSeekUi() {{
  const bar = document.getElementById("seekBar");
  const label = document.getElementById("seekTime");
  const note = document.getElementById("seekNote");
  if (!bar || !label) return;
  const dur = effectiveTrackDurationSec();
  const pos = clamp(currentPlaybackSec(), 0, dur > 0 ? dur : 0);
  if (!seekDragging) bar.value = String(Math.round(pos));
  bar.max = String(Math.max(1, Math.round(dur || 1)));
  label.textContent = formatDuration(pos) + " / " + formatDuration(dur);
  if (note) {{
    note.textContent = currentStartOffsetSec > 0
      ? "Seek bar shows full track timeline. Native player bar shows only the current segment after seek."
      : "";
  }}
}}

async function seekTo(targetSec) {{
  if (!currentTid) return;
  const dur = Math.max(0, Number(currentTrackDurationSec) || 0);
  if (dur <= 0) return;
  const target = clamp(Number(targetSec) || 0, 0, dur);
  await playIdAt(currentTid, target, {{ recordHistory: false }});
}}

function skipBy(delta) {{
  seekTo(currentPlaybackSec() + Number(delta || 0));
}}
window.skipBy = skipBy;

async function ensureWakeLock() {{
  const ka = document.getElementById("keepAwake");
  if (!ka || !ka.checked) return;
  if (!("wakeLock" in navigator)) return;
  if (wakeLock) return;
  try {{
    wakeLock = await navigator.wakeLock.request("screen");
    wakeLock.addEventListener("release", () => {{ wakeLock = null; }});
  }} catch (e) {{
    // Ignore unsupported/denied lock requests.
    wakeLock = null;
  }}
}}

async function releaseWakeLock() {{
  try {{
    if (wakeLock) await wakeLock.release();
  }} catch (e) {{}}
  wakeLock = null;
}}

// --- plain play (always routes playback back to a1) ---
async function playId(tid) {{
  return playIdAt(tid, 0, {{ recordHistory: true }});
}}
async function playIdAt(tid, startSec, opts = {{}}) {{
  const recordHistory = opts.recordHistory !== false;
  if (tid !== currentTid) {{
    recoveryWindowStartMs = 0;
    recoveryCountInWindow = 0;
  }}
  const m = metaFor(tid);
  currentStartOffsetSec = Math.max(0, Number(startSec) || 0);
  const url = urlFor(tid, !!m.force_mp3, currentStartOffsetSec);
  a1.src = url; a1.load();
  try {{ await waitForCanPlay(a1, 8000); }} catch {{}}
  try {{ await a1.play(); }} catch {{}}
  await ensureWakeLock();
  try {{ a2.pause(); }} catch {{}}
  ACTIVE = 1;
  setNowPlaying(tid);
  currentTid = tid;
  lastTid = tid;
  if (recordHistory && currentStartOffsetSec <= 0) {{
    if (!(historyIndex >= 0 && playHistory[historyIndex] === tid)) {{
      if (historyIndex < playHistory.length - 1) {{
        playHistory = playHistory.slice(0, historyIndex + 1);
      }}
      playHistory.push(tid);
      historyIndex = playHistory.length - 1;
    }}
  }}
  noteProgress();
}}
window.playById = (tid) => {{
  const shuffleOn = !!(document.getElementById('shuffle') && document.getElementById('shuffle').checked);
  if (shuffleOn) {{
    playNext();
    return;
  }}
  const useX = CROSSFADE_ENABLED && document.getElementById('xfade') && document.getElementById('xfade').checked;
  useX ? xfadeTo(tid, {{ recordHistory: true }}) : playIdAt(tid, 0, {{ recordHistory: true }});
}};

// --- crossfade + *handoff back to a1* after the fade ---
async function xfadeTo(tid, opts = {{}}) {{
  const recordHistory = opts.recordHistory !== false;
  if (!CROSSFADE_ENABLED) {{ await playIdAt(tid, 0, {{ recordHistory }}); return; }}
  const toggle = document.getElementById('xfade');
  const secsEl = document.getElementById('xfadeSecs');
  const secs = Math.max(0, Math.min(10, Number(secsEl ? secsEl.value : 0) || 0));
  if (!toggle || !toggle.checked || secs <= 0) {{ await playIdAt(tid, 0, {{ recordHistory }}); return; }}

  const ok = await ensureAudioGraph();
  if (!ok || !g1 || !g2) {{ await playIdAt(tid, 0, {{ recordHistory }}); return; }}
  try {{ if (AC.state === 'suspended') await AC.resume(); }} catch {{}}

  const active = (ACTIVE === 1 ? a1 : a2);
  const helper = (ACTIVE === 1 ? a2 : a1);
  const gActive = (ACTIVE === 1 ? g1 : g2);
  const gHelper = (ACTIVE === 1 ? g2 : g1);

  // 1) Prepare helper and prebuffer
  const m = metaFor(tid);
  const url = urlFor(tid, !!m.force_mp3);
  helper.src = url; helper.load();
  try {{ await waitForCanPlay(helper, 8000); }} catch (e) {{ console.warn("[xfade] helper timeout:", e); await playIdAt(tid, 0, {{ recordHistory }}); return; }}
  try {{ await helper.play(); }} catch {{ await playIdAt(tid, 0, {{ recordHistory }}); return; }}

  // 2) Run gain ramps
  const now = AC.currentTime;
  gActive.gain.cancelScheduledValues(now);
  gHelper.gain.cancelScheduledValues(now);
  gActive.gain.setValueAtTime(gActive.gain.value, now);
  gHelper.gain.setValueAtTime(gHelper.gain.value, now);
  gActive.gain.linearRampToValueAtTime(0.0, now + secs);
  gHelper.gain.linearRampToValueAtTime(1.0, now + secs);

  setNowPlaying(tid);
  currentTid = tid;
  lastTid = tid;
  if (recordHistory) {{
    if (!(historyIndex >= 0 && playHistory[historyIndex] === tid)) {{
      if (historyIndex < playHistory.length - 1) {{
        playHistory = playHistory.slice(0, historyIndex + 1);
      }}
      playHistory.push(tid);
      historyIndex = playHistory.length - 1;
    }}
  }}
  noteProgress();

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
function pickNextTrackId() {{
  const shuffle = document.getElementById('shuffle').checked;
  if (shuffle) {{
    const list = shuffleEligibleTracks();
    if (list.length === 0) return null;
    if (list.length === 1) return list[0].track_id;
    let i = Math.floor(Math.random() * list.length);
    if (currentTid && list[i].track_id === currentTid) i = (i + 1 + Math.floor(Math.random() * (list.length - 1))) % list.length;
    return list[i].track_id;
  }}
  const list = orderedTracks();
  if (list.length === 0) return null;
  const idx = list.findIndex(t => t.track_id === currentTid);
  return list[(idx + 1 + list.length) % list.length].track_id;
}}
function pickPrevTrackId() {{
  const list = orderedTracks();
  if (list.length === 0) return null;
  const idx = list.findIndex(t => t.track_id === currentTid);
  return list[(idx - 1 + list.length) % list.length].track_id;
}}
function peekNextTrackIdForPrefetch() {{
  if (historyIndex >= 0 && historyIndex < (playHistory.length - 1)) {{
    return playHistory[historyIndex + 1];
  }}
  return pickNextTrackId();
}}
function prefetchTrackIfNeeded(tid) {{
  if (!canPrefetch()) return;
  if (!tid) return;
  const m = metaFor(tid);
  if (!m || !m.force_mp3) return;
  if (prefetchedTrackIds.has(tid) || prefetchInFlight.has(tid)) return;
  prefetchInFlight.add(tid);
  const u = urlFor(tid, true, 0);
  fetch(u, {{ method: "HEAD", cache: "no-store" }})
    .then(() => {{
      prefetchedTrackIds.add(tid);
    }})
    .catch(() => {{}})
    .finally(() => {{
      prefetchInFlight.delete(tid);
    }});
}}
function prefetchUpcomingTrack() {{
  if (!canPrefetch()) return;
  const autoplay = !!(document.getElementById('autoplay') && document.getElementById('autoplay').checked);
  if (!autoplay) return;
  const nextTid = peekNextTrackIdForPrefetch();
  prefetchTrackIfNeeded(nextTid);
}}
function playNext() {{
  const shuffleOn = !!(document.getElementById('shuffle') && document.getElementById('shuffle').checked);
  let tid = null;
  let fromHistory = false;
  if (historyIndex >= 0 && historyIndex < (playHistory.length - 1)) {{
    historyIndex += 1;
    tid = playHistory[historyIndex];
    fromHistory = true;
  }} else {{
    tid = pickNextTrackId();
  }}
  if (!tid) {{
    showStatus(shuffleOn ? "No tracks in selected albums/tracks for shuffle." : "No tracks available.", "warn");
    return;
  }}
  const useX = CROSSFADE_ENABLED && document.getElementById('xfade') && document.getElementById('xfade').checked;
  useX ? xfadeTo(tid, {{ recordHistory: !fromHistory }}) : playIdAt(tid, 0, {{ recordHistory: !fromHistory }});
}}
function playPrev() {{
  let tid = null;
  let fromHistory = false;
  if (historyIndex > 0) {{
    historyIndex -= 1;
    tid = playHistory[historyIndex];
    fromHistory = true;
  }} else {{
    tid = pickPrevTrackId();
  }}
  if (!tid) {{ showStatus("No tracks in selected albums.", "warn"); return; }}
  const useX = CROSSFADE_ENABLED && document.getElementById('xfade') && document.getElementById('xfade').checked;
  useX ? xfadeTo(tid, {{ recordHistory: !fromHistory }}) : playIdAt(tid, 0, {{ recordHistory: !fromHistory }});
}}

// Ensure native ▶️ kicks off a valid source and respects the crossfade setting
function kickIfEmpty() {{
  if (!a1.currentSrc || a1.currentSrc === "" || a1.currentSrc === window.location.href) {{
    const shuffleOn = !!(document.getElementById('shuffle') && document.getElementById('shuffle').checked);
    if (shuffleOn) {{
      playNext();
    }} else if (currentTid) {{
      const useX = CROSSFADE_ENABLED && document.getElementById('xfade') && document.getElementById('xfade').checked;
      useX ? xfadeTo(currentTid, {{ recordHistory: false }}) : playId(currentTid);
    }} else {{
      playNext();
    }}
    setTimeout(() => a1.play().catch(() => {{}}), 0);
  }}
}}

const seekBarEl = document.getElementById("seekBar");
if (seekBarEl) {{
  seekBarEl.addEventListener("input", () => {{
    seekDragging = true;
    const label = document.getElementById("seekTime");
    const dur = Math.max(0, Number(currentTrackDurationSec) || 0);
    const pos = clamp(Number(seekBarEl.value) || 0, 0, dur > 0 ? dur : 0);
    if (label) label.textContent = formatDuration(pos) + " / " + formatDuration(dur);
  }});
  seekBarEl.addEventListener("change", async () => {{
    const v = Number(seekBarEl.value) || 0;
    seekDragging = false;
    await seekTo(v);
  }});
}}

function parseTrackIdFromSrc(src) {{
  const m = String(src || "").match(/\\/relay(?:-mp3)?\\/[^/]+\\/([0-9a-f]{{40}})/i);
  return m ? m[1] : null;
}}

function reserveRecoverySlot() {{
  const now = Date.now();
  if (!recoveryWindowStartMs || (now - recoveryWindowStartMs) > 60000) {{
    recoveryWindowStartMs = now;
    recoveryCountInWindow = 0;
  }}
  if (recoveryCountInWindow >= 4) return false;
  recoveryCountInWindow += 1;
  return true;
}}

async function recoverCurrentTrack(reason, opts = {{}}) {{
  if (!currentTid || recoveryInFlight) return false;
  const minPos = Number(opts.minPos ?? 2);
  const tailGuard = Number(opts.tailGuard ?? 6);
  const jumpSec = Number(opts.jumpSec ?? 1);
  const timeoutMs = Number(opts.timeoutMs ?? 3200);

  const dur = effectiveTrackDurationSec();
  const pos = currentPlaybackSec();
  if (!(dur > 0 && pos >= minPos && pos < (dur - tailGuard))) return false;
  if (!reserveRecoverySlot()) return false;

  recoveryInFlight = true;
  try {{
    const resumeAt = clamp(pos + jumpSec, 0, Math.max(0, dur - 1));
    showStatus(reason + " Resuming at " + formatDuration(resumeAt) + ".", "warn", timeoutMs);
    await playIdAt(currentTid, resumeAt, {{ recordHistory: false }});
    return true;
  }} finally {{
    recoveryInFlight = false;
  }}
}}

async function monitorStall() {{
  if (!currentTid || recoveryInFlight) return;
  if (a1.paused || a1.ended) return;
  const now = Date.now();
  const pos = currentPlaybackSec();
  const moved = pos > (lastProgressPlaybackSec + 0.20);
  if (moved) {{
    lastProgressPlaybackSec = pos;
    lastProgressWallMs = now;
    return;
  }}
  if (!lastProgressWallMs) {{
    lastProgressWallMs = now;
    return;
  }}
  const stalledForMs = now - lastProgressWallMs;
  const lowBuffer = a1.readyState <= HTMLMediaElement.HAVE_CURRENT_DATA;
  if (stalledForMs >= 12000 && lowBuffer) {{
    const recovered = await recoverCurrentTrack("Buffer stalled", {{ minPos: 2, tailGuard: 8, jumpSec: 1, timeoutMs: 2800 }});
    if (recovered) noteProgress();
  }}
}}

async function classifyPlaybackError(src) {{
  const tid = parseTrackIdFromSrc(src);
  if (!tid) return "Unclassified playback error";
  try {{
    const res = await fetch(API + "/debug/peek/" + userId + "/" + tid + "?v=" + libver(), {{ cache: "no-store" }});
    if (!res.ok) return "Diagnostics unavailable (HTTP " + res.status + ")";
    const j = await res.json();
    const hs = Number(j.head_status || 0);
    if (hs === 404) return "Source missing/moved or NAS temporarily unavailable (upstream 404)";
    if (hs === 401 || hs === 403) return "Source access denied/auth mismatch (upstream " + hs + ")";
    if (hs >= 500) return "Agent/source server error (upstream " + hs + ")";
    if (hs > 0 && hs < 400) return "Stream reachable; likely transcode/network interruption";
    if (j.reason === "no_base_or_rel_path") return "Agent offline or track path not mapped";
    if (j.error) return "Upstream connectivity problem: " + j.error;
    return "Unknown playback issue";
  }} catch (e) {{
    return "Diagnostic request failed";
  }}
}}

a1.addEventListener('error', async () => {{
  const srcAtError = a1.currentSrc || "(no source)";
  // Ignore transient decode/network blips if playback recovers quickly.
  await new Promise(r => setTimeout(r, 1200));
  if ((a1.currentSrc || "(no source)") !== srcAtError) return;
  if (!a1.paused && !a1.ended) return;
  if (a1.readyState >= HTMLMediaElement.HAVE_FUTURE_DATA && !a1.paused) return;
  if (await recoverCurrentTrack("Playback interrupted", {{ minPos: 2, tailGuard: 6, jumpSec: 1, timeoutMs: 3200 }})) return;

  const reason = await classifyPlaybackError(srcAtError);
  showStatus("Audio error. Could not load. Reason: " + reason, "error", 7000);
  // Debug mode: do not auto-skip on hard errors.
  // Keep the failed track visible so issues are not masked.
}});

// Autoplay next when finished
let a1StartedAt = 0;
let lastAutoAdvanceAt = 0;
a1.addEventListener('playing', () => {{ a1StartedAt = Date.now(); noteProgress(); }});
a1.addEventListener('play', () => {{ kickIfEmpty(); }});
a1.addEventListener('timeupdate', noteProgress);
a1.addEventListener('playing', () => {{
  setTimeout(prefetchUpcomingTrack, 1200);
}});
a1.addEventListener('seeked', noteProgress);
a1.addEventListener('waiting', () => {{
  showStatus("Buffering...", "info", 1600);
}});
a1.addEventListener('pause', () => {{ if (a1.paused) releaseWakeLock(); }});
document.addEventListener("visibilitychange", () => {{
  if (!document.hidden && !a1.paused) ensureWakeLock();
}});
setInterval(() => {{
  if (!a1.paused && !a1.ended) prefetchUpcomingTrack();
}}, 15000);

a1.addEventListener('ended', async () => {{
  const autoplay = document.getElementById('autoplay').checked;
  const now = Date.now();
  // Do not trust native duration alone; some relays report a shortened duration.
  // Use effective track duration (metadata-aware) to decide early-ended recovery.
  const expectedDur = effectiveTrackDurationSec();
  const expectedPos = currentPlaybackSec();
  const endedNearExpectedTail = expectedDur > 0 && expectedPos >= (expectedDur - 1.5);
  if (!endedNearExpectedTail) {{
    if (await recoverCurrentTrack("Stream ended early", {{ minPos: 5, tailGuard: 12, jumpSec: 2, timeoutMs: 3200 }})) return;
    showStatus("Track ended before expected duration; staying on current track.", "warn", 4200);
    return;
  }}

  if (!autoplay) return;

  // iOS bogus early-ended protection (common with chunked/live transcodes)
  const dur = Math.max(0, Number(currentTrackDurationSec) || 0);
  const pos = currentPlaybackSec();
  if (a1StartedAt && (now - a1StartedAt) < 8000) {{
    // One best-effort recovery before giving up.
    if (dur > 0 && pos < (dur - 5)) {{
      await seekTo(Math.min(dur - 1, pos + 1));
    }}
    return;
  }}

  // extra safety: don’t auto-advance twice within 2s
  if (now - lastAutoAdvanceAt < 2000) return;
  lastAutoAdvanceAt = now;
  playNext();
  setTimeout(() => a1.play().catch(() => {{}}), 0);
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
setInterval(refreshSeekUi, 400);
setInterval(monitorStall, 5000);
renderAlbumList();
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
            "artist_bio": t.get("artist_bio") or "",
            "album_bio": t.get("album_bio") or "",
            "duration_sec": t.get("duration_sec") or 0,
            "force_mp3": _track_needs_mp3_proxy(t),
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
  .status-banner{{display:none;margin-top:8px;padding:8px 10px;border-radius:8px;border:1px solid #d1d5db;background:#f9fafb;font-size:13px}}
  .status-banner.show{{display:block}}
  .status-banner.error{{border-color:#fca5a5;background:#fef2f2;color:#991b1b}}
  .status-banner.warn{{border-color:#fcd34d;background:#fffbeb;color:#92400e}}
  .status-banner.info{{border-color:#93c5fd;background:#eff6ff;color:#1e3a8a}}
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
  <div id="statusBanner" class="status-banner"></div>
</div>

<script>
// NOTE: Inside a Python f-string — ALL braces are doubled {{ }}.

function libver() {{ return Math.floor(Date.now()/1000); }}
const userId = "{user_id}";
const TRACKS = {tracks_json};

// Robust API base: if path contains /streamer/ anywhere, use /streamer/api
const API = "/streamer/api";
const TINY_MOBILE_UA = /iPhone|iPad|iPod|Android|Mobile/i.test(navigator.userAgent || "");
function tinyCanPrefetch() {{
  if (TINY_MOBILE_UA) return false;
  const c = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
  if (!c) return true;
  if (c.saveData) return false;
  const et = String(c.effectiveType || "").toLowerCase();
  if (et.includes("2g") || et.includes("3g")) return false;
  return true;
}}

let statusTimer = null;
let prefetchedTinyTrackIds = new Set();
let tinyPrefetchInFlight = new Set();
function showStatus(message, level = "info", timeoutMs = 4500) {{
  const el = document.getElementById("statusBanner");
  if (!el) return;
  el.className = "status-banner show " + level;
  el.textContent = message;
  if (statusTimer) clearTimeout(statusTimer);
  if (timeoutMs > 0) {{
    statusTimer = setTimeout(() => {{
      el.className = "status-banner";
      el.textContent = "";
      statusTimer = null;
    }}, timeoutMs);
  }}
}}

// Small logger for visibility
function logAudioState(prefix, el) {{
  const states = ["HAVE_NOTHING","HAVE_METADATA","HAVE_CURRENT_DATA","HAVE_FUTURE_DATA","HAVE_ENOUGH_DATA"];
  console.log(`[audio] ${{prefix}}`, {{
    src: el.currentSrc,
    readyState: states[el.readyState] || el.readyState,
    paused: el.paused, ended: el.ended, networkState: el.networkState,
    error: el.error && {{code: el.error.code, msg: el.error.message}}
  }});
}}

// Pick a random track
function pickIndex() {{
  if (TRACKS.length === 0) return -1;
  if (TRACKS.length === 1) return 0;
  let i = Math.floor(Math.random() * TRACKS.length);
  if (current >= 0 && i === current) {{
    i = (i + 1 + Math.floor(Math.random() * (TRACKS.length - 1))) % TRACKS.length;
  }}
  return i;
}}

function urlFor(track) {{
  const base = track && track.force_mp3 ? "/relay-mp3/" : "/relay/";
  return API + base + userId + "/" + track.track_id + "?v=" + libver();
}}
function prefetchTinyTrack(i) {{
  if (!tinyCanPrefetch()) return;
  if (i < 0 || i >= TRACKS.length) return;
  const t = TRACKS[i];
  if (!t || !t.force_mp3) return;
  const tid = t.track_id;
  if (prefetchedTinyTrackIds.has(tid) || tinyPrefetchInFlight.has(tid)) return;
  tinyPrefetchInFlight.add(tid);
  fetch(urlFor(t), {{ method: "HEAD", cache: "no-store" }})
    .then(() => {{ prefetchedTinyTrackIds.add(tid); }})
    .catch(() => {{}})
    .finally(() => {{ tinyPrefetchInFlight.delete(tid); }});
}}

// Use a <source type="audio/mpeg"> to help stricter browsers
function setAudioSrc(audio, url) {{
  audio.src = url;
}}

function playIndex(i) {{
  const m = TRACKS[i]; if (!m) return;
  const audio = document.getElementById('player');
  const url = urlFor(m);
  setAudioSrc(audio, url);
  audio.load();
  audio.play().then(() => logAudioState('playing-started', audio))
              .catch(() => logAudioState('play-catch', audio));
  const albumText = m.album ? " (" + m.album + ")" : "";
  let durText = "";
  if (m.duration_sec && m.duration_sec > 0) {{
    const total = Math.max(0, Math.round(m.duration_sec));
    const mm = Math.floor(total / 60);
    const ss = String(total % 60).padStart(2, "0");
    durText = " [" + mm + ":" + ss + "]";
  }}
  document.getElementById('now').innerText = m.artist + " — " + m.title + albumText + durText;
  // Warm a candidate next track while current is playing to reduce gap.
  setTimeout(() => {{
    const nx = pickIndex();
    prefetchTinyTrack(nx);
  }}, 1200);
}}
let current = -1;
function playNext() {{ current = pickIndex(); if (current >= 0) playIndex(current); }}

// Wire the explicit Next button (gives guaranteed user gesture)
document.getElementById('btnNext').addEventListener('click', () => playNext());

// Also allow the native ▶️ to kick the first track
const audio = document.getElementById('player');

let startedAt = 0;

audio.addEventListener('playing', () => {{
  startedAt = Date.now();
}});

function firstPlayKick() {{
  if (!audio.currentSrc || audio.currentSrc === "" || audio.currentSrc === window.location.href) {{
    playNext();
    setTimeout(() => audio.play().catch(() => {{}}), 0);
  }}
}}
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

function parseTrackIdFromSrc(src) {{
  const m = String(src || "").match(/\\/relay(?:-mp3)?\\/[^/]+\\/([0-9a-f]{{40}})/i);
  return m ? m[1] : null;
}}

async function classifyPlaybackError(src) {{
  const tid = parseTrackIdFromSrc(src);
  if (!tid) return "Unclassified playback error";
  try {{
    const res = await fetch(API + "/debug/peek/" + userId + "/" + tid + "?v=" + libver(), {{ cache: "no-store" }});
    if (!res.ok) return "Diagnostics unavailable (HTTP " + res.status + ")";
    const j = await res.json();
    const hs = Number(j.head_status || 0);
    if (hs === 404) return "Source missing/moved or NAS temporarily unavailable (upstream 404)";
    if (hs === 401 || hs === 403) return "Source access denied/auth mismatch (upstream " + hs + ")";
    if (hs >= 500) return "Agent/source server error (upstream " + hs + ")";
    if (hs > 0 && hs < 400) return "Stream reachable; likely transcode/network interruption";
    if (j.reason === "no_base_or_rel_path") return "Agent offline or track path not mapped";
    if (j.error) return "Upstream connectivity problem: " + j.error;
    return "Unknown playback issue";
  }} catch (e) {{
    return "Diagnostic request failed";
  }}
}}

audio.addEventListener('error', async () => {{
  const srcAtError = audio.currentSrc || "(no source)";
  console.error("[audio] error", audio.error, "src=", srcAtError);
  // Ignore transient decode/network blips if playback recovers quickly.
  await new Promise(r => setTimeout(r, 1200));
  if ((audio.currentSrc || "(no source)") !== srcAtError) return;
  if (!audio.paused && !audio.ended) return;
  if (audio.readyState >= HTMLMediaElement.HAVE_FUTURE_DATA && !audio.paused) return;

  // Tiny player: retry current track once before surfacing alert.
  const now = Date.now();
  if ((now - lastEndedAt) > 20000) lastEndedAt = 0;
  if (current >= 0 && lastEndedAt < 1) {{
    lastEndedAt = now;
    playIndex(current);
    return;
  }}

  const reason = await classifyPlaybackError(srcAtError);
  showStatus("Audio error. Could not load. Reason: " + reason, "error", 7000);
  // Debug mode: do not auto-skip on hard errors.
  // Keep the failed track visible so issues are not masked.
}});
let lastEndedAt = 0;

audio.addEventListener('ended', () => {{
  const now = Date.now();
  // iOS bogus early-ended protection (unknown duration / chunked stream weirdness)
  if (startedAt && (now - startedAt) < 8000) return;
  // Some mobile browsers emit duplicate ended events.
  if (now - lastEndedAt < 2000) return;
  lastEndedAt = now;
  playNext();
}});

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
