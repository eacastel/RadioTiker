# streamer.py
import json
import subprocess
import time

def stream_track_ffmpeg(track, stream_url):
    print(f"Streaming: {track['title']} - {track['artist']}")
    command = [
        "ffmpeg",
        "-re",
        "-i", track["filepath"],
        "-vn",
        "-c:a", "libmp3lame",
        "-b:a", "128k",
        "-f", "mp3",
        stream_url
    ]
    subprocess.run(command)

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Stream a queue of tracks to Icecast.")
    parser.add_argument("--queue", default="play_queue.json", help="Track queue JSON file")
    parser.add_argument("--stream-url", required=True, help="Icecast stream URL")

    args = parser.parse_args()

    with open(args.queue, "r") as f:
        queue = json.load(f)

    for track in queue:
        stream_track_ffmpeg(track, args.stream_url)
        time.sleep(1)  # Optional pause between tracks


