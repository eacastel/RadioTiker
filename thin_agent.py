import os
import json
import requests
from mutagen import File
from dotenv import load_dotenv

load_dotenv()

SERVER_URL = os.getenv("SERVER_URL")
USER_ID = os.getenv("USER_ID")
LIBRARY_PATH = os.getenv("LIBRARY_PATH", "./Music")
VALID_EXTENSIONS = tuple(os.getenv("VALID_AUDIO_EXTENSIONS", ".mp3,.flac,.wav,.m4a").split(","))


def scan_folder(folder_path):
    library = []
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
                print(f"Error reading {full_path}: {e}")
    return library


def send_to_server(tracks):
    payload = {
        "user_id": USER_ID,
        "library": tracks
    }
    try:
        response = requests.post(SERVER_URL, json=payload)
        print("✅ Server response:", response.status_code, response.text)
    except Exception as e:
        print("❌ Error sending to server:", e)


if __name__ == "__main__":
    print(f"🎵 Scanning: {LIBRARY_PATH}")
    tracks = scan_folder(LIBRARY_PATH)
    print(f"📁 Found {len(tracks)} tracks.")
    send_to_server(tracks)

