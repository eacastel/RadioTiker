# thin_agent.py

import os
import requests
from mutagen import File
from dotenv import load_dotenv
from urllib.parse import quote
from local_file_server import LocalFileServer

load_dotenv()

SERVER_URL = os.getenv("SERVER_URL", "https://radio.tiker.es/streamer/api/submit-scan")
USER_ID = os.getenv("USER_ID", "test-user-001")
LIBRARY_PATH = os.getenv("LIBRARY_PATH", "./Music")
AGENT_PORT = int(os.getenv("AGENT_PORT", "8765"))
VALID_EXTENSIONS = tuple(os.getenv("VALID_AUDIO_EXTENSIONS", ".mp3,.flac,.wav,.m4a").split(","))

def scan_folder(folder_path, base_url, auth_query):
    library = []
    root_abs = os.path.abspath(folder_path)
    for root, _, files in os.walk(root_abs):
        for file in files:
            if not file.lower().endswith(VALID_EXTENSIONS):
                continue
            full_path = os.path.join(root, file)
            try:
                audio = File(full_path, easy=True)
                rel = os.path.relpath(full_path, root_abs)
                rel_url = quote(rel.replace("\\", "/"))
                stream_url = f"{base_url}/{rel_url}?{auth_query}"
                metadata = {
                    "title": audio.get("title", [file])[0] if audio else file,
                    "artist": (audio.get("artist", ["Unknown"])[0] if audio else "Unknown"),
                    "album": (audio.get("album", ["Unknown"])[0] if audio else "Unknown"),
                    "path": full_path,
                    "stream_url": stream_url,
                }
                library.append(metadata)
            except Exception as e:
                print(f"Error reading {full_path}: {e}")
    return library

def send_to_server(user_id, tracks):
    payload = {"user_id": user_id, "library": tracks}
    try:
        response = requests.post(SERVER_URL, json=payload, timeout=10)
        print("✅ Server response:", response.status_code, response.text)
    except Exception as e:
        print("❌ Error sending to server:", e)

if __name__ == "__main__":
    print(f"🎵 Serving & scanning: {LIBRARY_PATH}")
    server = LocalFileServer(root_dir=LIBRARY_PATH, port=AGENT_PORT)
    server.start()
    print(f"🔌 Local server: {server.base_url}")
    tracks = scan_folder(LIBRARY_PATH, server.base_url, server.auth_query)
    print(f"📁 Found {len(tracks)} tracks.")
    send_to_server(USER_ID, tracks)
