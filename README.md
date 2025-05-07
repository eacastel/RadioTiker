# RadioTiker Core Metadata System

This is the backend metadata enrichment engine for RadioTiker. It scans music libraries, extracts metadata, enriches it using the [MusicBrainz](https://musicbrainz.org) API, and prepares data for personalized streaming.

---

##  Features

- Scan local/NAS music folders (`mp3_checker.py`)
- Enrich metadata via MusicBrainz (`fetch_metadata.py`)
- Configurable via `.env` file
- Ready for multi-user track libraries
- Designed to expand into FastAPI, Icecast, and DJ tools

---

## Project Structure

radio-tiker-core/
├── scripts/
│ ├── mp3_checker.py # Walk and list valid audio files
│ └── fetch_metadata.py # Query MusicBrainz for metadata
├── data/ # Will contain user track metadata
├── config/ # Reserved for future settings
├── .env.example # Sample environment configuration
├── requirements.txt # Python dependencies
└── README.md

---

## Setup Instructions

### 1. Clone the repo
git clone https://github.com/yourname/radio-tiker-core.git
cd radio-tiker-core

### 2. Set up Python environment

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

## Usage Examples

# Scan your music folder

python scripts/mp3_checker.py

# Enrich a single track's metadata

python scripts/fetch_metadata.py --title "Imagine" --artist "John Lennon"

# Coming Soon
Batch metadata import and storage
FastAPI-based metadata API
MongoDB integration
Stream-to-Icecast with metadata overlays
Track mood/BPM/key via Essentia or AcoustID
Web dashboard for DJs
