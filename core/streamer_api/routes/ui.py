from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
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
            "artwork_urls": t.get("artwork_urls") or [],
            "artist_image_url": ((t.get("artist_image_urls") or [""])[0] or ""),
            "artist_image_urls": t.get("artist_image_urls") or [],
            "artist_bio": t.get("artist_bio") or "",
            "album_bio": t.get("album_bio") or "",
            "genre": t.get("genre") or "",
            "year": t.get("year") or "",
            "format_family": t.get("format_family") or "",
            "codec": t.get("codec") or "",
            "bitrate_kbps": t.get("bitrate_kbps") or 0,
            "sample_rate": t.get("sample_rate") or 0,
            "bit_depth": t.get("bit_depth") or 0,
            "channels": t.get("channels") or 0,
            "metadata_quality": t.get("metadata_quality") or 0,
            "metadata_source": t.get("metadata_source") or "",
            "metadata_source_score": t.get("metadata_source_score"),
            "auto_enrich_disabled": bool(t.get("auto_enrich_disabled")),
            "is_hidden": bool(t.get("is_hidden")),
            "hidden_reason": t.get("hidden_reason") or "",
            "playability_status": t.get("playability_status") or "",
            "playability_fail_count": int(t.get("playability_fail_count") or 0),
            "playability_last_error": t.get("playability_last_error") or "",
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
  .playerbar{{position:sticky;top:0;z-index:100;background:#fff;border-bottom:1px solid #eee;padding:12px}}
  .row{{display:flex;gap:12px;align-items:center;flex-wrap:wrap}}
  .pill{{border:1px solid #ddd;border-radius:999px;padding:4px 10px}}
  .btn{{padding:6px 10px;border-radius:6px;border:1px solid #ddd;background:#fafafa;cursor:pointer}}
  .btn:hover{{filter:brightness(1.03)}}
  .danger{{background:#b91c1c;color:#fff;border:none}}
  .dot{{width:10px;height:10px;border-radius:50%;display:inline-block;vertical-align:middle;margin-right:6px;background:#bbb}}
  .dot.ok{{background:#16a34a}}
  .track-table{{border:1px solid #e5e7eb;border-radius:10px;overflow:hidden;margin-top:10px}}
  .track-head,.track-row{{display:grid;grid-template-columns:34px 1.5fr 1fr 1.2fr 120px 140px 70px 52px;gap:8px;align-items:center}}
  .track-head{{position:sticky;top:0;z-index:2;background:#f8fafc;border-bottom:1px solid #e5e7eb;padding:8px 10px}}
  .track-row{{padding:6px 10px;border-bottom:1px solid #f1f1f1}}
  .track-row:last-child{{border-bottom:none}}
  .col-sort{{background:none;border:none;color:#111;font-weight:600;cursor:pointer;padding:0}}
  .col-sort:hover{{text-decoration:underline}}
  .fav-btn{{border:none;background:transparent;cursor:pointer;font-size:16px;line-height:1;color:#9ca3af;padding:0 2px}}
  .fav-btn.on{{color:#f59e0b}}
  .track-title{{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
  .track-artist{{font-size:13px;color:#666;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
  .muted{{color:#777;font-size:13px}}
  .link-btn{{background:none;border:none;color:#2563eb;cursor:pointer;padding:0 2px;text-decoration:underline}}
  .link-btn[disabled]{{color:#9ca3af;cursor:default;text-decoration:none}}
  .now-meta{{margin-top:10px;padding:10px;border:1px solid #e5e7eb;border-radius:10px;background:#fafafa}}
  .now-meta-main{{display:grid;grid-template-columns:minmax(0,1fr) minmax(260px,600px);gap:12px;align-items:flex-start}}
  .now-meta-hero-wrap{{display:flex;justify-content:flex-end}}
  .now-meta-hero{{width:100%;max-width:600px;aspect-ratio:1 / 1;border-radius:10px;object-fit:cover;border:1px solid #ddd;background:#f3f4f6}}
  .now-meta-head{{font-size:13px;color:#666;margin-bottom:6px}}
  .now-meta-bio-title{{font-size:12px;font-weight:700;color:#555;text-transform:uppercase;letter-spacing:.02em}}
  .now-meta-bio{{font-size:13px;line-height:1.35;color:#333;white-space:pre-wrap;margin-top:2px}}
  .now-meta-stories{{margin-top:8px;display:flex;flex-direction:column;gap:10px}}
  .now-meta-gallery{{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}}
  .now-meta-thumb{{width:56px;height:56px;border-radius:6px;object-fit:cover;border:1px solid #ddd;background:#f3f4f6;cursor:pointer}}
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
  .menu-wrap{{position:relative;display:inline-block;z-index:1}}
  .menu-panel{{position:absolute;right:0;top:100%;z-index:20;min-width:190px;background:#fff;border:1px solid #ddd;border-radius:8px;padding:6px;box-shadow:0 6px 18px rgba(0,0,0,.12);display:none}}
  .menu-panel.show{{display:block}}
  .menu-item{{display:block;width:100%;text-align:left;padding:6px 8px;border:none;background:transparent;border-radius:6px;cursor:pointer}}
  .menu-item:hover{{background:#f3f4f6}}
  .hidden-panel{{margin-top:10px;padding:10px;border:1px solid #ececec;border-radius:8px;background:#fcfcfc}}
  @media (max-width: 760px) {{
    .track-head,.track-row{{grid-template-columns:30px 1.4fr 95px 118px 60px 44px}}
    .track-artist,.track-album{{display:none}}
    .filter-grid{{grid-template-columns:1fr 1fr}}
    .now-meta-main{{grid-template-columns:1fr}}
    .now-meta-hero-wrap{{justify-content:flex-start}}
    .now-meta-hero{{max-width:100%}}
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
    <label class="pill" title="Shuffle behavior">
      Mode:
      <select id="shuffleMode" style="margin-left:6px;padding:3px 6px;border:1px solid #d5d5d5;border-radius:6px">
        <option value="balanced">Balanced</option>
        <option value="discovery">Discovery</option>
        <option value="favorites">Favorites</option>
      </select>
    </label>
    <button class="btn danger" onclick="confirmClear()">Clear library</button>
    <button class="btn" onclick="resetCurrentTrackMetadata()">Reset current metadata</button>
    <button class="btn" onclick="resetAllEnrichedMetadata()">Reset all enriched metadata</button>
  </div>
  <div class="row" style="margin-top:8px">
    <!-- Visible player -->
    <audio id="player" controls preload="auto" playsinline webkit-playsinline style="width:100%"></audio>
    <!-- Hidden helper for crossfade -->
    <audio id="player2" preload="auto" playsinline webkit-playsinline style="display:none"></audio>
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
    · <button id="nowAlbumBtn" class="link-btn" type="button" onclick="goToNowPlayingAlbum()" disabled>Go to track</button>
  </div>
  <div class="now-meta" id="nowMetaPanel">
    <div class="now-meta-main">
      <div style="min-width:0;flex:1">
        <div id="nowMetaHead" class="now-meta-head">No track selected.</div>
        <div id="nowMetaGallery" class="now-meta-gallery"></div>
        <div class="now-meta-stories">
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
      <div class="now-meta-hero-wrap">
        <img id="nowHeroImg" class="now-meta-hero" src="" alt="Now playing artwork" loading="lazy" style="display:none">
      </div>
    </div>
  </div>
  <div id="statusBanner" class="status-banner"></div>
</div>

<main>
  <p>User: <b>{user_id}</b> · Library version: <b>{lib['version']}</b></p>
  <div class="row"><b>Tracks</b></div>
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
      <button class="btn danger" type="button" onclick="deleteSelectedPlaylist()">Delete playlist</button>
      <button class="btn" type="button" onclick="showSelectedPlaylist()">Show</button>
    </div>
    <div id="plSummary" class="summary"></div>
    <div class="summary">How to add tracks: 1) Create/select playlist, 2) filter library or start a track, 3) click "Add filtered tracks" or "Add current track".</div>
  </section>
  <section id="hiddenTracksPanel" class="hidden-panel" style="display:none"></section>
  <div id="albumList" style="margin-top:8px"></div>
</main>

<script>
// NOTE: This block is inside a Python f-string. JS braces are doubled {{ }}.

function libver() {{ return Math.floor(Date.now()/1000); }}
const userId = "{user_id}";
const LIBRARY_VERSION = "{lib['version']}";
const API = (window.location.pathname.startsWith("/streamer/")) ? "/streamer/api" : "/api";
const TRACKS = {tracks_json};
const TRACK_BY_ID = Object.fromEntries(TRACKS.map(t => [t.track_id, t]));
const ALBUM_STATE_KEY = "rt-album-enabled-" + userId;
const ALBUM_OPEN_KEY = "rt-album-open-" + userId;
const TRACK_STATE_KEY = "rt-track-enabled-" + userId;
const FILTER_STATE_KEY = "rt-library-filter-" + userId;
const KEEP_AWAKE_KEY = "rt-keep-awake-" + userId;
const SHUFFLE_STATE_KEY = "rt-shuffle-state-" + userId + "-" + LIBRARY_VERSION;
const FAVORITES_KEY = "rt-favorites-" + userId;
const PLAY_COUNTS_KEY = "rt-play-counts-" + userId + "-" + LIBRARY_VERSION;
const SHUFFLE_BAG_KEY = "rt-shuffle-bag-" + userId + "-" + LIBRARY_VERSION;
const SHUFFLE_MODE_KEY = "rt-shuffle-mode-" + userId;
const SORT_STATE_KEY = "rt-track-sort-" + userId;
const MOBILE_UA = /iPhone|iPad|iPod|Android|Mobile/i.test(navigator.userAgent || "");
const CROSSFADE_ENABLED = !MOBILE_UA && !!(window.AudioContext || window.webkitAudioContext);
let currentTid = null;
let lastTid = null;
let currentTrackDurationSec = 0;
let currentStartOffsetSec = 0;
let seekDragging = false;
const START_TIMEOUT_NATIVE_MS = 8000;
const START_TIMEOUT_MP3_MS = 25000;
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
let durationProbeInFlight = new Map();
let unknownDurationRecoveryTid = null;
const SHUFFLE_RECENT_WINDOW = 12;
const SHUFFLE_ARTIST_COOLDOWN = 3;
const SHUFFLE_ALBUM_COOLDOWN = 2;
const FAVORITE_WEIGHT_BALANCED = 1.6;
const FAVORITE_WEIGHT_FAVORITES = 2.2;

function savePlaybackState() {{
  try {{
    const payload = {{
      play_history: Array.isArray(playHistory) ? playHistory.slice(-500) : [],
      history_index: Number.isFinite(historyIndex) ? historyIndex : -1,
      current_tid: currentTid || null,
      last_tid: lastTid || null,
      ts: Date.now()
    }};
    localStorage.setItem(SHUFFLE_STATE_KEY, JSON.stringify(payload));
  }} catch (e) {{}}
}}

function loadPlaybackState() {{
  try {{
    const raw = localStorage.getItem(SHUFFLE_STATE_KEY);
    if (!raw) return;
    const j = JSON.parse(raw);
    if (!j || !Array.isArray(j.play_history)) return;
    const valid = j.play_history.filter(tid => !!TRACK_BY_ID[tid]);
    playHistory = valid.slice(-500);
    const idx = Number(j.history_index);
    historyIndex = (Number.isFinite(idx) && idx >= 0 && idx < playHistory.length) ? idx : (playHistory.length - 1);
    const cur = String(j.current_tid || "");
    const lst = String(j.last_tid || "");
    if (cur && TRACK_BY_ID[cur]) currentTid = cur;
    if (lst && TRACK_BY_ID[lst]) lastTid = lst;
  }} catch (e) {{}}
}}
loadPlaybackState();

function canPrefetch() {{
  if (MOBILE_UA) return false;
  const c = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
  if (!c) return true;
  if (c.saveData) return false;
  const et = String(c.effectiveType || "").toLowerCase();
  if (et.includes("2g") || et.includes("3g")) return false;
  return true;
}}

function parseDurationHeader(v) {{
  const n = Number(v);
  if (!Number.isFinite(n) || n <= 0) return 0;
  return n;
}}

async function hydrateDurationFromRelay(tid, forceMp3) {{
  if (!tid || !forceMp3) return 0;
  const t = TRACK_BY_ID[tid];
  if (!t) return 0;
  const existing = Number(t.duration_sec) || 0;
  if (existing > 0) return existing;
  if (durationProbeInFlight.has(tid)) return await durationProbeInFlight.get(tid);
  const p = (async () => {{
    try {{
      const res = await fetch(urlFor(tid, true, 0), {{ method: "HEAD", cache: "no-store" }});
      if (!res.ok) return 0;
      const d = parseDurationHeader(res.headers.get("Content-Duration"))
        || parseDurationHeader(res.headers.get("X-Content-Duration"));
      if (d > 0) {{
        t.duration_sec = d;
        if (tid === currentTid) currentTrackDurationSec = d;
        refreshSeekUi();
      }}
      return d;
    }} catch (e) {{
      return 0;
    }}
  }})();
  durationProbeInFlight.set(tid, p);
  try {{
    return await p;
  }} finally {{
    durationProbeInFlight.delete(tid);
  }}
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
function trackMenuId(tid) {{
  return "track-menu-" + String(tid || "").replace(/[^a-zA-Z0-9_-]/g, "-");
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

function sourceLabel(t) {{
  const src = String(t?.metadata_source || "").trim();
  if (!src) return "";
  const score = Number(t?.metadata_source_score);
  if (Number.isFinite(score) && score > 0 && score <= 1) {{
    return src + " (" + score.toFixed(2) + ")";
  }}
  return src;
}}

function openMetaImage(url) {{
  const u = String(url || "").trim();
  if (!u) return;
  window.open(u, "_blank");
}}
window.openMetaImage = openMetaImage;

function imageQualityScore(url) {{
  const u = String(url || "").toLowerCase();
  let s = 0;
  if (!u) return s;
  if (u.includes("front-1200")) s += 1200;
  else if (u.includes("front-1000")) s += 1000;
  else if (u.includes("front-800")) s += 800;
  else if (u.includes("front-500")) s += 500;
  else if (u.includes("front-250")) s += 250;
  else if (u.includes("/front")) s += 420;

  const wh = /\/h:(\d+)\/w:(\d+)\//.exec(u);
  if (wh) {{
    const h = Number(wh[1]) || 0;
    const w = Number(wh[2]) || 0;
    s += Math.min(h, w);
  }}
  const q = /\/q:(\d+)\//.exec(u);
  if (q) s += Math.max(0, Number(q[1]) - 40);

  if (u.includes("q:40") && u.includes("/h:150/w:150/")) s -= 180;
  return s;
}}

function upgradeImageUrl(url) {{
  let u = String(url || "").trim();
  if (!u) return u;
  // Prefer larger coverartarchive assets where possible.
  u = u.replace(/front-250(\\?|$)/i, "front-500$1");
  // Discogs proxy URLs are resizable; prefer better quality/size for hero preview.
  u = u.replace(/\\/q:40\\/h:150\\/w:150\\//i, "/q:90/h:1000/w:1000/");
  return u;
}}

function selectMetaImage(url) {{
  const u = String(url || "").trim();
  const hero = document.getElementById("nowHeroImg");
  if (!hero || !u) return;
  const candidate = upgradeImageUrl(u);
  hero.onerror = () => {{
    if (hero.src !== u) {{
      hero.onerror = null;
      hero.src = u;
      return;
    }}
    hero.style.display = "none";
  }};
  hero.src = candidate;
  hero.style.display = "";
}}
window.selectMetaImage = selectMetaImage;

function trackQualityScore(t) {{
  const family = String(t?.format_family || "").toLowerCase();
  const codec = String(t?.codec || "").toLowerCase();
  const br = Number(t?.bitrate_kbps) || 0;
  const sr = Number(t?.sample_rate) || 0;
  const bd = Number(t?.bit_depth) || 0;
  const ch = Number(t?.channels) || 0;

  let score = 0;
  if (family === "lossless") {{
    score += 60;
    if (bd > 16) score += Math.min(24, (bd - 16) * 1.5);
    if (sr > 44100) score += Math.min(20, ((sr - 44100) / 1000.0) * 0.4);
  }} else {{
    score += 20;
    score += Math.min(40, Math.max(0, br) / 8.0);
    if (codec === "opus" || codec === "aac" || codec === "m4a") score += 2;
  }}
  if (ch > 2) score += 2;
  return Math.max(0, Math.min(100, Math.round(score)));
}}

function trackQualityLabel(t) {{
  const s = trackQualityScore(t);
  if (s >= 85) return "Excellent";
  if (s >= 70) return "Good";
  if (s >= 50) return "Fair";
  return "Basic";
}}

function trackQualityDetail(t) {{
  const family = String(t?.format_family || "").toLowerCase();
  const codec = String(t?.codec || "").toUpperCase();
  const br = Number(t?.bitrate_kbps) || 0;
  const sr = Number(t?.sample_rate) || 0;
  const bd = Number(t?.bit_depth) || 0;
  const parts = [];
  if (family === "lossless") {{
    parts.push("lossless");
    if (bd > 0 || sr > 0) {{
      const b = bd > 0 ? (bd + "-bit") : "";
      const r = sr > 0 ? ((sr / 1000).toFixed(sr % 1000 === 0 ? 0 : 1) + "kHz") : "";
      parts.push([b, r].filter(Boolean).join("/"));
    }}
  }} else {{
    parts.push("lossy");
    if (br > 0) parts.push(Math.round(br) + "kbps");
  }}
  if (codec) parts.push(codec);
  return parts.join(" • ");
}}

const ALBUMS = (() => {{
  const byAlbum = new Map();
  for (const t of TRACKS.filter(t => !t.is_hidden)) {{
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
function loadFavorites() {{
  try {{
    const raw = localStorage.getItem(FAVORITES_KEY);
    const state = raw ? JSON.parse(raw) : {{}};
    if (!state || typeof state !== "object") return {{}};
    return state;
  }} catch {{
    return {{}};
  }}
}}
function saveFavorites() {{
  try {{
    localStorage.setItem(FAVORITES_KEY, JSON.stringify(favorites));
  }} catch {{}}
}}
function loadPlayCounts() {{
  try {{
    const raw = localStorage.getItem(PLAY_COUNTS_KEY);
    const state = raw ? JSON.parse(raw) : {{}};
    if (!state || typeof state !== "object") return {{}};
    return state;
  }} catch {{
    return {{}};
  }}
}}
function savePlayCounts() {{
  try {{
    localStorage.setItem(PLAY_COUNTS_KEY, JSON.stringify(playCounts));
  }} catch {{}}
}}
function loadShuffleBag() {{
  try {{
    const raw = localStorage.getItem(SHUFFLE_BAG_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(arr)) return [];
    return arr.filter(tid => !!TRACK_BY_ID[tid]);
  }} catch {{
    return [];
  }}
}}
function saveShuffleBag() {{
  try {{
    localStorage.setItem(SHUFFLE_BAG_KEY, JSON.stringify(shuffleBag.slice(-50000)));
  }} catch {{}}
}}
function loadShuffleMode() {{
  const v = String(localStorage.getItem(SHUFFLE_MODE_KEY) || "balanced");
  if (v === "balanced" || v === "discovery" || v === "favorites") return v;
  return "balanced";
}}
function saveShuffleMode(v) {{
  const mode = (v === "discovery" || v === "favorites") ? v : "balanced";
  localStorage.setItem(SHUFFLE_MODE_KEY, mode);
}}
function loadSortState() {{
  try {{
    const raw = localStorage.getItem(SORT_STATE_KEY);
    const j = raw ? JSON.parse(raw) : null;
    const field = String(j?.field || "title");
    const dir = String(j?.dir || "asc") === "desc" ? "desc" : "asc";
    return {{ field, dir }};
  }} catch {{
    return {{ field: "title", dir: "asc" }};
  }}
}}
function saveSortState() {{
  localStorage.setItem(SORT_STATE_KEY, JSON.stringify(sortState));
}}
let albumEnabled = loadAlbumEnabled();
let albumOpen = loadAlbumOpen();
let trackEnabled = loadTrackEnabled();
let favorites = loadFavorites();
let playCounts = loadPlayCounts();
let shuffleBag = loadShuffleBag();
let shuffleMode = loadShuffleMode();
let sortState = loadSortState();

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

async function resetCurrentTrackMetadata() {{
  if (!currentTid) {{
    showStatus("Start a track first, then reset current metadata.", "warn", 3000);
    return;
  }}
  if (!window.confirm("Reset enriched metadata for the current track?")) return;
  try {{
    const res = await fetch(API + "/library/" + userId + "/reset-enrichment", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{ track_ids: [currentTid], clear_overrides: true, clear_provider_snapshots: true }})
    }});
    if (!res.ok) throw new Error(await res.text());
    showStatus("Current track metadata reset. Re-scan or play to re-enrich.", "info", 4500);
    setTimeout(() => location.reload(), 300);
  }} catch (e) {{
    showStatus("Reset current failed: " + (e?.message || e), "error", 6000);
  }}
}}

async function resetAllEnrichedMetadata() {{
  if (!window.confirm("Reset enriched metadata for ALL tracks in this library?")) return;
  try {{
    const res = await fetch(API + "/library/" + userId + "/reset-enrichment", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{ clear_overrides: true, clear_provider_snapshots: false }})
    }});
    if (!res.ok) throw new Error(await res.text());
    const j = await res.json();
    showStatus("Reset complete for " + (j.changed || 0) + " tracks.", "info", 4500);
    setTimeout(() => location.reload(), 300);
  }} catch (e) {{
    showStatus("Reset all failed: " + (e?.message || e), "error", 6000);
  }}
}}

async function findBetterMatch(trackId) {{
  try {{
    const res = await fetch(API + "/metadata/enrich/" + userId + "/" + trackId, {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{
        apply: true,
        providers: ["acoustid"],
        min_score: 0.95
      }})
    }});
    if (!res.ok) throw new Error(await res.text());
    const j = await res.json();
    if (j.applied) {{
      showStatus("Applied better match for track.", "info", 3500);
      setTimeout(() => location.reload(), 250);
      return;
    }}
    showStatus("No safe AcoustID match found.", "warn", 3500);
  }} catch (e) {{
    showStatus("Find match failed: " + (e?.message || e), "error", 4500);
  }}
}}
window.findBetterMatch = findBetterMatch;

async function rejectTrackMatch(trackId) {{
  if (!window.confirm("Reset enriched metadata for this track?")) return;
  try {{
    const res = await fetch(API + "/library/" + userId + "/reset-enrichment", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{
        track_ids: [trackId],
        clear_overrides: true,
        clear_provider_snapshots: true
      }})
    }});
    if (!res.ok) throw new Error(await res.text());
    showStatus("Track metadata reset.", "info", 3500);
    setTimeout(() => location.reload(), 250);
  }} catch (e) {{
    showStatus("Reject failed: " + (e?.message || e), "error", 4500);
  }}
}}
window.rejectTrackMatch = rejectTrackMatch;

async function rejectAlbumMatch(albumKey) {{
  const sample = TRACKS.find(t => albumKeyOf(t.album || "Unknown Album") === albumKey);
  if (!sample) {{
    showStatus("No tracks found for that album.", "warn", 3000);
    return;
  }}
  const tids = TRACKS
    .filter(t => albumKeyOf(t.album || "Unknown Album") === albumKey)
    .map(t => t.track_id)
    .filter(Boolean);
  if (!window.confirm("Reset enriched metadata for this entire album (" + tids.length + " tracks)?")) return;
  try {{
    const res = await fetch(API + "/library/" + userId + "/reset-enrichment-album", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{
        album: sample.album || "Unknown Album",
        artist: sample.artist || "",
        clear_overrides: true,
        clear_provider_snapshots: true
      }})
    }});
    if (!res.ok) throw new Error(await res.text());
    const j = await res.json();
    showStatus("Album metadata reset for " + (j.changed || 0) + " tracks.", "info", 4000);
    setTimeout(() => location.reload(), 200);
  }} catch (e) {{
    showStatus("Album reject failed: " + (e?.message || e), "error", 4500);
  }}
}}
window.rejectAlbumMatch = rejectAlbumMatch;

async function toggleNeverAutoEnrich(trackId, lockedNow) {{
  const enable = !!lockedNow; // if currently locked, this click re-enables auto
  const prompt = enable
    ? "Re-enable auto-enrichment for this track?"
    : "Disable auto-enrichment for this track?";
  if (!window.confirm(prompt)) return;
  try {{
    const res = await fetch(API + "/library/" + userId + "/track/" + trackId + "/auto-enrich", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{ enabled: enable }})
    }});
    if (!res.ok) throw new Error(await res.text());
    showStatus(enable ? "Auto-enrichment enabled for track." : "Auto-enrichment disabled for track.", "info", 3500);
    setTimeout(() => location.reload(), 250);
  }} catch (e) {{
    showStatus("Auto-enrich toggle failed: " + (e?.message || e), "error", 4500);
  }}
}}
window.toggleNeverAutoEnrich = toggleNeverAutoEnrich;

function closeTrackMenus() {{
  document.querySelectorAll(".menu-panel.show").forEach(el => el.classList.remove("show"));
}}
window.closeTrackMenus = closeTrackMenus;

function toggleTrackMenu(trackId, ev) {{
  if (ev) ev.stopPropagation();
  const id = trackMenuId(trackId);
  const panel = document.getElementById(id);
  if (!panel) return;
  const wasOpen = panel.classList.contains("show");
  closeTrackMenus();
  if (!wasOpen) panel.classList.add("show");
}}
window.toggleTrackMenu = toggleTrackMenu;

async function hideTrack(trackId) {{
  if (!window.confirm("Hide this track from main library list?")) return;
  try {{
    const res = await fetch(API + "/library/" + userId + "/track/" + trackId + "/hide", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{ hidden: true, reason: "user_hidden" }})
    }});
    if (!res.ok) throw new Error(await res.text());
    showStatus("Track hidden.", "info", 2500);
    setTimeout(() => location.reload(), 200);
  }} catch (e) {{
    showStatus("Hide failed: " + (e?.message || e), "error", 4500);
  }}
}}
window.hideTrack = hideTrack;

async function restoreTrack(trackId) {{
  try {{
    const res = await fetch(API + "/library/" + userId + "/track/" + trackId + "/hide", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{ hidden: false }})
    }});
    if (!res.ok) throw new Error(await res.text());
    showStatus("Track restored.", "info", 2500);
    setTimeout(() => location.reload(), 200);
  }} catch (e) {{
    showStatus("Restore failed: " + (e?.message || e), "error", 4500);
  }}
}}
window.restoreTrack = restoreTrack;

async function removeTrack(trackId) {{
  if (!window.confirm("Remove this track from library? It can return on future rescans.")) return;
  try {{
    const res = await fetch(API + "/library/" + userId + "/track/" + trackId, {{ method: "DELETE" }});
    if (!res.ok) throw new Error(await res.text());
    showStatus("Track removed.", "info", 2500);
    setTimeout(() => location.reload(), 200);
  }} catch (e) {{
    showStatus("Remove failed: " + (e?.message || e), "error", 4500);
  }}
}}
window.removeTrack = removeTrack;

function renderHiddenTracks() {{
  const panel = document.getElementById("hiddenTracksPanel");
  if (!panel) return;
  const hidden = TRACKS.filter(t => !!t.is_hidden);
  if (!hidden.length) {{
    panel.style.display = "none";
    panel.innerHTML = "";
    return;
  }}
  panel.style.display = "";
  const rows = hidden.slice(0, 200).map(t =>
    '<div class="row" style="justify-content:space-between;border-bottom:1px solid #eee;padding:6px 0">' +
      '<div style="min-width:0"><b>' + escapeHtml(t.title) + '</b> · ' + escapeHtml(t.artist) + ' <span class="muted">(' + escapeHtml(t.hidden_reason || "hidden") + ')</span></div>' +
      '<div style="display:flex;gap:6px;flex-wrap:wrap">' +
        '<button class="btn" type="button" onclick="restoreTrack(\\'' + escapeHtml(t.track_id) + '\\')">Restore</button>' +
        '<button class="btn danger" type="button" onclick="removeTrack(\\'' + escapeHtml(t.track_id) + '\\')">Remove</button>' +
      '</div>' +
    '</div>'
  ).join("");
  panel.innerHTML = '<b>Hidden tracks (' + hidden.length + ')</b>' + rows;
}}
window.renderHiddenTracks = renderHiddenTracks;

function renderAlbumList() {{
  const root = document.getElementById("albumList");
  if (!root) return;
  const albums = filteredAlbums();
  const tracks = albums.flatMap(a => a.tracks);
  if (tracks.length === 0) {{
    root.innerHTML = "<p class=\\"muted\\">No matching albums/tracks for current filters.</p>";
    updateLibrarySummary(albums);
    renderHiddenTracks();
    return;
  }}
  const sortDir = sortState.dir === "desc" ? "↓" : "↑";
  const sortMark = (f) => (sortState.field === f ? (" " + sortDir) : "");
  const sorted = sortTracks(tracks);
  const rows = sorted.map(t => {{
    const src = sourceLabel(t);
    const locked = !!t.auto_enrich_disabled;
    const lockLabel = locked ? "Auto-enrich off" : "Never auto-enrich";
    const isFav = !!favorites[t.track_id];
    const favMenuLabel = isFav ? "Unfavorite" : "Favorite";
    const qScore = trackQualityScore(t);
    const qLabel = trackQualityLabel(t);
    const qDetail = trackQualityDetail(t);
    const healthBad = !isTrackPlayable(t);
    const healthLabel = healthBad ? " · Unplayable" : "";
    const dur = formatDuration(t.duration_sec);
    return (
      "<div class=\\"track-row\\" id=\\"track-" + t.track_id + "\\">" +
        "<div><button class=\\"fav-btn " + (isFav ? "on" : "") + "\\" type=\\"button\\" title=\\"" + (isFav ? "Unfavorite" : "Favorite") + "\\" onclick=\\"toggleFavoriteTrack('" + t.track_id + "')\\">★</button></div>" +
        "<div class=\\"track-title\\" title=\\"" + escapeHtml(t.title) + "\\">" + escapeHtml(t.title) + "</div>" +
        "<div class=\\"track-artist\\" title=\\"" + escapeHtml(t.artist) + "\\">" + escapeHtml(t.artist) + (src ? " · " + escapeHtml(src) : "") + "</div>" +
        "<div class=\\"track-album\\" title=\\"" + escapeHtml(t.album || "Unknown Album") + "\\">" + escapeHtml(t.album || "Unknown Album") + "</div>" +
        "<div class=\\"muted\\" title=\\"" + escapeHtml("Q" + qScore + " · " + qLabel + " · " + qDetail + healthLabel) + "\\">" + dur + " · Q" + qScore + " " + escapeHtml(qLabel) + (healthBad ? " · ⚠" : "") + "</div>" +
        "<label class=\\"pill\\"><input type=\\"checkbox\\" " + (trackEnabled[t.track_id] ? "checked" : "") + " onchange=\\"toggleTrackEnabled('" + t.track_id + "', this.checked)\\"> In Shuffle</label>" +
        "<div><button class=\\"btn\\" type=\\"button\\" onclick=\\"playById('" + t.track_id + "')\\">Play</button></div>" +
        "<div style=\\"display:flex;justify-content:flex-end\\">" +
          "<div class=\\"menu-wrap\\">" +
            "<button class=\\"btn\\" type=\\"button\\" onclick=\\"toggleTrackMenu('" + t.track_id + "', event)\\">⋯</button>" +
            "<div class=\\"menu-panel\\" id=\\"" + trackMenuId(t.track_id) + "\\">" +
              "<button class=\\"menu-item\\" type=\\"button\\" onclick=\\"findBetterMatch('" + t.track_id + "'); closeTrackMenus();\\">Find next match</button>" +
              "<button class=\\"menu-item\\" type=\\"button\\" onclick=\\"rejectTrackMatch('" + t.track_id + "'); closeTrackMenus();\\">Reject match</button>" +
              "<button class=\\"menu-item\\" type=\\"button\\" onclick=\\"toggleFavoriteTrack('" + t.track_id + "'); closeTrackMenus();\\">" + favMenuLabel + "</button>" +
              "<button class=\\"menu-item\\" type=\\"button\\" onclick=\\"toggleNeverAutoEnrich('" + t.track_id + "', " + (locked ? "true" : "false") + "); closeTrackMenus();\\">" + lockLabel + "</button>" +
              "<button class=\\"menu-item\\" type=\\"button\\" onclick=\\"hideTrack('" + t.track_id + "'); closeTrackMenus();\\">Hide track</button>" +
              "<button class=\\"menu-item\\" type=\\"button\\" onclick=\\"removeTrack('" + t.track_id + "'); closeTrackMenus();\\">Remove track</button>" +
            "</div>" +
          "</div>" +
        "</div>" +
      "</div>"
    );
  }}).join("");
  root.innerHTML =
    "<section class=\\"track-table\\">" +
      "<div class=\\"track-head\\">" +
        "<button class=\\"col-sort\\" type=\\"button\\" title=\\"Sort by favorite\\" onclick=\\"toggleSort('favorite')\\">★" + sortMark("favorite") + "</button>" +
        "<button class=\\"col-sort\\" type=\\"button\\" onclick=\\"toggleSort('title')\\">Title" + sortMark("title") + "</button>" +
        "<button class=\\"col-sort\\" type=\\"button\\" onclick=\\"toggleSort('artist')\\">Artist" + sortMark("artist") + "</button>" +
        "<button class=\\"col-sort\\" type=\\"button\\" onclick=\\"toggleSort('album')\\">Album" + sortMark("album") + "</button>" +
        "<button class=\\"col-sort\\" type=\\"button\\" onclick=\\"toggleSort('quality')\\">Quality" + sortMark("quality") + "</button>" +
        "<span class=\\"muted\\">Shuffle</span>" +
        "<span class=\\"muted\\">Play</span>" +
        "<span class=\\"muted\\">⋯</span>" +
      "</div>" +
      rows +
    "</section>";
  updateLibrarySummary(albums);
  renderHiddenTracks();
}}

function toggleSort(field) {{
  const f = String(field || "title");
  if (sortState.field === f) sortState.dir = (sortState.dir === "asc" ? "desc" : "asc");
  else {{
    sortState.field = f;
    sortState.dir = (f === "favorite") ? "desc" : "asc";
  }}
  saveSortState();
  renderAlbumList();
}}
window.toggleSort = toggleSort;

function sortTracks(tracks) {{
  const dir = (sortState.dir === "desc") ? -1 : 1;
  const field = String(sortState.field || "title");
  const a = (tracks || []).slice();
  const num = (v) => Number(v || 0);
  a.sort((x, y) => {{
    let cmp = 0;
    if (field === "favorite") {{
      cmp = (favorites[y.track_id] ? 1 : 0) - (favorites[x.track_id] ? 1 : 0);
    }} else if (field === "quality") {{
      cmp = num(trackQualityScore(x)) - num(trackQualityScore(y));
    }} else if (field === "duration") {{
      cmp = num(x.duration_sec) - num(y.duration_sec);
    }} else if (field === "artist") {{
      cmp = String(x.artist || "").localeCompare(String(y.artist || ""));
    }} else if (field === "album") {{
      cmp = String(x.album || "").localeCompare(String(y.album || ""));
    }} else {{
      cmp = String(x.title || "").localeCompare(String(y.title || ""));
    }}
    if (cmp === 0) cmp = String(x.title || "").localeCompare(String(y.title || ""));
    return cmp * dir;
  }});
  return a;
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
    const subset = a.tracks.filter(_trackMatchesFilters).filter(t => !t.is_hidden);
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
  const hiddenCount = TRACKS.filter(t => !!t.is_hidden).length;
  el.textContent = "Showing " + albumCount + " albums · " + trackCount + " tracks" + (hiddenCount ? (" · hidden: " + hiddenCount) : "");
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

async function deleteSelectedPlaylist() {{
  const sel = document.getElementById("plSelect");
  const playlistId = sel ? sel.value : "";
  if (!playlistId) {{
    showStatus("Select a playlist first.", "warn", 2500);
    return;
  }}
  const p = playlistsCache.find(x => x.playlist_id === playlistId);
  const name = p?.name || "selected playlist";
  if (!window.confirm("Delete playlist '" + name + "'? This cannot be undone.")) return;
  try {{
    const res = await fetch(API + "/playlists/" + userId + "/" + playlistId, {{
      method: "DELETE",
    }});
    if (!res.ok) throw new Error(await res.text());
    showStatus("Deleted playlist: " + name, "info", 3000);
    if (sel) sel.value = "";
    await refreshPlaylists();
    updatePlaylistSummary("Playlist deleted.");
  }} catch (e) {{
    showStatus("Delete playlist failed: " + (e?.message || e), "error", 4500);
  }}
}}
window.deleteSelectedPlaylist = deleteSelectedPlaylist;

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

function toggleFavoriteTrack(tid) {{
  const key = String(tid || "");
  if (!key) return;
  if (favorites[key]) delete favorites[key];
  else favorites[key] = true;
  saveFavorites();
  renderAlbumList();
  showStatus(favorites[key] ? "Favorited." : "Unfavorited.", "info", 1800);
}}
window.toggleFavoriteTrack = toggleFavoriteTrack;

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

function updateNowAlbumLink(trackId) {{
  const btn = document.getElementById("nowAlbumBtn");
  if (!btn) return;
  const tid = String(trackId || "");
  const known = !!TRACK_BY_ID[tid];
  if (!known) {{
    btn.disabled = true;
    btn.textContent = "Go to track";
    btn.dataset.trackId = "";
    return;
  }}
  btn.disabled = false;
  btn.textContent = "Go to track";
  btn.dataset.trackId = tid;
}}

function goToNowPlayingAlbum() {{
  const btn = document.getElementById("nowAlbumBtn");
  const tid = btn && btn.dataset ? btn.dataset.trackId : "";
  if (!tid) return;
  const row = document.getElementById("track-" + tid);
  if (!row) return;
  const topPanel = document.querySelector(".playerbar");
  const metaPanel = document.getElementById("nowMetaPanel");
  const topH = topPanel ? topPanel.getBoundingClientRect().height : 0;
  const metaH = metaPanel ? metaPanel.getBoundingClientRect().height : 0;
  const extra = Math.max(16, Math.min(64, metaH * 0.08));
  const y = window.scrollY + row.getBoundingClientRect().top - topH - extra;
  window.scrollTo({{ top: Math.max(0, y), behavior: "smooth" }});
  row.style.outline = "2px solid #93c5fd";
  setTimeout(() => {{ row.style.outline = ""; }}, 1200);
}}
window.goToNowPlayingAlbum = goToNowPlayingAlbum;

document.addEventListener('DOMContentLoaded', () => {{
  bindFilterUi();
  renderAlbumList();
  refreshPlaylists();
  const sm = document.getElementById("shuffleMode");
  if (sm) {{
    sm.value = shuffleMode;
    sm.addEventListener("change", () => {{
      shuffleMode = String(sm.value || "balanced");
      saveShuffleMode(shuffleMode);
      showStatus("Shuffle mode: " + shuffleMode, "info", 1600);
    }});
  }}
  document.addEventListener("click", () => closeTrackMenus());
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
  [a1, a2].forEach((el) => {{
    if (!el) return;
    try {{
      el.setAttribute("playsinline", "");
      el.setAttribute("webkit-playsinline", "");
    }} catch (e) {{}}
  }});
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
  const knownTrack = TRACK_BY_ID[tid] || null;
  currentTrackDurationSec = Number(m.duration_sec) || Number(knownTrack && knownTrack.duration_sec) || 0;
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
  updateNowAlbumLink(tid);
  refreshSeekUi();
}}

function renderNowMeta(m) {{
  const hero = document.getElementById("nowHeroImg");
  const head = document.getElementById("nowMetaHead");
  const artistBio = document.getElementById("nowArtistBio");
  const albumBio = document.getElementById("nowAlbumBio");
  const gallery = document.getElementById("nowMetaGallery");
  if (!hero || !head || !artistBio || !albumBio || !gallery) return;

  const artwork = String(m?.artist_image_url || m?.artwork_url || "").trim();
  if (artwork) {{
    hero.src = upgradeImageUrl(artwork);
    hero.style.display = "";
    hero.onerror = () => {{ hero.style.display = "none"; }};
  }} else {{
    hero.style.display = "none";
    hero.removeAttribute("src");
  }}

  const bits = [];
  if (m?.artist) bits.push(m.artist);
  if (m?.album) bits.push(m.album);
  if (m?.year) bits.push(String(m.year));
  if (m?.genre) bits.push(String(m.genre));
  const src = sourceLabel(m);
  if (src) bits.push(src);
  head.textContent = bits.length ? bits.join(" · ") : "No track selected.";

  const artistTxt = String(m?.artist_bio || "").trim();
  const albumTxt = String(m?.album_bio || "").trim();
  artistBio.textContent = artistTxt || "No artist story yet.";
  albumBio.textContent = albumTxt || "No album story yet.";

  const urls = [];
  const pushUnique = (u) => {{
    const s = String(u || "").trim();
    if (!s) return;
    if (!/^https?:\/\//i.test(s)) return;
    if (!urls.includes(s)) urls.push(s);
  }};
  pushUnique(m?.artist_image_url);
  pushUnique(m?.artwork_url);
  for (const u of (m?.artist_image_urls || [])) pushUnique(u);
  for (const u of (m?.artwork_urls || [])) pushUnique(u);
  urls.sort((a, b) => imageQualityScore(b) - imageQualityScore(a));
  if (urls.length) selectMetaImage(urls[0]);
  const thumbs = urls.slice(0, 12).map(u =>
    '<img class="now-meta-thumb" src="' + escapeHtml(u) + '" data-url="' + escapeHtml(u) + '" loading="lazy" alt="More image" title="Preview image" onclick="selectMetaImage(this.dataset.url)" ondblclick="openMetaImage(this.dataset.url)">'
  ).join("");
  gallery.innerHTML = thumbs;
}}
function urlFor(tid, forceMp3, startSec = 0) {{
  const base = forceMp3 ? "/relay-mp3/" : "/relay/";
  let u = API + base + userId + "/" + tid + "?v=" + libver();
  if (forceMp3 && startSec > 0) u += "&start=" + encodeURIComponent(String(startSec));
  return u;
}}

function isTrackPlayable(t) {{
  if (!t) return false;
  const st = String(t.playability_status || "").toLowerCase();
  const fails = Number(t.playability_fail_count || 0);
  if (st === "bad") return false;
  if (fails >= 3) return false;
  return true;
}}

function orderedTracks() {{
  return ALBUMS.flatMap(a => a.tracks).filter(t => !t.is_hidden && isTrackPlayable(t));
}}

function shuffleEligibleTracks() {{
  return TRACKS.filter(
    t => !t.is_hidden && isTrackPlayable(t) && !!trackEnabled[t.track_id]
  );
}}

function trackArtistKey(t) {{
  return _normText(t && t.artist);
}}
function trackAlbumKey(t) {{
  return _normText(t && t.album);
}}
function shuffleFavoriteWeight() {{
  if (shuffleMode === "favorites") return FAVORITE_WEIGHT_FAVORITES;
  if (shuffleMode === "discovery") return 1.0;
  return FAVORITE_WEIGHT_BALANCED;
}}
function trackShuffleWeight(t) {{
  const tid = t && t.track_id;
  if (!tid) return 1.0;
  const plays = Math.max(0, Number(playCounts[tid] || 0));
  const novelty = 1 / Math.sqrt(plays + 1); // 1.0 new -> decays smoothly
  const noveltyWeight = 0.65 + (1.35 * novelty);
  const favWeight = favorites[tid] ? shuffleFavoriteWeight() : 1.0;
  return Math.max(0.0001, noveltyWeight * favWeight);
}}
function pickWeightedTrackId(candidates) {{
  if (!candidates || candidates.length === 0) return null;
  let total = 0;
  const weighted = candidates.map(t => {{
    const w = trackShuffleWeight(t);
    total += w;
    return {{ t, w }};
  }});
  if (!(total > 0)) return candidates[Math.floor(Math.random() * candidates.length)].track_id;
  let r = Math.random() * total;
  for (const it of weighted) {{
    r -= it.w;
    if (r <= 0) return it.t.track_id;
  }}
  return weighted[weighted.length - 1].t.track_id;
}}
function ensureShuffleBag(list) {{
  const activeIds = new Set((list || []).map(t => t.track_id));
  shuffleBag = shuffleBag.filter(tid => activeIds.has(tid));
  if (shuffleBag.length === 0) {{
    shuffleBag = (list || []).map(t => t.track_id);
    // Fisher-Yates
    for (let i = shuffleBag.length - 1; i > 0; i--) {{
      const j = Math.floor(Math.random() * (i + 1));
      const tmp = shuffleBag[i];
      shuffleBag[i] = shuffleBag[j];
      shuffleBag[j] = tmp;
    }}
  }}
  saveShuffleBag();
}}
function pickShuffleTrackIdNoRecent(list) {{
  if (!list || list.length === 0) return null;
  if (list.length === 1) return list[0].track_id;

  ensureShuffleBag(list);
  const idSet = new Set(list.map(t => t.track_id));
  const byId = Object.fromEntries(list.map(t => [t.track_id, t]));
  const windowSize = Math.max(1, Math.min(SHUFFLE_RECENT_WINDOW, Math.floor(list.length * 0.5)));
  const blockedTracks = new Set();
  const blockedArtists = new Set();
  const blockedAlbums = new Set();
  if (currentTid) blockedTracks.add(currentTid);

  let artistSlots = SHUFFLE_ARTIST_COOLDOWN;
  let albumSlots = SHUFFLE_ALBUM_COOLDOWN;
  for (let i = playHistory.length - 1; i >= 0 && blockedTracks.size < (windowSize + 1); i--) {{
    const tid = playHistory[i];
    if (!tid || !idSet.has(tid)) continue;
    const tr = byId[tid];
    blockedTracks.add(tid);
    if (tr && artistSlots > 0) {{
      const ak = trackArtistKey(tr);
      if (ak) blockedArtists.add(ak);
      artistSlots -= 1;
    }}
    if (tr && albumSlots > 0) {{
      const bk = trackAlbumKey(tr);
      if (bk) blockedAlbums.add(bk);
      albumSlots -= 1;
    }}
  }}

  const bagSet = new Set(shuffleBag);
  const pickFrom = (sourceList, relaxArtist, relaxAlbum, relaxTrackWindow) => sourceList.filter(t => {{
    if (!t) return false;
    if (!relaxTrackWindow && blockedTracks.has(t.track_id)) return false;
    if (!relaxArtist) {{
      const ak = trackArtistKey(t);
      if (ak && blockedArtists.has(ak)) return false;
    }}
    if (!relaxAlbum) {{
      const bk = trackAlbumKey(t);
      if (bk && blockedAlbums.has(bk)) return false;
    }}
    return true;
  }});

  const bagTracks = list.filter(t => bagSet.has(t.track_id));
  let candidates = pickFrom(bagTracks, false, false, false);
  if (!candidates.length) candidates = pickFrom(bagTracks, false, true, false);
  if (!candidates.length) candidates = pickFrom(bagTracks, true, true, false);
  if (!candidates.length) candidates = pickFrom(list, false, false, false);
  if (!candidates.length) candidates = pickFrom(list, false, true, true);
  if (!candidates.length) candidates = list.slice();

  const chosen = pickWeightedTrackId(candidates);
  if (chosen) {{
    shuffleBag = shuffleBag.filter(tid => tid !== chosen);
    saveShuffleBag();
  }}
  return chosen;
}}

function currentPlaybackSec() {{
  const active = (ACTIVE === 2 ? a2 : a1);
  return Math.max(0, currentStartOffsetSec + (Number(active.currentTime) || 0));
}}

function noteProgress() {{
  lastProgressWallMs = Date.now();
  lastProgressPlaybackSec = currentPlaybackSec();
}}
function noteTrackPlay(tid) {{
  const key = String(tid || "");
  if (!key) return;
  const n = Math.max(0, Number(playCounts[key] || 0)) + 1;
  playCounts[key] = n;
  if (shuffleBag.includes(key)) {{
    shuffleBag = shuffleBag.filter(x => x !== key);
    saveShuffleBag();
  }}
  savePlayCounts();
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
    unknownDurationRecoveryTid = null;
  }}
  const m = metaFor(tid);
  if ((Number(m.duration_sec) || 0) <= 0 && !!m.force_mp3) {{
    await hydrateDurationFromRelay(tid, true);
  }}
  currentStartOffsetSec = Math.max(0, Number(startSec) || 0);
  let started = false;
  let triedMp3Fallback = false;
  const primaryForceMp3 = !!m.force_mp3;
  const startModes = primaryForceMp3 ? [true, false] : [false, true];
  for (const forceMp3 of startModes) {{
    const url = urlFor(tid, forceMp3, currentStartOffsetSec);
    a1.src = url; a1.load();
    try {{ await waitForCanPlay(a1, forceMp3 ? START_TIMEOUT_MP3_MS : START_TIMEOUT_NATIVE_MS); }} catch {{}}
    try {{
      await a1.play();
      started = true;
      if (forceMp3 && !primaryForceMp3) {{
        // Stabilize this track for the rest of the session if native relay was flaky.
        m.force_mp3 = true;
      }}
      break;
    }} catch {{}}
    if (!forceMp3) triedMp3Fallback = true;
  }}
  if (!started) {{
    // One extra rescue attempt: warm cached MP3 path, then retry once.
    try {{
      const warmUrl = urlFor(tid, true, currentStartOffsetSec);
      await fetch(warmUrl, {{ method: "HEAD", cache: "no-store" }});
      a1.src = warmUrl; a1.load();
      await waitForCanPlay(a1, START_TIMEOUT_MP3_MS);
      await a1.play();
      started = true;
      m.force_mp3 = true;
    }} catch {{}}
  }}
  if (!started) {{
    let reason = "";
    try {{
      reason = await classifyPlaybackError(a1.currentSrc || urlFor(tid, true, currentStartOffsetSec));
    }} catch {{}}
    showStatus(
      triedMp3Fallback
        ? ("Audio failed on native and MP3 relay for this track." + (reason ? (" Reason: " + reason) : ""))
        : ("Audio failed to start for this track." + (reason ? (" Reason: " + reason) : "")),
      "error",
      8000
    );
    return false;
  }}
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
      noteTrackPlay(tid);
    }}
  }}
  noteProgress();
  savePlaybackState();
  return true;
}}
window.playById = (tid) => {{
  // Explicit row "Play" must always honor the selected track.
  // Shuffle only affects automatic/next selection, not direct picks.
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
  try {{ await waitForCanPlay(helper, m && m.force_mp3 ? START_TIMEOUT_MP3_MS : START_TIMEOUT_NATIVE_MS); }} catch (e) {{ console.warn("[xfade] helper timeout:", e); await playIdAt(tid, 0, {{ recordHistory }}); return; }}
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
      noteTrackPlay(tid);
    }}
  }}
  noteProgress();
  savePlaybackState();

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
      try {{ await waitForCanPlay(a1, START_TIMEOUT_NATIVE_MS); }} catch {{}}
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
    return pickShuffleTrackIdNoRecent(list);
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
async function playNext() {{
  const shuffleOn = !!(document.getElementById('shuffle') && document.getElementById('shuffle').checked);
  let tid = null;
  let fromHistory = false;
  if (historyIndex >= 0 && historyIndex < (playHistory.length - 1)) {{
    historyIndex += 1;
    tid = playHistory[historyIndex];
    fromHistory = true;
    savePlaybackState();
  }} else {{
    tid = pickNextTrackId();
  }}
  if (!tid) {{
    showStatus(shuffleOn ? "No tracks in selected albums/tracks for shuffle." : "No tracks available.", "warn");
    return;
  }}
  const useX = CROSSFADE_ENABLED && document.getElementById('xfade') && document.getElementById('xfade').checked;
  if (useX) {{
    await xfadeTo(tid, {{ recordHistory: !fromHistory }});
  }} else {{
    await playIdAt(tid, 0, {{ recordHistory: !fromHistory }});
  }}
}}
function playPrev() {{
  let tid = null;
  let fromHistory = false;
  if (historyIndex > 0) {{
    historyIndex -= 1;
    tid = playHistory[historyIndex];
    fromHistory = true;
    savePlaybackState();
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
  const nativeDur = Number(a1.duration);
  const nativePos = Number(a1.currentTime) || 0;
  const expectedDur = Math.max(0, Number(currentTrackDurationSec) || 0);
  const expectedPos = currentPlaybackSec();
  const endedNearNativeTail = Number.isFinite(nativeDur) && nativeDur > 0 && nativePos >= (nativeDur - 1.5);
  const hasExpectedDur = expectedDur > 0;
  const endedNearExpectedTail = hasExpectedDur && expectedPos >= (expectedDur - 1.5);
  // Prefer metadata tail check; if metadata duration is missing, fall back to native media tail.
  const endedNearTail = endedNearExpectedTail || (!hasExpectedDur && endedNearNativeTail);
  if (!endedNearTail) {{
    if (await recoverCurrentTrack("Stream ended early", {{ minPos: 5, tailGuard: 12, jumpSec: 2, timeoutMs: 3200 }})) return;
    showStatus("Stream ended before track tail. Staying on current track.", "warn", 3500);
    return;
  }}

  if (!autoplay) return;

  // iOS bogus early-ended protection (common with chunked/live transcodes)
  const dur = expectedDur;
  const pos = expectedPos;
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
@router.get("/user/{user_id}/play-mobile")
def play_mobile(user_id: str, request: Request):
    # Dedicated mobile entrypoint: always serve stripped tiny player.
    return radio_page(user_id, request)


@router.get("/radio/{user_id}", response_class=HTMLResponse)
def radio_page(user_id: str, request: Request):
    # Guardrail: old mobile bookmarks to /radio can reintroduce legacy playback issues.
    # Redirect only mobile UA to hardened mobile player; keep desktop /radio unchanged.
    ua = str(request.headers.get("user-agent") or "").lower()
    is_mobile = any(tok in ua for tok in ("iphone", "ipad", "ipod", "android", "mobile"))
    path = str(request.url.path or "")
    is_mobile_entry = path.endswith(f"/user/{user_id}/play-mobile")
    force_tiny = str(request.query_params.get("tiny") or "").strip().lower() in {"1", "true", "yes", "on"}
    if is_mobile and not force_tiny and not is_mobile_entry:
        base = "/streamer/api" if path.startswith("/streamer/") else "/api"
        return RedirectResponse(url=f"{base}/user/{user_id}/play-mobile", status_code=307)

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
  .now-media{{margin-top:10px;display:flex;gap:10px;align-items:flex-start}}
  .now-img{{width:88px;height:88px;border-radius:8px;object-fit:cover;border:1px solid #ddd;background:#f3f4f6;display:none}}
  .now-meta{{font-size:13px;color:#666;line-height:1.35}}
</style>
</head>
<body>
<div class="bar">
  <div class="row">
    <span id="stDot" class="dot"></span>
    <strong>{user_id} — Radio</strong>
    <button class="btn" id="btnPrev" type="button">⏮️ Prev</button>
    <button class="btn" id="btnNext" type="button">▶️ Next</button>
  </div>
  <div class="row" style="margin-top:8px">
    <!-- IMPORTANT: playsinline + preload="auto" -->
    <audio id="player" controls preload="auto" playsinline style="width:100%"></audio>
  </div>
  <div class="now"><b>Now Playing:</b> <span id="now">—</span></div>
  <div class="now-media">
    <img id="nowImg" class="now-img" alt="Now playing artwork" loading="lazy">
    <div id="nowMeta" class="now-meta"></div>
  </div>
  <div class="hint">Tip: press ▶️ or “Next”. The dot is green when your agent is online.</div>
  <div id="statusBanner" class="status-banner"></div>
</div>

<script>
// NOTE: Inside a Python f-string — ALL braces are doubled {{ }}.

function libver() {{ return Math.floor(Date.now()/1000); }}
const userId = "{user_id}";
let TRACKS = [];

// Robust API base: if path contains /streamer/ anywhere, use /streamer/api
const API = "/streamer/api";
const TINY_MOBILE_UA = /iPhone|iPad|iPod|Android|Mobile/i.test(navigator.userAgent || "");
const TINY_SHUFFLE_RECENT_WINDOW = 10;
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
let tinyTracksLoadPromise = null;
let tinyTrackDetailInFlight = new Map();

async function loadTinyTracks() {{
  if (tinyTracksLoadPromise) return tinyTracksLoadPromise;
  tinyTracksLoadPromise = (async () => {{
    const res = await fetch(API + "/mobile/bootstrap/" + encodeURIComponent(userId) + "?v=" + libver(), {{ cache: "no-store" }});
    if (!res.ok) throw new Error("Bootstrap HTTP " + res.status);
    const j = await res.json();
    const incoming = Array.isArray(j?.tracks) ? j.tracks : [];
    TRACKS = incoming.map(t => {{
      return {{
        track_id: t?.track_id || "",
        playability_status: t?.playability_status || "",
        playability_fail_count: Number(t?.playability_fail_count || 0),
        playability_last_error: t?.playability_last_error || "",
      }};
    }}).filter(t => !!t.track_id);
    return TRACKS.length;
  }})().catch(err => {{
    tinyTracksLoadPromise = null;
    throw err;
  }});
  return tinyTracksLoadPromise;
}}
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

async function loadTinyTrackDetail(trackId) {{
  const tid = String(trackId || "").trim();
  if (!tid) return null;
  if (tinyTrackDetailInFlight.has(tid)) return tinyTrackDetailInFlight.get(tid);
  const p = (async () => {{
    const res = await fetch(API + "/mobile/track/" + encodeURIComponent(userId) + "/" + encodeURIComponent(tid) + "?v=" + libver(), {{ cache: "no-store" }});
    if (!res.ok) throw new Error("Track detail HTTP " + res.status);
    return await res.json();
  }})();
  tinyTrackDetailInFlight.set(tid, p);
  try {{
    return await p;
  }} finally {{
    tinyTrackDetailInFlight.delete(tid);
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
let tinyRecentTrackIds = [];
let tinyPlayHistory = [];
let tinyHistoryIndex = -1;
function tinyIsPlayable(t) {{
  if (!t) return false;
  const st = String(t.playability_status || "").toLowerCase();
  const fails = Number(t.playability_fail_count || 0);
  if (st === "bad") return false;
  if (fails >= 3) return false;
  return true;
}}
function pickIndex() {{
  const playable = TRACKS.map((t, i) => [t, i]).filter(([t, _]) => tinyIsPlayable(t));
  if (playable.length === 0) return -1;
  if (playable.length === 1) return playable[0][1];
  const windowSize = Math.max(1, Math.min(TINY_SHUFFLE_RECENT_WINDOW, Math.floor(TRACKS.length * 0.5)));
  const blocked = new Set(tinyRecentTrackIds.slice(-windowSize));
  if (current >= 0 && TRACKS[current]?.track_id) blocked.add(TRACKS[current].track_id);
  let candidates = [];
  for (const [t, i] of playable) {{
    const tid = t?.track_id;
    if (!tid) continue;
    if (!blocked.has(tid)) candidates.push(i);
  }}
  if (candidates.length === 0) {{
    for (const [_, i] of playable) {{
      if (i !== current) candidates.push(i);
    }}
  }}
  if (candidates.length === 0) candidates = [playable[0][1]];
  return candidates[Math.floor(Math.random() * candidates.length)];
}}

function tinyPlayabilitySummary() {{
  if (!Array.isArray(TRACKS) || TRACKS.length === 0) return "";
  const bad = TRACKS.filter(t => String(t?.playability_status || "").toLowerCase() === "bad");
  if (!bad.length) return "";
  const sample = bad.find(t => String(t?.playability_last_error || "").trim()) || bad[0];
  const reason = String(sample?.playability_last_error || "").trim();
  return bad.length + " tracks are currently marked bad" + (reason ? (". Sample reason: " + reason) : ".");
}}

async function fetchTinyNextTrack(opts = {{}}) {{
  const res = await fetch(API + "/mobile/next/" + encodeURIComponent(userId), {{
    method: "POST",
    headers: {{ "Content-Type": "application/json" }},
    body: JSON.stringify({{
      current_track_id: String(opts.currentTrackId || ""),
      recent_track_ids: Array.isArray(opts.recentTrackIds) ? opts.recentTrackIds : [],
    }}),
    cache: "no-store",
  }});
  if (!res.ok) throw new Error("Next track HTTP " + res.status);
  const j = await res.json();
  return j?.track || null;
}}

function urlFor(track) {{
  const mobilePath = String(track?.stream_path_mobile || "");
  if (mobilePath) {{
    const sep = mobilePath.includes("?") ? "&" : "?";
    return mobilePath + sep + "v=" + libver();
  }}
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

async function playIndex(i, opts = {{}}) {{
  const baseTrack = TRACKS[i]; if (!baseTrack) return;
  const detail = await loadTinyTrackDetail(baseTrack.track_id);
  const m = Object.assign({{}}, baseTrack, detail || {{}});
  TRACKS[i] = m;
  const recordHistory = opts.recordHistory !== false;
  current = i;
  if (m.track_id) {{
    tinyRecentTrackIds.push(m.track_id);
    const keep = Math.max(4, TINY_SHUFFLE_RECENT_WINDOW * 2);
    if (tinyRecentTrackIds.length > keep) tinyRecentTrackIds = tinyRecentTrackIds.slice(-keep);
    if (recordHistory) {{
      if (!(tinyHistoryIndex >= 0 && tinyPlayHistory[tinyHistoryIndex] === m.track_id)) {{
        if (tinyHistoryIndex < tinyPlayHistory.length - 1) {{
          tinyPlayHistory = tinyPlayHistory.slice(0, tinyHistoryIndex + 1);
        }}
        tinyPlayHistory.push(m.track_id);
        tinyHistoryIndex = tinyPlayHistory.length - 1;
      }}
    }}
  }}
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
  const nowImg = document.getElementById("nowImg");
  const nowMeta = document.getElementById("nowMeta");
  if (nowMeta) nowMeta.textContent = [m.artist, m.album].filter(Boolean).join(" · ");
  if (nowImg) {{
    nowImg.style.display = "none";
    nowImg.removeAttribute("src");
  }}
  try {{
    const detail = await loadTinyTrackDetail(m.track_id);
    if (detail && nowMeta) {{
      const bits = [];
      if (detail.artist) bits.push(detail.artist);
      if (detail.album) bits.push(detail.album);
      if (detail.year) bits.push(String(detail.year));
      if (detail.genre) bits.push(String(detail.genre));
      nowMeta.textContent = bits.join(" · ");
      const artistBio = String(detail.artist_bio || "").trim();
      const albumBio = String(detail.album_bio || "").trim();
      if (artistBio || albumBio) {{
        nowMeta.textContent += (nowMeta.textContent ? "\\n\\n" : "") + [artistBio, albumBio].filter(Boolean).join("\\n\\n");
      }}
    }}
    if (detail && nowImg) {{
      const src = String(detail.artwork_url || detail.artist_image_url || "").trim();
      if (src) {{
        nowImg.src = src;
        nowImg.style.display = "";
        nowImg.onerror = () => {{
          nowImg.style.display = "none";
        }};
      }}
    }}
  }} catch (e) {{
    console.warn("[tiny] track detail fetch failed", e);
  }}
  // Warm a candidate next track while current is playing to reduce gap.
  setTimeout(() => {{
    const nx = pickIndex();
    prefetchTinyTrack(nx);
  }}, 1200);
}}
let current = -1;
function indexOfTrackId(tid) {{
  if (!tid) return -1;
  for (let i = 0; i < TRACKS.length; i++) {{
    if (TRACKS[i] && TRACKS[i].track_id === tid) return i;
  }}
  return -1;
}}
function playNext() {{
  // If user went back, allow stepping forward through known history first.
  if (tinyHistoryIndex >= 0 && tinyHistoryIndex < (tinyPlayHistory.length - 1)) {{
    tinyHistoryIndex += 1;
    const tid = tinyPlayHistory[tinyHistoryIndex];
    const i = indexOfTrackId(tid);
    if (i >= 0) {{ playIndex(i, {{ recordHistory: false }}); return; }}
  }}
  fetchTinyNextTrack({{
    currentTrackId: (current >= 0 && TRACKS[current]) ? TRACKS[current].track_id : "",
    recentTrackIds: tinyRecentTrackIds.slice(-TINY_SHUFFLE_RECENT_WINDOW),
  }}).then(track => {{
    if (!track || !track.track_id) {{
      showStatus(tinyPlayabilitySummary() || "No playable tracks are currently available.", "warn", 5000);
      return;
    }}
    const i = indexOfTrackId(track.track_id);
    if (i >= 0) {{
      TRACKS[i] = Object.assign({{}}, TRACKS[i], track);
      playIndex(i, {{ recordHistory: true }});
      return;
    }}
    TRACKS.push(track);
    playIndex(TRACKS.length - 1, {{ recordHistory: true }});
  }}).catch(err => {{
    showStatus("Could not fetch next track: " + (err?.message || "unknown error"), "error", 5000);
  }});
}}
function playPrev() {{
  if (tinyHistoryIndex > 0) {{
    tinyHistoryIndex -= 1;
    const tid = tinyPlayHistory[tinyHistoryIndex];
    const i = indexOfTrackId(tid);
    if (i >= 0) {{ playIndex(i, {{ recordHistory: false }}); return; }}
  }}
  showStatus("No previous track in history yet.", "warn", 2500);
}}

// Wire the explicit Next button (gives guaranteed user gesture)
document.getElementById('btnPrev').addEventListener('click', () => playPrev());
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
  const t = (current >= 0 && TRACKS[current]) ? TRACKS[current] : null;
  const expectedDur = Math.max(0, Number(t && t.duration_sec || 0));
  const expectedPos = Number(audio.currentTime) || 0;
  if (expectedDur > 0 && expectedPos < (expectedDur - 1.5)) {{
    showStatus("Stream ended early. Keeping current track.", "warn", 3000);
    return;
  }}
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
(async () => {{
  try {{
    const n = await loadTinyTracks();
    if (!n) showStatus("Library is empty.", "warn", 5000);
  }} catch (e) {{
    showStatus("Could not load track list: " + (e?.message || "unknown error"), "error", 7000);
  }}
  refreshStatus();
  setInterval(refreshStatus, 10000);
}})();
</script>
</body>
</html>"""
    return HTMLResponse(html, status_code=200)
