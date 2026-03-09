# thin_agent_gui.py

import os, time, threading, hashlib, json, re, shutil
from pathlib import Path
from dotenv import load_dotenv
from mutagen import File as MutagenFile
import requests
import subprocess
import webbrowser

import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox, Toplevel, Label
from urllib.parse import urljoin

from local_file_server import LocalFileServer
from tunnel_manager import start_tunnel_from_env

APP_NAME = "RadioTiker Thin Agent"
APP_VERSION = "0.3"

load_dotenv()

# --- API endpoints (proxied via NGINX) ---
API_BASE    = os.getenv("SERVER_BASE", "https://next.radio.tiker.es/streamer/api/")
SUBMIT_URL  = urljoin(API_BASE, "submit-scan")
ANNOUNCE_URL= urljoin(API_BASE, "agent/announce")
ENRICH_URL_BASE = urljoin(API_BASE, "metadata/enrich-library/")

CONF_DIR  = Path.home() / ".radiotiker"
CONF_DIR.mkdir(parents=True, exist_ok=True)
CONF_FILE = CONF_DIR / "agent.json"

# ---------- state helpers ----------
def read_state() -> dict:
    try:
        return json.loads(CONF_FILE.read_text())
    except Exception:
        return {}

def write_state(d: dict):
    try:
        CONF_FILE.write_text(json.dumps(d, indent=2))
    except Exception:
        pass

def get_last_root() -> str | None:
    return read_state().get("last_root")

def _remember_root(path: str):
    st = read_state()
    st["last_root"] = path
    write_state(st)

def tailscale_ok() -> bool:
    try:
        out = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=2)
        if out.returncode != 0:
            return False
        return any(line.strip().startswith("100.") for line in out.stdout.splitlines())
    except Exception:
        return False
    
def _default_root() -> str:
    env = os.getenv("LIBRARY_PATH")
    if env and os.path.isdir(env):
        return env
    last = get_last_root()
    if last and os.path.isdir(last):
        return last
    return str(Path.home())

# ---------- config ----------
VALID_EXTENSIONS = tuple(
    ext.strip().lower()
    for ext in os.getenv("VALID_AUDIO_EXTENSIONS", ".mp3,.flac,.wav,.m4a").split(",")
    if ext.strip()
)
DEFAULT_PORT     = int(os.getenv("AGENT_PORT", "8765"))
PUBLIC_BASE_URL  = os.getenv("PUBLIC_BASE_URL")  # optional tunnel override
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

# ---------- identity ----------
def get_user_id_interactive(default_val="user-001"):
    try:
        import tkinter.simpledialog as sd
        root = tk.Tk(); root.withdraw()
        val = sd.askstring("RadioTiker", "Enter your user ID:", initialvalue=default_val)
        try: root.destroy()
        except Exception: pass
        return val or default_val
    except Exception:
        return default_val

def load_or_prompt_user_id():
    env_val = os.getenv("USER_ID")
    if env_val:
        st = read_state()
        st["user_id"] = env_val
        write_state(st)
        return env_val
    st = read_state()
    if "user_id" in st and st["user_id"]:
        return st["user_id"]
    uid = get_user_id_interactive("user-001")
    st["user_id"] = uid
    write_state(st)
    return uid

USER_ID = load_or_prompt_user_id()


def _scan_resume_file(user_id: str) -> Path:
    return CONF_DIR / f"scan_resume_{user_id}.json"


def _load_scan_resume(user_id: str) -> dict:
    p = _scan_resume_file(user_id)
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _save_scan_resume(user_id: str, data: dict):
    p = _scan_resume_file(user_id)
    try:
        p.write_text(json.dumps(data, indent=2))
    except Exception:
        pass


def _iter_audio_files(folder_path: str):
    root_abs = os.path.abspath(folder_path)
    for root, dirs, files in os.walk(root_abs):
        dirs.sort()
        for fname in sorted(files):
            if fname.lower().endswith(VALID_EXTENSIONS):
                yield os.path.join(root, fname)

# ---------- utils ----------
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
        mf = MutagenFile(path)
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
        mf = MutagenFile(path)
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
    if not ENABLE_ACOUSTID_SCAN:
        return None, None
    exe = shutil.which("fpcalc")
    if not exe:
        return None, None
    try:
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

