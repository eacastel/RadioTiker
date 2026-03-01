# thin_agent.py

import os
import time
import hashlib
import re
import requests
from mutagen import File
from dotenv import load_dotenv
from local_file_server import LocalFileServer
from tunnel_manager import start_tunnel_from_env

load_dotenv()

SERVER_URL = os.getenv("SERVER_URL", "https://radio.tiker.es/streamer/api/submit-scan")
ANNOUNCE_URL = os.getenv(
    "ANNOUNCE_URL",
    SERVER_URL.replace("/submit-scan", "/agent/announce"),
)
USER_ID = os.getenv("USER_ID", "test-user-001")
LIBRARY_PATH = os.getenv("LIBRARY_PATH", "./Music")
AGENT_PORT = int(os.getenv("AGENT_PORT", "8765"))
VALID_EXTENSIONS = tuple(os.getenv("VALID_AUDIO_EXTENSIONS", ".mp3,.flac,.wav,.m4a").split(","))
VALID_EXTENSIONS = tuple(ext.strip().lower() for ext in VALID_EXTENSIONS if ext.strip())
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip() or None

def _file_fingerprint(path: str) -> tuple[int, int]:
    st = os.stat(path)
    return (st.st_size, int(st.st_mtime))

def _track_id(path: str, size: int, mtime: int) -> str:
    h = hashlib.sha1()
    h.update(path.encode("utf-8", "ignore"))
    h.update(b"|"); h.update(str(size).encode())
    h.update(b"|"); h.update(str(mtime).encode())
    return h.hexdigest()

def _duration_seconds(path: str):
    try:
        mf = File(path)
        return round(float(getattr(mf.info, "length", 0.0)), 3) if mf and getattr(mf, "info", None) else None
    except Exception:
        return None

def _first_tag(audio, key: str, default=None):
    if not audio:
        return default
    try:
        vals = audio.get(key)
        if not vals:
            return default
        val = vals[0]
        return str(val).strip() if val is not None else default
    except Exception:
        return default

def _parse_first_int(value) -> int | None:
    if value is None:
        return None
    m = re.search(r"\d+", str(value))
    if not m:
        return None
    try:
        return int(m.group(0))
    except Exception:
        return None

def _parse_float(value) -> float | None:
    if value is None:
        return None
    m = re.search(r"\d+(\.\d+)?", str(value))
    if not m:
        return None
    try:
        return float(m.group(0))
    except Exception:
        return None

def _codec_from_path(path: str) -> str | None:
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    return ext or None

def _technical_info(path: str):
    codec = _codec_from_path(path)
    sample_rate = None
    bit_depth = None
    bitrate_kbps = None
    channels = None
    try:
        mf = File(path)
        info = getattr(mf, "info", None) if mf else None
        if info:
            sr = getattr(info, "sample_rate", None)
            if isinstance(sr, int) and sr > 0:
                sample_rate = sr
            bd = getattr(info, "bits_per_sample", None)
            if isinstance(bd, int) and bd > 0:
                bit_depth = bd
            br = getattr(info, "bitrate", None)
            if isinstance(br, int) and br > 0:
                bitrate_kbps = int(round(br / 1000))
            ch = getattr(info, "channels", None)
            if isinstance(ch, int) and ch > 0:
                channels = ch
    except Exception:
        pass
    return codec, sample_rate, bit_depth, bitrate_kbps, channels

def announce_agent(user_id: str, base_url: str):
    payload = {"user_id": user_id, "base_url": base_url}
    try:
        r = requests.post(ANNOUNCE_URL, json=payload, timeout=10)
        print("📣 Announce:", r.status_code, r.text[:200])
    except Exception as e:
        print("❌ Announce failed:", e)

def scan_folder(folder_path, base_url):
    library = []
    root_abs = os.path.abspath(folder_path)
    for root, _, files in os.walk(root_abs):
        for file in files:
            if not file.lower().endswith(VALID_EXTENSIONS):
                continue
            full_path = os.path.join(root, file)
            try:
                audio = File(full_path, easy=True)
                size, mtime = _file_fingerprint(full_path)
                rel = os.path.relpath(full_path, root_abs).replace("\\", "/")
                duration_sec = _duration_seconds(full_path)
                codec, sample_rate, bit_depth, bitrate_kbps, channels = _technical_info(full_path)
                track_no = _parse_first_int(_first_tag(audio, "tracknumber"))
                disc_no = _parse_first_int(_first_tag(audio, "discnumber"))
                year = _parse_first_int(_first_tag(audio, "date"))
                bpm = _parse_float(_first_tag(audio, "bpm"))
                title_default = os.path.splitext(file)[0]
                metadata = {
                    "title": _first_tag(audio, "title", title_default),
                    "artist": _first_tag(audio, "artist", "Unknown"),
                    "album": _first_tag(audio, "album", "Unknown"),
                    "album_artist": _first_tag(audio, "albumartist"),
                    "genre": _first_tag(audio, "genre"),
                    "year": year,
                    "track_no": track_no,
                    "disc_no": disc_no,
                    "composer": _first_tag(audio, "composer"),
                    "bpm": bpm,
                    "musical_key": _first_tag(audio, "initialkey") or _first_tag(audio, "key"),
                    "codec": codec,
                    "sample_rate": sample_rate,
                    "bit_depth": bit_depth,
                    "bitrate_kbps": bitrate_kbps,
                    "channels": channels,
                    "path": full_path,
                    "rel_path": rel,
                    "file_size": size,
                    "mtime": mtime,
                    "duration_sec": duration_sec,
                    "track_id": _track_id(full_path, size, mtime),
                }
                library.append(metadata)
            except Exception as e:
                print(f"Error reading {full_path}: {e}")
    return library

def send_in_batches(user_id, tracks, batch_size=300):
    if not tracks:
        print("No tracks found.")
        return
    version = int(time.time())
    total = len(tracks)
    for i in range(0, total, batch_size):
        chunk = tracks[i:i+batch_size]
        payload = {
            "user_id": user_id,
            "library": chunk,
            "library_version": version,
            "replace": bool(i == 0),
        }
        try:
            response = requests.post(SERVER_URL, json=payload, timeout=90)
            print(f"✅ Batch {i+1}-{i+len(chunk)}/{total}:", response.status_code, response.text[:200])
        except Exception as e:
            print(f"❌ Batch {i+1}-{i+len(chunk)} failed:", e)
            break

if __name__ == "__main__":
    library_root = os.path.abspath(os.path.expanduser(LIBRARY_PATH))
    print(f"🎵 Serving & scanning: {library_root}")
    tunnel, tunnel_base = start_tunnel_from_env(AGENT_PORT, log_fn=print)
    public_base_url = PUBLIC_BASE_URL or tunnel_base

    server = LocalFileServer(root_dir=library_root, port=AGENT_PORT, public_base_url=public_base_url)
    server.start()
    base = server.base_url()
    print(f"🔌 Local server: {base}")
    announce_agent(USER_ID, base)
    tracks = scan_folder(library_root, base)
    print(f"📁 Found {len(tracks)} tracks.")
    send_in_batches(USER_ID, tracks)
    # Keep the tunnel alive for long-running sessions.
    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        if tunnel:
            tunnel.stop()
