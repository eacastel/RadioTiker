# thin_agent.py

import os
import time
import hashlib
import re
import shutil
import json
import subprocess
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
ENRICH_URL_BASE = os.getenv(
    "ENRICH_URL_BASE",
    SERVER_URL.replace("/submit-scan", "/metadata/enrich-library"),
)
USER_ID = os.getenv("USER_ID", "test-user-001")
LIBRARY_PATH = os.getenv("LIBRARY_PATH", "./Music")
AGENT_PORT = int(os.getenv("AGENT_PORT", "8765"))
VALID_EXTENSIONS = tuple(os.getenv("VALID_AUDIO_EXTENSIONS", ".mp3,.flac,.wav,.m4a").split(","))
VALID_EXTENSIONS = tuple(ext.strip().lower() for ext in VALID_EXTENSIONS if ext.strip())
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip() or None
FULL_RESCAN_INTERVAL_MIN = int(os.getenv("FULL_RESCAN_INTERVAL_MIN", "0") or "0")
ENABLE_ACOUSTID_SCAN = str(os.getenv("ENABLE_ACOUSTID_SCAN", "0")).strip().lower() in {"1", "true", "yes", "on"}
SCAN_BATCH_SIZE = max(25, int(os.getenv("RT_SCAN_BATCH_SIZE", "50") or "50"))
SCAN_SUBMIT_TIMEOUT_SEC = max(15, int(os.getenv("RT_SCAN_SUBMIT_TIMEOUT_SEC", "180") or "180"))
SCAN_SUBMIT_RETRIES = max(1, int(os.getenv("RT_SCAN_SUBMIT_RETRIES", "3") or "3"))
SCAN_RETRY_BACKOFF_SEC = max(1.0, float(os.getenv("RT_SCAN_RETRY_BACKOFF_SEC", "2") or "2"))
SCAN_RESUME_ENABLED = str(os.getenv("RT_SCAN_RESUME_ENABLED", "1")).strip().lower() in {"1", "true", "yes", "on"}
SCAN_RESUME_RESET = str(os.getenv("RT_SCAN_RESUME_RESET", "0")).strip().lower() in {"1", "true", "yes", "on"}
POST_SCAN_ENRICH_ENABLED = str(os.getenv("RT_POST_SCAN_ENRICH_ENABLED", "0")).strip().lower() in {"1", "true", "yes", "on"}
POST_SCAN_ENRICH_LIMIT = max(20, int(os.getenv("RT_POST_SCAN_ENRICH_LIMIT", "300") or "300"))
POST_SCAN_ENRICH_MAX_PASSES = max(1, int(os.getenv("RT_POST_SCAN_ENRICH_MAX_PASSES", "25") or "25"))
POST_SCAN_ENRICH_MIN_SCORE = float(os.getenv("RT_POST_SCAN_ENRICH_MIN_SCORE", "0.88") or "0.88")
POST_SCAN_ENRICH_APPLY = str(os.getenv("RT_POST_SCAN_ENRICH_APPLY", "1")).strip().lower() in {"1", "true", "yes", "on"}
POST_SCAN_ENRICH_TIMEOUT_SEC = max(15, int(os.getenv("RT_POST_SCAN_ENRICH_TIMEOUT_SEC", "180") or "180"))
POST_SCAN_ENRICH_PROVIDERS = [
    p.strip().lower()
    for p in os.getenv("RT_POST_SCAN_ENRICH_PROVIDERS", "musicbrainz,discogs,acoustid").split(",")
    if p.strip()
]


def _scan_resume_file(user_id: str):
    base = os.path.join(os.path.expanduser("~"), ".radiotiker")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, f"scan_resume_{user_id}.json")


def _load_scan_resume(user_id: str):
    p = _scan_resume_file(user_id)
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_scan_resume(user_id: str, data: dict):
    p = _scan_resume_file(user_id)
    try:
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except Exception:
        pass


def _iter_audio_files(folder_path: str):
    root_abs = os.path.abspath(folder_path)
    for root, dirs, files in os.walk(root_abs):
        dirs.sort()
        for fname in sorted(files):
            if fname.lower().endswith(VALID_EXTENSIONS):
                yield os.path.join(root, fname)

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


