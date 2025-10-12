import os
import json
from mutagen import File
from dotenv import load_dotenv
from fetch_metadata import enrich_metadata
import argparse

# Load .env
load_dotenv()

MOUNTED_MUSIC_DIR = os.getenv("MOUNTED_MUSIC_DIR", ".")
VALID_AUDIO_EXTENSIONS = tuple(ext.strip().lower() for ext in os.getenv("VALID_AUDIO_EXTENSIONS", ".mp3,.flac,.wav").split(","))
OUTPUT_ROOT = os.path.join("data", "user-libraries")

def is_valid_audio_file(file_path):
    return file_path.lower().endswith(VALID_AUDIO_EXTENSIONS)

def extract_local_metadata(filepath):
    try:
        audio = File(filepath, easy=True)
        if audio is None:
            return {}
        return {
            "title": audio.get("title", [None])[0],
            "artist": audio.get("artist", [None])[0],
            "album": audio.get("album", [None])[0],
        }
    except Exception as e:
        print(f"Error reading metadata from {filepath}: {e}")
        return {}

def enrich_folder(user_id, folder_path):
    output_dir = os.path.join(OUTPUT_ROOT, user_id)
    os.makedirs(output_dir, exist_ok=True)

    for root, _, files in os.walk(folder_path):
        for file in files:
            if not is_valid_audio_file(file):
                continue
            full_path = os.path.join(root, file)
            print(f"Processing: {full_path}")
            base_metadata = extract_local_metadata(full_path)

            if not base_metadata.get("title"):
                print(f"Skipping {file}: No title found")
                continue

            enriched = enrich_metadata(
                title=base_metadata["title"],
                artist=base_metadata.get("artist"),
                album=base_metadata.get("album")
            )

            if enriched:
                output_path = os.path.join(output_dir, f"{os.path.splitext(file)[0]}.json")
                with open(output_path, "w") as out_file:
                    json.dump(enriched, out_file, indent=2)
                print(f"Saved enriched metadata to {output_path}")
            else:
                print(f"No match found for: {file}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Enrich all audio files in a folder using MusicBrainz.")
    parser.add_argument("--user", required=True, help="User ID (used for output folder)")
    parser.add_argument("--path", help="Path to scan (defaults to MOUNTED_MUSIC_DIR)")

    args = parser.parse_args()
    scan_path = args.path if args.path else MOUNTED_MUSIC_DIR
    enrich_folder(args.user, scan_path)

