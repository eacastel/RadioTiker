# thin_agent_gui.py
import os, time, threading, hashlib, json
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from mutagen import File as MutagenFile
import requests

import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox, Toplevel, Label

from local_file_server import LocalFileServer

APP_NAME = "RadioTiker Thin Agent"
APP_VERSION = "0.2"

load_dotenv()

# NOTE: points to the proxied FastAPI path
SERVER_URL = os.getenv("SERVER_URL", "https://radio.tiker.es/streamer/api/submit-scan")

CONF_DIR = Path.home() / ".radiotiker"
CONF_DIR.mkdir(parents=True, exist_ok=True)
CONF_FILE = CONF_DIR / "agent.json"

VALID_EXTENSIONS = tuple(
    ext.strip().lower()
    for ext in os.getenv("VALID_AUDIO_EXTENSIONS", ".mp3,.flac,.wav,.m4a").split(",")
    if ext.strip()
)

DEFAULT_PORT = int(os.getenv("AGENT_PORT", "8765"))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL")  # optional override (e.g., Cloudflare Tunnel)

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
        return env_val
    if CONF_FILE.exists():
        try:
            return json.loads(CONF_FILE.read_text()).get("user_id") or "user-001"
        except Exception:
            pass
    uid = get_user_id_interactive("user-001")
    try:
        CONF_FILE.write_text(json.dumps({"user_id": uid}, indent=2))
    except Exception:
        pass
    return uid

USER_ID = load_or_prompt_user_id()

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

# --- NEW: tell server our base_url so it can build stream URLs dynamically ---
def announce_agent(user_id: str, base_url: str, log_fn):
    # derive /api/agent/announce from SERVER_URL
    from urllib.parse import urljoin
    announce_url = urljoin(SERVER_URL, "../agent/announce")  # -> /streamer/api/agent/announce
    payload = {"user_id": user_id, "base_url": base_url}
    try:
        r = requests.post(announce_url, json=payload, timeout=10)
        log_fn(f"📣 Announce: {r.status_code} {r.text[:200]}\n")
    except Exception as e:
        log_fn(f"❌ Announce failed: {e}\n")

def scan_folder(folder_path, log_fn):
    lib = []
    # pre-count
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

                rel = os.path.relpath(full_path, folder_path).replace(os.sep, "/")  # <— NEW

                rec = {
                    "title": title,
                    "artist": artist,
                    "album": album,
                    "path": full_path,
                    "rel_path": rel,                 # <— NEW (no stream_url)
                    "file_size": size,
                    "mtime": mtime,
                    "duration_sec": dur,
                    "track_id": _track_id(full_path, size, mtime),
                }
                lib.append(rec)
                count += 1
                if count % 25 == 0 or count == total:
                    log_fn(f"  • Scanned {count}/{total} ...\n")
            except Exception as e:
                log_fn(f"❌ {full_path}: {e}\n")
    return lib

def send_to_server(user_id: str, tracks: list, log_fn):
    payload = {"user_id": user_id, "library": tracks, "library_version": int(time.time())}
    try:
        r = requests.post(SERVER_URL, json=payload, timeout=60)
        log_fn(f"✅ Server responded: {r.status_code} {r.text[:300]}\n")
    except Exception as e:
        log_fn(f"❌ Error sending to server: {e}\n")

class AgentGUI:
    def __init__(self, root):
        self.root = root
        self.root.title(f"{APP_NAME} v{APP_VERSION} — user: {USER_ID}")

        self.path_var = tk.StringVar()
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
        folder = filedialog.askdirectory()
        if folder:
            self.path_var.set(folder)

    def scan_and_send(self):
        path = self.path_var.get()
        if not os.path.isdir(path):
            messagebox.showerror("Error", "Invalid folder path")
            return

        try:
            if self.file_server:
                self.file_server.stop()
            self.file_server = LocalFileServer(root_dir=path, port=DEFAULT_PORT, public_base_url=PUBLIC_BASE_URL)
            self.file_server.start()
            base = self.file_server.base_url()
            self.log(f"📡 Local file server: {base}\n")
            announce_agent(USER_ID, base, self.log)     # <— NEW
        except Exception as e:
            self.log(f"❌ Could not start local file server: {e}\n")
            return

        def work():
            tracks = scan_folder(path, self.log)
            self.log(f"🎵 Found {len(tracks)} valid audio files.\n")
            send_to_server(USER_ID, tracks, self.log)
        threading.Thread(target=work, daemon=True).start()

# --- splash + headless ---
def run_headless():
    path = os.getenv("LIBRARY_PATH")
    if not path or not os.path.isdir(path):
        print("❌ Headless mode requires LIBRARY_PATH to a valid directory.")
        return
    fs = LocalFileServer(root_dir=path, port=DEFAULT_PORT, public_base_url=PUBLIC_BASE_URL)
    fs.start()
    base = fs.base_url()
    print(f"📡 Local file server: {base}")
    announce_agent(USER_ID, base, print)
    tracks = scan_folder(path, print)
    print(f"🎵 Found {len(tracks)} tracks. Sending to server…")
    send_to_server(USER_ID, tracks, print)
    print("✅ Done. Press Ctrl+C to quit; server stays up so streaming works.")
    try:
        while True: time.sleep(3600)
    except KeyboardInterrupt:
        fs.stop()

if __name__ == "__main__":
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