def announce_agent(user_id: str, base_url: str, log_fn):
    payload = {"user_id": user_id, "base_url": base_url}
    try:
        r = requests.post(ANNOUNCE_URL, json=payload, timeout=10)
        log_fn(f"📣 Announce: {r.status_code} {r.text[:200]}\n")
    except Exception as e:
        log_fn(f"❌ Announce failed: {e}\n")


def run_post_scan_enrichment(user_id: str, log_fn):
    if not POST_SCAN_ENRICH_ENABLED:
        return
    if not POST_SCAN_ENRICH_PROVIDERS:
        log_fn("ℹ️ Post-scan enrich skipped: no providers configured.\n")
        return
    url = f"{ENRICH_URL_BASE.rstrip('/')}/{user_id}"
    log_fn(
        "🧠 Post-scan enrich started: "
        f"providers={POST_SCAN_ENRICH_PROVIDERS} "
        f"limit={POST_SCAN_ENRICH_LIMIT} "
        f"max_passes={POST_SCAN_ENRICH_MAX_PASSES} "
        f"min_score={POST_SCAN_ENRICH_MIN_SCORE} "
        f"apply={POST_SCAN_ENRICH_APPLY}\n"
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
                log_fn(f"⚠️ Post-scan enrich pass {i} failed: HTTP {r.status_code} {r.text[:200]}\n")
                break
            j = r.json()
            scanned = int(j.get("scanned") or 0)
            matched = int(j.get("matched") or 0)
            applied = int(j.get("applied") or 0)
            total_scanned += scanned
            total_matched += matched
            total_applied += applied
            log_fn(f"🧠 Enrich pass {i}: scanned={scanned} matched={matched} applied={applied}\n")
            if scanned < POST_SCAN_ENRICH_LIMIT:
                break
            if matched == 0 and applied == 0:
                break
        except Exception as e:
            log_fn(f"⚠️ Post-scan enrich pass {i} error: {e}\n")
            break
    log_fn(
        "✅ Post-scan enrich completed: "
        f"scanned={total_scanned} matched={total_matched} applied={total_applied}\n"
    )

def scan_folder(folder_path, log_fn, stop_event: threading.Event | None = None):
    lib = []
    # pre-count (nice progress)
    total = 0
    for root, _, files in os.walk(folder_path):
        if stop_event and stop_event.is_set():
            log_fn("⚠️ Scan cancelled.\n")
            return lib
        for fname in files:
            if fname.lower().endswith(VALID_EXTENSIONS):
                total += 1

    count = 0
    log_fn(f"Scanning folder: {folder_path}\nTotal candidates: {total}\n")
    for root, _, files in os.walk(folder_path):
        if stop_event and stop_event.is_set():
            log_fn("⚠️ Scan cancelled.\n")
            return lib
        for fname in files:
            if stop_event and stop_event.is_set():
                log_fn("⚠️ Scan cancelled.\n")
                return lib
            if not fname.lower().endswith(VALID_EXTENSIONS):
                continue
            full_path = os.path.join(root, fname)
            try:
                size, mtime = _file_fingerprint(full_path)

                easy = MutagenFile(full_path, easy=True)
                title_default = os.path.splitext(fname)[0]
                title = _first_tag(easy, "title", title_default)
                artist = _first_tag(easy, "artist", "Unknown")
                album = _first_tag(easy, "album", "Unknown")
                album_artist = _first_tag(easy, "albumartist")
                genre = _first_tag(easy, "genre")
                year = _parse_first_int(_first_tag(easy, "date"))
                track_no = _parse_first_int(_first_tag(easy, "tracknumber"))
                disc_no = _parse_first_int(_first_tag(easy, "discnumber"))
                composer = _first_tag(easy, "composer")
                bpm = _parse_float(_first_tag(easy, "bpm"))
                musical_key = _first_tag(easy, "initialkey") or _first_tag(easy, "key")
                codec, sample_rate, bit_depth, bitrate_kbps, channels = _technical_info(full_path)
                dur = _duration_seconds(full_path)
                acoustid_fp, acoustid_dur = _acoustid_fingerprint(full_path)
                rel = os.path.relpath(full_path, folder_path).replace(os.sep, "/")

                lib.append({
                    "title": title,
                    "artist": artist,
                    "album": album,
                    "album_artist": album_artist,
                    "genre": genre,
                    "year": year,
                    "track_no": track_no,
                    "disc_no": disc_no,
                    "composer": composer,
                    "bpm": bpm,
                    "musical_key": musical_key,
                    "codec": codec,
                    "sample_rate": sample_rate,
                    "bit_depth": bit_depth,
                    "bitrate_kbps": bitrate_kbps,
                    "channels": channels,
                    "path": full_path,
                    "rel_path": rel,
                    "file_size": size,
                    "mtime": mtime,
                    "duration_sec": dur,
                    "acoustid_fingerprint": acoustid_fp,
                    "acoustid_duration": acoustid_dur,
                    "track_id": _track_id(full_path, size, mtime),
                })
                count += 1
                if count % 25 == 0 or count == total:
                    log_fn(f"  • Scanned {count}/{total} ...\n")
            except Exception as e:
                log_fn(f"❌ {full_path}: {e}\n")
    return lib

def send_in_batches(
    user_id: str,
    tracks: list,
    log_fn,
    batch_size=SCAN_BATCH_SIZE,
    replace=False,
    stop_event: threading.Event | None = None,
):
    if not tracks:
        log_fn("Nothing to send.\n")
        return
    version = int(time.time())
    total = len(tracks)
    for i in range(0, total, batch_size):
        if stop_event and stop_event.is_set():
            log_fn("⚠️ Upload cancelled.\n")
            break
        chunk = tracks[i:i+batch_size]
        payload = {
            "user_id": user_id,
            "library": chunk,
            "library_version": version,
            "replace": bool(replace and i == 0),  # clear on FIRST batch only
        }
        ok = False
        last_err = None
        for attempt in range(1, SCAN_SUBMIT_RETRIES + 1):
            try:
                r = requests.post(SUBMIT_URL, json=payload, timeout=SCAN_SUBMIT_TIMEOUT_SEC)
                if r.status_code >= 400:
                    raise RuntimeError(f"HTTP {r.status_code}: {r.text[:240]}")
                log_fn(f"✅ Batch {i+1}-{i+len(chunk)} / {total} → {r.status_code} {r.text[:200]}\n")
                ok = True
                break
            except Exception as e:
                last_err = e
                if attempt < SCAN_SUBMIT_RETRIES:
                    wait = SCAN_RETRY_BACKOFF_SEC * attempt
                    log_fn(
                        f"⚠️ Batch {i+1}-{i+len(chunk)} attempt {attempt}/{SCAN_SUBMIT_RETRIES} failed: {e}. "
                        f"Retrying in {wait:.0f}s...\n"
                    )
                    time.sleep(wait)
        if not ok:
            log_fn(
                f"❌ Batch {i+1}-{i+len(chunk)} failed after {SCAN_SUBMIT_RETRIES} attempts: {last_err}\n"
            )
            break


def _post_scan_chunk(
    user_id: str,
    chunk: list,
    version: int,
    replace: bool,
    log_fn,
    range_start: int,
    range_end: int,
    total: int,
) -> bool:
    payload = {
        "user_id": user_id,
        "library": chunk,
        "library_version": version,
        "replace": bool(replace),
    }
    last_err = None
    for attempt in range(1, SCAN_SUBMIT_RETRIES + 1):
        try:
            r = requests.post(SUBMIT_URL, json=payload, timeout=SCAN_SUBMIT_TIMEOUT_SEC)
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:240]}")
            log_fn(f"✅ Batch {range_start}-{range_end} / {total} → {r.status_code} {r.text[:200]}\n")
            return True
        except Exception as e:
            last_err = e
            if attempt < SCAN_SUBMIT_RETRIES:
                wait = SCAN_RETRY_BACKOFF_SEC * attempt
                log_fn(
                    f"⚠️ Batch {range_start}-{range_end} attempt {attempt}/{SCAN_SUBMIT_RETRIES} failed: {e}. "
                    f"Retrying in {wait:.0f}s...\n"
                )
                time.sleep(wait)
    log_fn(f"❌ Batch {range_start}-{range_end} failed after {SCAN_SUBMIT_RETRIES} attempts: {last_err}\n")
    return False


