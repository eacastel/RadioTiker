import musicbrainzngs
import argparse
import json
import sys
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

APP_NAME = os.getenv("APP_NAME", "RadioTiker")
APP_VERSION = os.getenv("APP_VERSION", "0.1")
APP_DOMAIN = os.getenv("APP_DOMAIN", "http://localhost")

# Initialize MusicBrainz with dynamic values
musicbrainzngs.set_useragent(APP_NAME, APP_VERSION, APP_DOMAIN)

def enrich_metadata(title, artist=None, album=None):
    try:
        result = musicbrainzngs.search_recordings(
            recording=title,
            artist=artist or "",
            release=album or "",
            limit=1
        )
        recordings = result.get('recording-list', [])
        if not recordings:
            return None

        rec = recordings[0]
        enriched = {
            "title": rec.get("title"),
            "artist": rec["artist-credit"][0]["name"] if rec.get("artist-credit") else None,
            "album": rec["release-list"][0]["title"] if rec.get("release-list") else None,
            "year": rec["release-list"][0].get("date", "").split("-")[0] if rec.get("release-list") else None,
            "musicbrainz_id": rec.get("id")
        }
        return enriched
    except Exception as e:
        print(f"Error fetching metadata: {e}", file=sys.stderr)
        return None

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch enriched metadata from MusicBrainz.")
    parser.add_argument("--title", required=True, help="Track title")
    parser.add_argument("--artist", help="Track artist")
    parser.add_argument("--album", help="Track album")

    args = parser.parse_args()

    enriched = enrich_metadata(args.title, args.artist, args.album)
    print(json.dumps(enriched, indent=2) if enriched else "No match found.")