def _acoustid_fingerprint(path: str):
    """
    Best-effort Chromaprint using fpcalc on the agent host.
    Returns (fingerprint, duration) or (None, None).
    """
    if not ENABLE_ACOUSTID_SCAN:
        return None, None
    exe = shutil.which("fpcalc")
    if not exe:
        return None, None
    try:
        # Prefer JSON output when available.
        p = subprocess.run([exe, "-json", path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20, text=True, check=False)
        out = (p.stdout or "").strip()
        if out.startswith("{"):
            j = json.loads(out)
            fp = str(j.get("fingerprint") or "").strip()
            dur = float(j.get("duration")) if j.get("duration") is not None else None
            if fp:
                return fp, dur
    except Exception:
        pass
    try:
        # Fallback key=value output.
        p = subprocess.run([exe, path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20, text=True, check=False)
        fp = None
        dur = None
        for ln in (p.stdout or "").splitlines():
            if ln.startswith("FINGERPRINT="):
                fp = ln.split("=", 1)[1].strip()
            elif ln.startswith("DURATION="):
                try:
                    dur = float(ln.split("=", 1)[1].strip())
                except Exception:
                    dur = None
        if fp:
            return fp, dur
    except Exception:
        pass
    return None, None

def announce_agent(user_id: str, base_url: str):
    payload = {"user_id": user_id, "base_url": base_url}
    try:
        r = requests.post(ANNOUNCE_URL, json=payload, timeout=10)
        print("📣 Announce:", r.status_code, r.text[:200])
    except Exception as e:
        print("❌ Announce failed:", e)


def run_post_scan_enrichment(user_id: str):
    if not POST_SCAN_ENRICH_ENABLED:
        return
    if not POST_SCAN_ENRICH_PROVIDERS:
        print("ℹ️ Post-scan enrich skipped: no providers configured.")
        return
    url = f"{ENRICH_URL_BASE.rstrip('/')}/{user_id}"
    print(
        "🧠 Post-scan enrich started:",
        f"providers={POST_SCAN_ENRICH_PROVIDERS}",
        f"limit={POST_SCAN_ENRICH_LIMIT}",
        f"max_passes={POST_SCAN_ENRICH_MAX_PASSES}",
        f"min_score={POST_SCAN_ENRICH_MIN_SCORE}",
        f"apply={POST_SCAN_ENRICH_APPLY}",
    )
    total_scanned = 0
    total_matched = 0
    total_applied = 0
    for i in range(1, POST_SCAN_ENRICH_MAX_PASSES + 1):
        payload = {
            "limit": POST_SCAN_ENRICH_LIMIT,
            "apply": POST_SCAN_ENRICH_APPLY,
            "providers": POST_SCAN_ENRICH_PROVIDERS,
            "min_score": POST_SCAN_ENRICH_MIN_SCORE,
        }
        try:
            r = requests.post(url, json=payload, timeout=POST_SCAN_ENRICH_TIMEOUT_SEC)
            if r.status_code >= 400:
                print(f"⚠️ Post-scan enrich pass {i} failed: HTTP {r.status_code} {r.text[:200]}")
                break
            j = r.json()
            scanned = int(j.get("scanned") or 0)
            matched = int(j.get("matched") or 0)
            applied = int(j.get("applied") or 0)
            total_scanned += scanned
            total_matched += matched
            total_applied += applied
            print(f"🧠 Enrich pass {i}: scanned={scanned} matched={matched} applied={applied}")
            # Stop when server scanned less than requested window (likely drained)
            if scanned < POST_SCAN_ENRICH_LIMIT:
                break
            # If no progress, avoid burning API calls.
            if matched == 0 and applied == 0:
                break
        except Exception as e:
            print(f"⚠️ Post-scan enrich pass {i} error: {e}")
            break
    print(
        "✅ Post-scan enrich completed:",
        f"scanned={total_scanned}",
        f"matched={total_matched}",
        f"applied={total_applied}",
    )

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
                acoustid_fp, acoustid_dur = _acoustid_fingerprint(full_path)
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
                    "acoustid_fingerprint": acoustid_fp,
                    "acoustid_duration": acoustid_dur,
                    "track_id": _track_id(full_path, size, mtime),
                }
                library.append(metadata)
            except Exception as e:
                print(f"Error reading {full_path}: {e}")
    return library

def _post_scan_chunk(user_id, chunk, version, replace, range_start, range_end, total):
    payload = {
        "user_id": user_id,
        "library": chunk,
        "library_version": version,
        "replace": bool(replace),
    }
    last_err = None
    for attempt in range(1, SCAN_SUBMIT_RETRIES + 1):
        try:
            response = requests.post(SERVER_URL, json=payload, timeout=SCAN_SUBMIT_TIMEOUT_SEC)
            if response.status_code >= 400:
                raise RuntimeError(f"HTTP {response.status_code}: {response.text[:240]}")
            print(f"✅ Batch {range_start}-{range_end}/{total}:", response.status_code, response.text[:200])
            return True
        except Exception as e:
            last_err = e
            if attempt < SCAN_SUBMIT_RETRIES:
                wait = SCAN_RETRY_BACKOFF_SEC * attempt
                print(
                    f"⚠️ Batch {range_start}-{range_end} attempt {attempt}/{SCAN_SUBMIT_RETRIES} failed: {e}. "
                    f"Retrying in {wait:.0f}s..."
                )
                time.sleep(wait)
    print(f"❌ Batch {range_start}-{range_end} failed after {SCAN_SUBMIT_RETRIES} attempts:", last_err)
    return False


def scan_and_send_incremental(user_id, folder_path, batch_size=SCAN_BATCH_SIZE, replace=True):
    root_abs = os.path.abspath(folder_path)
    version = int(time.time())
    candidates = list(_iter_audio_files(root_abs))
    total = len(candidates)
    resume_from = 0
    replace_next = bool(replace)
    uploaded = 0
    failed = False

    if SCAN_RESUME_ENABLED:
        if SCAN_RESUME_RESET:
            try:
                os.remove(_scan_resume_file(user_id))
                print("♻️ Scan resume checkpoint reset.")
            except FileNotFoundError:
                pass
            except Exception:
                pass
        resume_state = _load_scan_resume(user_id)
        if resume_state.get("folder_path") == root_abs and not bool(resume_state.get("completed", False)):
            resume_from = max(0, int(resume_state.get("committed_scanned", 0) or 0))
            uploaded = max(0, int(resume_state.get("uploaded", 0) or 0))
            replace_next = bool(resume_state.get("replace_next", replace))
            if resume_from > 0:
                print(f"↩️ Resuming scan from {resume_from}/{total} committed files.")
        else:
            _save_scan_resume(user_id, {
                "user_id": user_id,
                "folder_path": root_abs,
                "version": version,
                "total_candidates": total,
                "committed_scanned": 0,
                "uploaded": 0,
                "replace_next": bool(replace),
                "completed": False,
                "updated_at": int(time.time()),
            })

    print(f"Scanning folder: {root_abs}")
    print(f"Total candidates: {total}")
    scanned = resume_from
    chunk = []

    for idx in range(resume_from, total):
        full_path = candidates[idx]
        file = os.path.basename(full_path)
        try:
            audio = File(full_path, easy=True)
            size, mtime = _file_fingerprint(full_path)
            rel = os.path.relpath(full_path, root_abs).replace("\\", "/")
            duration_sec = _duration_seconds(full_path)
            acoustid_fp, acoustid_dur = _acoustid_fingerprint(full_path)
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
                "acoustid_fingerprint": acoustid_fp,
                "acoustid_duration": acoustid_dur,
                "track_id": _track_id(full_path, size, mtime),
            }
            chunk.append(metadata)
            scanned += 1
            if scanned % 25 == 0 or scanned == total:
                print(f"  • Scanned {scanned}/{total} ...")
            if len(chunk) >= batch_size:
                start = scanned - len(chunk) + 1
                end = scanned
                if not _post_scan_chunk(user_id, chunk, version, replace_next, start, end, total):
                    failed = True
                    if SCAN_RESUME_ENABLED:
                        _save_scan_resume(user_id, {
                            "user_id": user_id,
                            "folder_path": root_abs,
                            "version": version,
                            "total_candidates": total,
                            "committed_scanned": scanned - len(chunk),
                            "uploaded": uploaded,
                            "replace_next": replace_next,
                            "completed": False,
                            "updated_at": int(time.time()),
                        })
                    print(f"⏸️ Paused after failed batch {start}-{end}; restart will resume.")
                    return {"scanned": scanned, "uploaded": uploaded, "failed": True}
                replace_next = False
                uploaded += len(chunk)
                if SCAN_RESUME_ENABLED:
                    _save_scan_resume(user_id, {
                        "user_id": user_id,
                        "folder_path": root_abs,
                        "version": version,
                        "total_candidates": total,
                        "committed_scanned": scanned,
                        "uploaded": uploaded,
                        "replace_next": replace_next,
                        "completed": False,
                        "updated_at": int(time.time()),
                    })
                chunk = []
        except Exception as e:
            print(f"Error reading {full_path}: {e}")

    if chunk:
        start = scanned - len(chunk) + 1
        end = scanned
        if not _post_scan_chunk(user_id, chunk, version, replace_next, start, end, total):
            failed = True
            if SCAN_RESUME_ENABLED:
                _save_scan_resume(user_id, {
                    "user_id": user_id,
                    "folder_path": root_abs,
                    "version": version,
                    "total_candidates": total,
                    "committed_scanned": scanned - len(chunk),
                    "uploaded": uploaded,
                    "replace_next": replace_next,
                    "completed": False,
                    "updated_at": int(time.time()),
                })
            print(f"⏸️ Final partial batch {start}-{end} failed; restart will resume.")
            return {"scanned": scanned, "uploaded": uploaded, "failed": True}
        else:
            replace_next = False
            uploaded += len(chunk)

    if SCAN_RESUME_ENABLED:
        _save_scan_resume(user_id, {
            "user_id": user_id,
            "folder_path": root_abs,
            "version": version,
            "total_candidates": total,
            "committed_scanned": scanned,
            "uploaded": uploaded,
            "replace_next": False,
            "completed": True,
            "updated_at": int(time.time()),
        })
    return {"scanned": scanned, "uploaded": uploaded, "failed": failed}