def scan_and_send_incremental(
    user_id: str,
    folder_path: str,
    log_fn,
    batch_size: int = SCAN_BATCH_SIZE,
    replace: bool = False,
    stop_event: threading.Event | None = None,
):
    root_abs = os.path.abspath(folder_path)
    version = int(time.time())
    candidates = list(_iter_audio_files(root_abs))
    total = len(candidates)
    resume_state = {}
    resume_from = 0
    replace_next = bool(replace)
    uploaded = 0
    failed = False

    if stop_event and stop_event.is_set():
        log_fn("⚠️ Scan cancelled.\n")
        return {"scanned": 0, "uploaded": 0, "failed": False}

    if SCAN_RESUME_ENABLED:
        if SCAN_RESUME_RESET:
            try:
                _scan_resume_file(user_id).unlink()
                log_fn("♻️ Scan resume checkpoint reset.\n")
            except FileNotFoundError:
                pass
            except Exception:
                pass
        resume_state = _load_scan_resume(user_id)
        if (
            resume_state.get("folder_path") == root_abs
            and not bool(resume_state.get("completed", False))
        ):
            resume_from = max(0, int(resume_state.get("committed_scanned", 0) or 0))
            uploaded = max(0, int(resume_state.get("uploaded", 0) or 0))
            replace_next = bool(resume_state.get("replace_next", replace))
            if resume_from > 0:
                log_fn(f"↩️ Resuming scan from {resume_from}/{total} committed files.\n")
        else:
            resume_state = {
                "user_id": user_id,
                "folder_path": root_abs,
                "version": version,
                "total_candidates": total,
                "committed_scanned": 0,
                "uploaded": 0,
                "replace_next": bool(replace),
                "completed": False,
                "updated_at": int(time.time()),
            }
            _save_scan_resume(user_id, resume_state)

    log_fn(f"Scanning folder: {root_abs}\nTotal candidates: {total}\n")
    scanned = resume_from
    chunk: list[dict] = []
    for idx in range(resume_from, total):
        if stop_event and stop_event.is_set():
            log_fn("⚠️ Scan cancelled.\n")
            return {"scanned": scanned, "uploaded": uploaded, "failed": failed}
        full_path = candidates[idx]
        fname = os.path.basename(full_path)
        try:
            size, mtime = _file_fingerprint(full_path)

            easy = MutagenFile(full_path, easy=True)
            title_default = os.path.splitext(fname)[0]
            title = _first_tag(easy, "title", title_default)
            artist = _first_tag(easy, "artist", "Unknown")
            album = _first_tag(easy, "album", "Unknown")
            album_artist = _first_tag(easy, "albumartist")
            genre = _first_tag(easy, "genre")
            year = _parse_first_int(_first_tag(easy, "date"))
            track_no = _parse_first_int(_first_tag(easy, "tracknumber"))
            disc_no = _parse_first_int(_first_tag(easy, "discnumber"))
            composer = _first_tag(easy, "composer")
            bpm = _parse_float(_first_tag(easy, "bpm"))
            musical_key = _first_tag(easy, "initialkey") or _first_tag(easy, "key")
            codec, sample_rate, bit_depth, bitrate_kbps, channels = _technical_info(full_path)
            dur = _duration_seconds(full_path)
            acoustid_fp, acoustid_dur = _acoustid_fingerprint(full_path)
            rel = os.path.relpath(full_path, root_abs).replace(os.sep, "/")

            chunk.append({
                "title": title,
                "artist": artist,
                "album": album,
                "album_artist": album_artist,
                "genre": genre,
                "year": year,
                "track_no": track_no,
                "disc_no": disc_no,
                "composer": composer,
                "bpm": bpm,
                "musical_key": musical_key,
                "codec": codec,
                "sample_rate": sample_rate,
                "bit_depth": bit_depth,
                "bitrate_kbps": bitrate_kbps,
                "channels": channels,
                "path": full_path,
                "rel_path": rel,
                "file_size": size,
                "mtime": mtime,
                "duration_sec": dur,
                "acoustid_fingerprint": acoustid_fp,
                "acoustid_duration": acoustid_dur,
                "track_id": _track_id(full_path, size, mtime),
            })
            scanned += 1
            if scanned % 25 == 0 or scanned == total:
                log_fn(f"  • Scanned {scanned}/{total} ...\n")

            if len(chunk) >= batch_size:
                start = scanned - len(chunk) + 1
                end = scanned
                if not _post_scan_chunk(
                    user_id=user_id,
                    chunk=chunk,
                    version=version,
                    replace=replace_next,
                    log_fn=log_fn,
                    range_start=start,
                    range_end=end,
                    total=total,
                ):
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
                    log_fn(f"⏸️ Paused after failed batch {start}-{end}; restart will resume.\n")
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
            log_fn(f"❌ {full_path}: {e}\n")

    if chunk:
        start = scanned - len(chunk) + 1
        end = scanned
        if not _post_scan_chunk(
            user_id=user_id,
            chunk=chunk,
            version=version,
            replace=replace_next,
            log_fn=log_fn,
            range_start=start,
            range_end=end,
            total=total,
        ):
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
            log_fn(f"⏸️ Final partial batch {start}-{end} failed; restart will resume.\n")
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

