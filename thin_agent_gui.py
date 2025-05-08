# thin_agent_gui.py

import os
import json
import requests
from mutagen import File
from dotenv import load_dotenv
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox

load_dotenv()

SERVER_URL = os.getenv("SERVER_URL", "https://radio.tiker.es/api/submit-scan")
USER_ID = os.getenv("USER_ID", "test-user-001")
VALID_EXTENSIONS = tuple(os.getenv("VALID_AUDIO_EXTENSIONS", ".mp3,.flac,.wav,.m4a").split(","))


def scan_folder(folder_path, log_fn):
    library = []
    log_fn(f"Scanning folder: {folder_path}\n")
    for root, _, files in os.walk(folder_path):
        for file in files:
            if not file.lower().endswith(VALID_EXTENSIONS):
                continue
            full_path = os.path.join(root, file)
            try:
                audio = File(full_path, easy=True)
                metadata = {
                    "title": audio.get("title", [file])[0],
                    "artist": audio.get("artist", ["Unknown"])[0],
                    "album": audio.get("album", ["Unknown"])[0],
                    "path": full_path
                }
                library.append(metadata)
            except Exception as e:
                log_fn(f"❌ Error reading {full_path}: {e}")
    return library


def send_to_server(tracks, log_fn):
    payload = {
        "user_id": USER_ID,
        "library": tracks
    }
    try:
        response = requests.post(SERVER_URL, json=payload)
        log_fn(f"✅ Server responded: {response.status_code} {response.text}\n")
    except Exception as e:
        log_fn(f"❌ Error sending to server: {e}\n")


class AgentGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("RadioTiker Thin Agent")

        self.path_var = tk.StringVar()
        self.path_entry = tk.Entry(root, textvariable=self.path_var, width=50)
        self.path_entry.grid(row=0, column=0, padx=10, pady=10)

        self.browse_btn = tk.Button(root, text="Browse", command=self.browse_folder)
        self.browse_btn.grid(row=0, column=1, padx=5)

        self.scan_btn = tk.Button(root, text="Scan and Send", command=self.scan_and_send)
        self.scan_btn.grid(row=1, column=0, columnspan=2, pady=10)

        self.log_box = scrolledtext.ScrolledText(root, width=70, height=20)
        self.log_box.grid(row=2, column=0, columnspan=2, padx=10, pady=5)

    def browse_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.path_var.set(folder)

    def scan_and_send(self):
        path = self.path_var.get()
        if not os.path.isdir(path):
            messagebox.showerror("Error", "Invalid folder path")
            return
        tracks = scan_folder(path, self.log)
        self.log(f"🎵 Found {len(tracks)} valid audio files.\n")
        send_to_server(tracks, self.log)

    def log(self, message):
        self.log_box.insert(tk.END, message)
        self.log_box.see(tk.END)


if __name__ == "__main__":
    root = tk.Tk()
    app = AgentGUI(root)
    root.mainloop()

