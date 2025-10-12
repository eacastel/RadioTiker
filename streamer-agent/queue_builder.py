# queue_builder.py
import json
import random

def load_tracks(file_path):
    with open(file_path, "r") as f:
        return json.load(f)

def build_queue(tracks, shuffle=True):
    if shuffle:
        random.shuffle(tracks)
    return tracks

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Build a stream queue from scanned tracks.")
    parser.add_argument("--input", default="scanned_library.json", help="Scanned metadata JSON")
    parser.add_argument("--output", default="play_queue.json", help="Output queue JSON")
    parser.add_argument("--shuffle", action="store_true", help="Shuffle the queue")

    args = parser.parse_args()

    tracks = load_tracks(args.input)
    queue = build_queue(tracks, shuffle=args.shuffle)

    with open(args.output, "w") as f:
        json.dump(queue, f, indent=2)

    print(f"Created play queue with {len(queue)} tracks → {args.output}")