def send_in_batches(user_id, tracks, batch_size=SCAN_BATCH_SIZE, replace=True):
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
            "replace": bool(replace and i == 0),
        }
        ok = False
        last_err = None
        for attempt in range(1, SCAN_SUBMIT_RETRIES + 1):
            try:
                response = requests.post(SERVER_URL, json=payload, timeout=SCAN_SUBMIT_TIMEOUT_SEC)
                if response.status_code >= 400:
                    raise RuntimeError(f"HTTP {response.status_code}: {response.text[:240]}")
                print(f"✅ Batch {i+1}-{i+len(chunk)}/{total}:", response.status_code, response.text[:200])
                ok = True
                break
            except Exception as e:
                last_err = e
                if attempt < SCAN_SUBMIT_RETRIES:
                    wait = SCAN_RETRY_BACKOFF_SEC * attempt
                    print(
                        f"⚠️ Batch {i+1}-{i+len(chunk)} attempt {attempt}/{SCAN_SUBMIT_RETRIES} failed: {e}. "
                        f"Retrying in {wait:.0f}s..."
                    )
                    time.sleep(wait)
        if not ok:
            print(f"❌ Batch {i+1}-{i+len(chunk)} failed after {SCAN_SUBMIT_RETRIES} attempts:", last_err)
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

    def run_full_scan_cycle(reason: str = "scheduled"):
        print(f"🔎 Full scan ({reason}) started")
        result = scan_and_send_incremental(USER_ID, library_root, batch_size=SCAN_BATCH_SIZE, replace=True)
        print(
            f"📁 Scan complete ({reason}): valid={result['scanned']} uploaded={result['uploaded']} "
            f"failed={'yes' if result['failed'] else 'no'}."
        )
        if not result["failed"] and result["uploaded"] > 0:
            run_post_scan_enrichment(USER_ID)
        print(f"✅ Full scan ({reason}) completed")

    run_full_scan_cycle("startup")

    # Keep the tunnel alive for long-running sessions.
    try:
        last_scan_ts = time.time()
        while True:
            time.sleep(5)
            if FULL_RESCAN_INTERVAL_MIN > 0:
                now = time.time()
                if (now - last_scan_ts) >= (FULL_RESCAN_INTERVAL_MIN * 60):
                    run_full_scan_cycle("interval")
                    last_scan_ts = now
    except KeyboardInterrupt:
        if tunnel:
            tunnel.stop()
