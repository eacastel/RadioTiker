# RadioTiker (Monorepo)

RadioTiker is a personal “radio-style” music streaming stack.

This monorepo consolidates:
- **core/**: Server-side streamer API (FastAPI/Uvicorn), library storage, relay/transcode endpoints
- **thin-agent/**: Thin agent that scans local music folders and serves files to the streamer via a local file server; also supports packaging binaries (PyInstaller)

## Repository Layout

- `core/`
  - `streamer_api/` — FastAPI app and API routes
  - `streamer-agent/` — supporting agent/app pieces (legacy naming preserved)
  - `scripts/`, `config/`, `data/` — operational assets
- `thin-agent/`
  - `thin_agent.py` — CLI thin agent (incremental scan logic)
  - `thin_agent_gui.py` — optional GUI build
  - `local_file_server.py`, `shared_config.py`, build artifacts, specs

## Production URLs (Hetzner)

- Streamer API (proxied via Nginx):
  - `https://radio.tiker.es/streamer/api/health`
  - `https://radio.tiker.es/streamer/api/...`

- Downloads:
  - `https://radio.tiker.es/downloads/`

## Service (systemd)

On Hetzner, the Streamer API runs as:

- Unit: `/etc/systemd/system/rt-streamer.service`
- Uvicorn: `127.0.0.1:8090`
- Nginx proxies `/streamer/api/*` → `http://127.0.0.1:8090/api/*`

This repo stores tracked copies of infra configs under:
- `infra/systemd/`
- `infra/nginx/`

## Quick Start (Server)

1) Create venv + install deps:
- `cd core/streamer_api`
- `python3 -m venv .venv`
- `source .venv/bin/activate`
- `pip install -U pip wheel`
- `pip install -r requirements.txt`
- `pip install "uvicorn[standard]"`

2) Start service:
- `sudo systemctl daemon-reload`
- `sudo systemctl restart rt-streamer.service`
- `sudo systemctl status rt-streamer.service --no-pager`

3) Logs:
- `sudo journalctl -u rt-streamer.service -n 120 --no-pager`

## Thin Agent

See `INSTRUCTIONS.md` for:
- building Linux x86_64 + ARMv7 binaries with PyInstaller
- publishing binaries to `/var/www/radio.tiker.es/html/downloads`
- running on desktop and Raspberry Pi
- optional library reset
