# scanner.py
import os
import json
from mutagen import File

VALID_AUDIO_EXTENSIONS = ('.mp3', '.flac', '.wav', '.m4a')

def is_valid_audio(file_path):
    return file_path.lower().endswith(VALID_AUDIO_EXTENSIONS)

def scan_folder(music_path):
    tracks = []
    for root, _, files in os.walk(music_path):
        for filename in files:
            if not is_valid_audio(filename):
                continue
            path = os.path.join(root, filename)
            try:
                audio = File(path, easy=True)
                if audio is None:
                    continue
                metadata = {
                    "title": audio.get("title", [os.path.splitext(filename)[0]])[0],
                    "artist": audio.get("artist", ["Unknown"])[0],
                    "album": audio.get("album", ["Unknown"])[0],
                    "filepath": path
                }
                tracks.append(metadata)
            except Exception as e:
                print(f"Error reading {path}: {e}")
    return tracks

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scan a folder for audio files.")
    parser.add_argument("path", help="Path to music folder")
    parser.add_argument("--output", default="scanned_library.json", help="Output JSON file")

    args = parser.parse_args()
    result = scan_folder(args.path)
    with open(args.output, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Scanned {len(result)} files and saved to {args.output}")