def _remember_root(path: str):
    st = read_state()
    st["last_root"] = path
    write_state(st)

def tailscale_ok() -> bool:
    try:
        import subprocess
        out = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=2)
        ip = (out.stdout or "").strip().splitlines()[0] if out.returncode == 0 else ""
        return ip.startswith("100.")
    except Exception:
        return False

class _ServerHolder:
    """Keeps one LocalFileServer instance alive across operations."""
    def __init__(self):
        self.server = None

def _start_local_server(root: str, log_fn, holder: _ServerHolder, port: int, public_base_url: str | None) -> str:
    if holder.server:
        try:
            holder.server.stop()
        except Exception:
            pass
    holder.server = LocalFileServer(root_dir=root, port=port, public_base_url=public_base_url)
    holder.server.start()
    base = holder.server.base_url()
    log_fn(f"📡 Local file server: {base}\n")
    return base


# ---------- GUI ----------
class AgentGUI:
    def __init__(self, root):
        self.root = root
        self.root.title(f"{APP_NAME} v{APP_VERSION}")

        # single pattern: keep a direct reference to the server
        self.file_server = None
        self.tunnel = None
        self._hb_stop = None  # heartbeat stopper
        self._scan_thread = None
        self._scan_stop = None

        row = 0
        # user label inside the app
        tk.Label(root, text=f"User: {USER_ID}", font=("Helvetica", 11, "bold")).grid(
            row=row, column=0, sticky="w", padx=10, pady=(10,4), columnspan=2
        )
        row += 1

        # Music folder selector
        self.path_var = tk.StringVar(value=_default_root())
        tk.Label(root, text="Music folder:").grid(row=row, column=0, sticky="w", padx=10, pady=6)
        self.e_path = tk.Entry(root, textvariable=self.path_var, width=56)
        self.e_path.grid(row=row, column=1, padx=6, pady=6, sticky="we")
        tk.Button(root, text="Browse", command=self.browse).grid(row=row, column=2, padx=4)
        row += 1

        # Actions
        self.btn_scan = tk.Button(root, text="Scan and Send", command=self.scan_and_send)
        self.btn_scan.grid(row=row, column=0, pady=8, sticky="w", padx=(10, 6))
        self.btn_open = tk.Button(root, text="Open Library", command=self.open_library)
        self.btn_open.grid(row=row, column=1, pady=8, sticky="w")
        row += 1

        # Log window
        self.log_box = scrolledtext.ScrolledText(root, width=78, height=20)
        self.log_box.grid(row=row, column=0, columnspan=3, padx=10, pady=8, sticky="nsew")
        row += 1

        # Status bar with Tailscale LED + connection state
        self.status_var = tk.StringVar(value="Status: starting…")
        self.led = tk.Canvas(root, width=12, height=12, highlightthickness=0)
        self.led.grid(row=row, column=0, sticky="w", padx=(10,4))
        tk.Label(root, textvariable=self.status_var).grid(row=row, column=1, sticky="w")
        row += 1

        # grid weights
        root.grid_columnconfigure(1, weight=1)
        root.grid_rowconfigure(3, weight=1)  # log box grows

        # close handler to stop server + heartbeat cleanly
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # auto-connect (serve + announce remembered root) shortly after UI shows
        self.root.after(300, self.auto_connect)

    # ----------------- heartbeat -----------------
    def _start_heartbeat(self, base_url: str):
        # avoid multiple concurrent heartbeats
        if self._hb_stop is not None:
            return
        self._hb_stop = threading.Event()

        def beat():
            while not self._hb_stop.is_set():
                try:
                    requests.post(ANNOUNCE_URL, json={"user_id": USER_ID, "base_url": base_url}, timeout=5)
                except Exception:
                    pass
                # LED refresh: green if tailscale looks ok
                self._set_led(tailscale_ok())
                self._hb_stop.wait(60)  # every 60s

        threading.Thread(target=beat, daemon=True).start()

    def _stop_heartbeat(self):
        if self._hb_stop:
            self._hb_stop.set()
            self._hb_stop = None

    # ----------------- ui helpers -----------------
    def log(self, s:str):
        self.log_box.insert(tk.END, s)
        self.log_box.see(tk.END)
        self.root.update_idletasks()

    def _set_led(self, ok: bool):
        self.led.delete("all")
        color = "#16a34a" if ok else "#dc2626"  # green / red
        self.led.create_oval(2, 2, 10, 10, fill=color, outline=color)

    def browse(self):
        initial = self.path_var.get() or _default_root()
        folder = filedialog.askdirectory(initialdir=initial, mustexist=True)
        if folder:
            self.path_var.set(folder)

    def open_library(self):
        play_url = urljoin(API_BASE, f"user/{USER_ID}/play")
        try:
            webbrowser.open(play_url)
            self.log(f"🌐 Opened library: {play_url}\n")
        except Exception as e:
            self.log(f"❌ Could not open browser: {e}\n")

    # ----------------- server lifecycle -----------------
    def _start_local_server(self, path: str, public_base_url: str | None = None) -> str:
        if self.file_server:
            # restart on same/different path (cheap)
            try:
                self.file_server.stop()
            except Exception:
                pass
            self.file_server = None

        base_override = public_base_url if public_base_url is not None else PUBLIC_BASE_URL
        self.file_server = LocalFileServer(root_dir=path, port=DEFAULT_PORT, public_base_url=base_override)
        try:
            self.file_server.start()
            base = self.file_server.base_url()
            self.log(f"📡 Local file server: {base}\n")
            return base
        except OSError as e:
            # Dev-safe behavior: if another agent already owns the port, reuse it.
            if getattr(e, "errno", None) == 98:
                existing_base = base_override or f"http://127.0.0.1:{DEFAULT_PORT}"
                self.log(
                    f"⚠️ Port {DEFAULT_PORT} already in use. Reusing existing local server at {existing_base}\n"
                )
                self.file_server = None
                return existing_base
            raise

    def stop_server(self):
        try:
            self._stop_heartbeat()
            if self.file_server:
                self.file_server.stop()
                self.file_server = None
            if self.tunnel:
                self.tunnel.stop()
                self.tunnel = None
        except Exception:
            pass

    def on_close(self):
        if self._scan_stop:
            self._scan_stop.set()
        self.stop_server()
        self.root.destroy()

    # ----------------- flows -----------------
    def auto_connect(self):
        path = _default_root()
        self.path_var.set(path)

        # initial LED
        self._set_led(tailscale_ok())

        try:
            # Start reverse tunnel if enabled (best-effort)
            self.tunnel, tunnel_base = start_tunnel_from_env(DEFAULT_PORT, log_fn=self.log)
            base = self._start_local_server(path, public_base_url=tunnel_base)
            announce_agent(USER_ID, base, self.log)
            self._start_heartbeat(base)
            _remember_root(path)
            self.status_var.set("Status: connected (serving)")
        except Exception as e:
            self.status_var.set("Status: error starting file server")
            self.log(f"❌ Could not start local file server: {e}\n")

    def scan_and_send(self):
        # Prevent overlapping scans that interleave progress and corrupt UX.
        if self._scan_thread and self._scan_thread.is_alive():
            if not messagebox.askyesno(
                "Scan already running",
                "A scan/upload is already running.\n\nStop it and start a new one?",
            ):
                return
            if self._scan_stop:
                self._scan_stop.set()
            self.log("⚠️ Requested stop of current scan. Starting new scan...\n")

        path = self.path_var.get()
        if not os.path.isdir(path):
            messagebox.showerror("Error", "Invalid folder path")
            return

        # warm NAS mount
        try:
            _ = os.listdir(path)
        except Exception as e:
            self.log(f"⚠️ Could not list '{path}': {e}\n")

        prev_root = get_last_root()
        replacing = False

        if prev_root and os.path.abspath(prev_root) != os.path.abspath(path):
            # switched root → recommend replace
            msg = (
                "You selected a NEW library root.\n\n"
                "This will REPLACE the previously uploaded library on the server.\n"
                "Proceed with a full re-scan and replace?"
            )
            replacing = messagebox.askyesno("Switch library root", msg, icon="warning")
            if not replacing:
                if not messagebox.askyesno("Rescan without replacing?",
                                           "Proceed to rescan and append (no clear)?"):
                    return
        else:
            # same root → ask whether to CLEAR existing first
            replacing = messagebox.askyesno(
                "Rescan library",
                "Do you want to CLEAR the existing server library first?\n\n"
                "Yes = hard reset, No = append/update"
            )

        # (re)start local file server & announce (ensures base_url fresh)
        try:
            base = self._start_local_server(path)
            announce_agent(USER_ID, base, self.log)
            self._start_heartbeat(base)
        except Exception as e:
            self.log(f"❌ Could not start local file server: {e}\n")
            return

        self._scan_stop = threading.Event()

        self.btn_scan.config(state="disabled")
        self.status_var.set("Status: scanning...")

        def work():
            try:
                result = scan_and_send_incremental(
                    USER_ID,
                    path,
                    self.log,
                    batch_size=SCAN_BATCH_SIZE,
                    replace=replacing,
                    stop_event=self._scan_stop,
                )
                if self._scan_stop and self._scan_stop.is_set():
                    self.status_var.set("Status: scan cancelled")
                    return
                self.log(
                    f"🎵 Scan complete. valid={result['scanned']} uploaded={result['uploaded']} "
                    f"failed={'yes' if result['failed'] else 'no'}\n"
                )
                if not result["failed"] and result["uploaded"] > 0:
                    run_post_scan_enrichment(USER_ID, self.log)
                _remember_root(path)
                if self._scan_stop and self._scan_stop.is_set():
                    self.status_var.set("Status: upload cancelled")
                elif result["failed"]:
                    self.status_var.set("Status: connected (partial upload)")
                else:
                    self.status_var.set("Status: connected (scanned)")
            finally:
                self.root.after(0, lambda: self.btn_scan.config(state="normal"))
                self._scan_stop = None
                self._scan_thread = None

        self._scan_thread = threading.Thread(target=work, daemon=True)
        self._scan_thread.start()

