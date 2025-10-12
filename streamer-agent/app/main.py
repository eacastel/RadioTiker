from pathlib import Path
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
import json
import requests

app = FastAPI(title="RadioTiker Streamer API")

# === Storage locations ===
ROOT = Path(__file__).resolve().parent.parent.parent  # radio-tiker-core/
DATA_DIR = ROOT / "data" / "user-libraries"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# === In-memory cache ===
LIBRARIES: Dict[str, List[Dict[str, Any]]] = {}

# === Models ===
class Track(BaseModel):
    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    path: Optional[str] = None
    stream_url: Optional[str] = None

class ScanPayload(BaseModel):
    user_id: str
    library: List[Track]

# === Helpers ===
def library_path(user_id: str) -> Path:
    safe = "".join(ch for ch in user_id if ch.isalnum() or ch in ("-", "_", "."))
    return DATA_DIR / f"{safe}.json"

def load_library(user_id: str) -> List[Dict[str, Any]]:
    if user_id in LIBRARIES:
        return LIBRARIES[user_id]
    p = library_path(user_id)
    if p.exists():
        try:
            data = json.loads(p.read_text())
            if isinstance(data, list):
                LIBRARIES[user_id] = data
                return data
        except Exception:
            pass
    LIBRARIES[user_id] = []
    return LIBRARIES[user_id]

def save_library(user_id: str, lib: List[Dict[str, Any]]):
    LIBRARIES[user_id] = lib
    p = library_path(user_id)
    p.write_text(json.dumps(lib, indent=2))

# === Endpoints ===
@app.get("/api/health")
def health():
    return {"ok": True}

@app.post("/api/submit-scan")
def submit_scan(payload: ScanPayload):
    lib = [t.model_dump() for t in payload.library]
    save_library(payload.user_id, lib)
    preview = [
        {k: v for k, v in t.items() if k in ("title", "artist", "stream_url")}
        for t in lib[:3]
    ]
    return {"ok": True, "user_id": payload.user_id, "count": len(lib), "preview": preview}

@app.get("/api/library/{user_id}")
def get_library(user_id: str):
    return load_library(user_id)

@app.get("/relay/{user_id}/{idx}")
def relay_by_index(user_id: str, idx: int, request: Request):
    lib = load_library(user_id)
    if not (0 <= idx < len(lib)):
        raise HTTPException(status_code=404, detail="Track index out of range")
    stream_url = lib[idx].get("stream_url")
    if not stream_url:
        raise HTTPException(status_code=400, detail="No stream_url for this track")

    headers = {"User-Agent": "RadioTiker-Relay/0.1"}
    rng = request.headers.get("range")
    if rng:
        headers["Range"] = rng

    try:
        upstream = requests.get(stream_url, stream=True, timeout=(5, 30), headers=headers)
        upstream.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Upstream fetch failed: {e}")

    passthrough = {k: v for k in [
        "Content-Type","Content-Length","Content-Range","Accept-Ranges",
        "Cache-Control","ETag","Last-Modified"
    ] if (v := upstream.headers.get(k))}

    def iter_bytes():
        for chunk in upstream.iter_content(chunk_size=64*1024):
            if chunk:
                yield chunk

    return StreamingResponse(iter_bytes(),
                             media_type=upstream.headers.get("Content-Type") or "audio/mpeg",
                             headers=passthrough,
                             status_code=upstream.status_code)

@app.get("/user/{user_id}/play", response_class=HTMLResponse)
def user_player(user_id: str):
    lib = load_library(user_id)
    rows = []
    for i, t in enumerate(lib):
        title = t.get("title") or "Unknown Title"
        artist = t.get("artist") or "Unknown Artist"
        stream_exists = "✅" if t.get("stream_url") else "—"
        rows.append(f"""
          <tr>
            <td>{i}</td>
            <td>{title}</td>
            <td>{artist}</td>
            <td style="text-align:center">{stream_exists}</td>
            <td><button onclick="playIndex({i})">Play</button></td>
          </tr>
        """)
    rows_html = "\n".join(rows) if rows else "<tr><td colspan='5'>No tracks yet.</td></tr>"

    html = f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>RadioTiker – Player ({user_id})</title>
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; padding: 24px; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
    th, td {{ border-bottom: 1px solid #eee; padding: 8px; text-align: left; }}
    thead th {{ background: #fafafa; }}
    .now {{ margin-top: 16px; }}
    button {{ padding: 6px 10px; }}
  </style>
</head>
<body>
  <h1>RadioTiker – Player</h1>
  <p>User: <strong>{user_id}</strong></p>

  <audio id="player" controls preload="none" style="width:100%">
    <source id="src" src="" type="audio/mpeg"/>
    Your browser does not support the audio element.
  </audio>
  <div class="now"><strong>Now Playing:</strong> <span id="now">—</span></div>

  <table>
    <thead>
      <tr><th>#</th><th>Title</th><th>Artist</th><th>URL?</th><th>Action</th></tr>
    </thead>
    <tbody>
      {rows_html}
    </tbody>
  </table>

  <script>
    const userId = {json.dumps(user_id)};
    function playIndex(i) {{
      const url = `/streamer/api/relay/${{userId}}/${{i}}`;
      const audio = document.getElementById('player');
      const src = document.getElementById('src');
      src.src = url;
      audio.load();
      audio.play().catch(()=>{{}});
      document.getElementById('now').innerText = `Track #${{i}} via relay`;
    }}
  </script>
</body>
</html>
"""
    return HTMLResponse(content=html, status_code=200)
