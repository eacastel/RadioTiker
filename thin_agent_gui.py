import os, time, threading, hashlib, json
from pathlib import Path
from dotenv import load_dotenv
from mutagen import File as MutagenFile
import requests

import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox, Toplevel, Label
from urllib.parse import urljoin

from local_file_server import LocalFileServer

APP_NAME = "RadioTiker Thin Agent"
APP_VERSION = "0.3"

load_dotenv()

# --- API endpoints (proxied via NGINX) ---
API_BASE    = os.getenv("SERVER_BASE", "https://radio.tiker.es/streamer/api/")
SUBMIT_URL  = urljoin(API_BASE, "submit-scan")
ANNOUNCE_URL= urljoin(API_BASE, "agent/announce")

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

def announce_agent(user_id: str, base_url: str, log_fn):
    payload = {"user_id": user_id, "base_url": base_url}
    try:
        r = requests.post(ANNOUNCE_URL, json=payload, timeout=10)
        log_fn(f"📣 Announce: {r.status_code} {r.text[:200]}\n")
    except Exception as e:
        log_fn(f"❌ Announce failed: {e}\n")

def scan_folder(folder_path, log_fn):
    lib = []
    # pre-count (nice progress)
    total = 0
    for root, _, files in os.walk(folder_path):
        for fname in files:
            if fname.lower().endswith(VALID_EXTENSIONS):
                total += 1

    count = 0
    log_fn(f"Scanning folder: {folder_path}\nTotal candidates: {total}\n")
    for root, _, files in os.walk(folder_path):
        for fname in files:
            if not fname.lower().endswith(VALID_EXTENSIONS):
                continue
            full_path = os.path.join(root, fname)
            try:
                size, mtime = _file_fingerprint(full_path)

                easy = MutagenFile(full_path, easy=True)
                title  = (easy.get("title",  [os.path.splitext(fname)[0]]) or [None])[0] if easy else os.path.splitext(fname)[0]
                artist = (easy.get("artist", ["Unknown"])              or ["Unknown"])[0] if easy else "Unknown"
                album  = (easy.get("album",  ["Unknown"])              or ["Unknown"])[0] if easy else "Unknown"

                dur = _duration_seconds(full_path)
                rel = os.path.relpath(full_path, folder_path).replace(os.sep, "/")

                lib.append({
                    "title": title,
                    "artist": artist,
                    "album": album,
                    "path": full_path,
                    "rel_path": rel,
                    "file_size": size,
                    "mtime": mtime,
                    "duration_sec": dur,
                    "track_id": _track_id(full_path, size, mtime),
                })
                count += 1
                if count % 25 == 0 or count == total:
                    log_fn(f"  • Scanned {count}/{total} ...\n")
            except Exception as e:
                log_fn(f"❌ {full_path}: {e}\n")
    return lib

def send_in_batches(user_id: str, tracks: list, log_fn, batch_size=300, replace=False):
    if not tracks:
        log_fn("Nothing to send.\n")
        return
    version = int(time.time())
    total = len(tracks)
    for i in range(0, total, batch_size):
        chunk = tracks[i:i+batch_size]
        payload = {
            "user_id": user_id,
            "library": chunk,
            "library_version": version,
            "replace": bool(replace and i == 0),  # clear on FIRST batch only
        }
        try:
            r = requests.post(SUBMIT_URL, json=payload, timeout=90)
            log_fn(f"✅ Batch {i+1}-{i+len(chunk)} / {total} → {r.status_code} {r.text[:200]}\n")
        except Exception as e:
            log_fn(f"❌ Batch {i+1}-{i+len(chunk)} failed: {e}\n")
            break

# ---------- GUI ----------
class AgentGUI:
    def __init__(self, root):
        self.root = root
        self.root.title(f"{APP_NAME} v{APP_VERSION} — user: {USER_ID}")

        # Set initial value ONCE with default root
        self.path_var = tk.StringVar(value=_default_root())

        row = 0
        tk.Label(root, text="Music folder:").grid(row=row, column=0, sticky="w", padx=10, pady=6)
        self.e_path = tk.Entry(root, textvariable=self.path_var, width=56)
        self.e_path.grid(row=row, column=1, padx=6, pady=6)
        tk.Button(root, text="Browse", command=self.browse).grid(row=row, column=2, padx=4)
        row += 1

        self.btn = tk.Button(root, text="Scan and Send", command=self.scan_and_send)
        self.btn.grid(row=row, column=0, columnspan=3, pady=8)
        row += 1

        self.log_box = scrolledtext.ScrolledText(root, width=78, height=20)
        self.log_box.grid(row=row, column=0, columnspan=3, padx=10, pady=8)

        self.file_server = None

    def log(self, s:str):
        self.log_box.insert(tk.END, s)
        self.log_box.see(tk.END)
        self.root.update_idletasks()

    def browse(self):
        initial = self.path_var.get() or _default_root()
        folder = filedialog.askdirectory(initialdir=initial, mustexist=True)
        if folder:
            self.path_var.set(folder)

    def scan_and_send(self):
        path = self.path_var.get()
        if not os.path.isdir(path):
            messagebox.showerror("Error", "Invalid folder path")
            return

        # Warm up automounts (NAS) so the first list doesn’t look empty
        try:
            _ = os.listdir(path)
        except Exception as e:
            self.log(f"⚠️ Could not list '{path}': {e}\n")

        # Detect root change
        prev_root = get_last_root()
        replacing = False
        if prev_root and os.path.abspath(prev_root) != os.path.abspath(path):
            msg = (
                "You selected a NEW library root.\n\n"
                "This will REPLACE the previously uploaded library on the server.\n"
                "Proceed with a full re-scan?"
            )
            if not messagebox.askyesno("Switch library root", msg, icon="warning"):
                return
            replacing = True

        # start local file server + announce
        try:
            if self.file_server:
                self.file_server.stop()
            self.file_server = LocalFileServer(root_dir=path, port=DEFAULT_PORT, public_base_url=PUBLIC_BASE_URL)
            self.file_server.start()
            base = self.file_server.base_url()
            self.log(f"📡 Local file server: {base}\n")
            announce_agent(USER_ID, base, self.log)
        except Exception as e:
            self.log(f"❌ Could not start local file server: {e}\n")
            return

        def work():
            tracks = scan_folder(path, self.log)
            self.log(f"🎵 Found {len(tracks)} valid audio files.\n")

            # Remember root (always)
            st = read_state()
            st["last_root"] = path
            write_state(st)

            # Send in batches; first batch clears server if replacing
            send_in_batches(USER_ID, tracks, self.log, batch_size=500, replace=replacing)

        threading.Thread(target=work, daemon=True).start()

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
    tracks = scan_folder(path, print)
    print(f"🎵 Found {len(tracks)} tracks. Sending to server in batches…")
    send_in_batches(USER_ID, tracks, print, batch_size=500, replace=False)
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