# ---------- headless ----------
def run_headless():
    path = os.getenv("LIBRARY_PATH") or _default_root()
    if not path or not os.path.isdir(path):
        print("❌ Headless mode requires LIBRARY_PATH (or remembered path) to a valid directory.")
        return
    fs = LocalFileServer(root_dir=path, port=DEFAULT_PORT, public_base_url=PUBLIC_BASE_URL)
    fs.start()
    base = fs.base_url()
    print(f"📡 Local file server: {base}")
    announce_agent(USER_ID, base, print)
    # remember root
    st = read_state(); st["last_root"] = path; write_state(st)
    result = scan_and_send_incremental(
        USER_ID,
        path,
        print,
        batch_size=SCAN_BATCH_SIZE,
        replace=False,
    )
    print(
        f"🎵 Scan complete. valid={result['scanned']} uploaded={result['uploaded']} "
        f"failed={'yes' if result['failed'] else 'no'}"
    )
    if not result["failed"] and result["uploaded"] > 0:
        run_post_scan_enrichment(USER_ID, print)
    print("✅ Done. Press Ctrl+C to quit; server stays up so streaming works.")
    try:
        while True: time.sleep(3600)
    except KeyboardInterrupt:
        fs.stop()

if __name__ == "__main__":
    # If no DISPLAY (Linux/macOS) → headless; Windows still shows GUI.
    if not os.environ.get("DISPLAY") and os.name != "nt":
        run_headless()
    else:
        no_splash = os.getenv("RT_NO_SPLASH") == "1"
        root = tk.Tk()
        if not no_splash:
            root.withdraw()
            splash = Toplevel(root)
            splash.title("Loading…")
            splash.geometry("300x150+500+300")
            splash.overrideredirect(True)
            Label(splash, text=f"{APP_NAME} v{APP_VERSION}", font=("Helvetica", 14)).pack(pady=20)
            Label(splash, text="Starting up…").pack()
            def start_app():
                try: splash.destroy()
                except Exception: pass
                root.deiconify()
                AgentGUI(root)
            root.after(1200, start_app)
        else:
            AgentGUI(root)
        root.mainloop()
