import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

MOUNTED_MUSIC_DIR = os.getenv("MOUNTED_MUSIC_DIR", ".")
VALID_AUDIO_EXTENSIONS = tuple(ext.strip().lower() for ext in os.getenv("VALID_AUDIO_EXTENSIONS", ".mp3,.flac,.wav").split(","))

def is_valid_audio_file(file_path):
    return file_path.lower().endswith(VALID_AUDIO_EXTENSIONS)

def scan_music_folder(folder_path):
    print(f"Scanning: {folder_path}")
    for root, _, files in os.walk(folder_path):
        for file in files:
            if is_valid_audio_file(file):
                full_path = os.path.join(root, file)
                print(full_path)

if __name__ == "__main__":
    folder = sys.argv[1] if len(sys.argv) > 1 else MOUNTED_MUSIC_DIR
    scan_music_folder(folder)

