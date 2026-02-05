# RadioTiker — Operations & Build Instructions

This doc is the “how to run it” reference for the RadioTiker monorepo.

## Monorepo Paths (important)

- Core server code: `~/RadioTiker/core`
- Streamer API app: `~/RadioTiker/core/streamer_api`
- Thin agent code: `~/RadioTiker/thin-agent`

Legacy directories from the old split repos may still exist (`~/radio-tiker-core`, `~/streamer-thin-agent`) but production should use the monorepo paths above.

---

## Streamer API (FastAPI/Uvicorn) — systemd

### Check service status
    sudo systemctl status rt-streamer.service --no-pager

### Restart
    sudo systemctl restart rt-streamer.service

### Logs
    sudo journalctl -u rt-streamer.service -n 120 --no-pager

### systemd unit (production)
Unit file lives at:
- `/etc/systemd/system/rt-streamer.service`

Expected key settings:
- `WorkingDirectory=/home/eacastel/RadioTiker/core/streamer_api`
- `ExecStart=/home/eacastel/RadioTiker/core/streamer_api/.venv/bin/uvicorn streamer_api.main:app --host 127.0.0.1 --port 8090 --proxy-headers --root-path /streamer`

### Create/repair the venv for the service
    cd ~/RadioTiker/core/streamer_api
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -U pip wheel
    pip install -r requirements.txt
    pip install "uvicorn[standard]"
    deactivate

Then:
    sudo systemctl daemon-reload
    sudo systemctl restart rt-streamer.service

---

## Nginx (proxy + downloads)

Nginx site file (production):
- `/etc/nginx/sites-available/streamer-agent`

Notes:
- Proxies `/streamer/api/*` → `http://127.0.0.1:8090/api/*`
- Health can be answered directly by Nginx at `/streamer/api/health`
- Downloads served from `/var/www/radio.tiker.es/html/downloads`

Validate config:
    sudo nginx -t

Reload (only if you changed config):
    sudo systemctl reload nginx

### Track Nginx config in git
    cd ~/RadioTiker
    mkdir -p infra/nginx
    sudo cp /etc/nginx/sites-available/streamer-agent infra/nginx/streamer-agent
    sudo chown eacastel:eacastel infra/nginx/streamer-agent
    git add infra/nginx/streamer-agent
    git commit -m "Track nginx streamer-agent config"
    git push

### Track systemd config in git
    cd ~/RadioTiker
    mkdir -p infra/systemd
    sudo cp /etc/systemd/system/rt-streamer.service infra/systemd/rt-streamer.service
    sudo chown eacastel:eacastel infra/systemd/rt-streamer.service
    git add infra/systemd/rt-streamer.service
    git commit -m "Track rt-streamer systemd unit"
    git push

---

## Thin Agent Builds (PyInstaller)

All thin-agent work is now under:
- `~/RadioTiker/thin-agent`

### 0) One-time prep on Hetzner (venv + pyinstaller)

    cd ~/RadioTiker/thin-agent

    python3 -m venv .venv
    source .venv/bin/activate

    pip install --upgrade pip
    pip install -r requirements.txt pyinstaller

If `.venv` already exists:

    cd ~/RadioTiker/thin-agent
    source .venv/bin/activate

---

### 1) Rebuild Linux 64-bit agent (desktop)

Build a CLI agent based on `thin_agent.py`.

    cd ~/RadioTiker/thin-agent
    source .venv/bin/activate

    rm -rf build dist *.spec

    pyinstaller thin_agent.py \
      --onefile \
      --name radiotiker-thin-agent-v0.4-linux

Output:
    ls dist
    # radiotiker-thin-agent-v0.4-linux

Optional tarball:
    cd dist
    tar -czvf radiotiker-thin-agent-v0.4-linux.tar.gz radiotiker-thin-agent-v0.4-linux
    cd ..

Optional GUI build:
    pyinstaller thin_agent_gui.py \
      --onefile \
      --name radiotiker-thin-agent-gui-v0.4-linux

---

### 2) Rebuild ARM Pi 32-bit agent (ARMv7)

Build from Hetzner using Docker with ARMv7 emulation.

#### 2.1 Ensure Docker is ready (once)
    sudo apt update
    sudo apt install -y docker.io qemu-user-static
    sudo systemctl enable --now docker

#### 2.2 Run an ARMv7 Python container
From the thin-agent dir:

    cd ~/RadioTiker/thin-agent

    sudo docker run --rm -it \
      --platform linux/arm/v7 \
      -v "$PWD":/src \
      python:3.11-slim bash

Inside container:

    cd /src
    pip install --upgrade pip
    pip install -r requirements.txt pyinstaller

    pyinstaller thin_agent.py \
      --onefile \
      --name radiotiker-thin-agent-v0.4-arm32

    ls dist
    # radiotiker-thin-agent-v0.4-arm32
    exit

Back on Hetzner host:

    cd ~/RadioTiker/thin-agent/dist
    tar -czvf radiotiker-thin-agent-v0.4-arm32.tar.gz radiotiker-thin-agent-v0.4-arm32

---

### 3) Publish both binaries to radio.tiker.es/downloads

From Hetzner:

    cd ~/RadioTiker/thin-agent/dist

    sudo mkdir -p /var/www/radio.tiker.es/html/downloads

    sudo cp radiotiker-thin-agent-v0.4-linux \
            radiotiker-thin-agent-v0.4-linux.tar.gz \
            radiotiker-thin-agent-v0.4-arm32 \
            radiotiker-thin-agent-v0.4-arm32.tar.gz \
            /var/www/radio.tiker.es/html/downloads/

    sudo chown eacastel:www-data /var/www/radio.tiker.es/html/downloads/radiotiker-thin-agent-v0.4-*
    sudo chmod 644 /var/www/radio.tiker.es/html/downloads/radiotiker-thin-agent-v0.4-*

Optional: update "latest" aliases
    cd /var/www/radio.tiker.es/html/downloads

    sudo ln -sf radiotiker-thin-agent-v0.4-linux  radiotiker-thin-agent-latest-linux
    sudo ln -sf radiotiker-thin-agent-v0.4-arm32  radiotiker-thin-agent-latest-arm32

Download URLs:
- https://radio.tiker.es/downloads/radiotiker-thin-agent-v0.4-linux
- https://radio.tiker.es/downloads/radiotiker-thin-agent-v0.4-arm32
- https://radio.tiker.es/downloads/radiotiker-thin-agent-latest-linux
- https://radio.tiker.es/downloads/radiotiker-thin-agent-latest-arm32

---

## 4) Run on each machine

### 4.1 Desktop (aurora, Linux x86_64)

    cd ~
    curl -O https://radio.tiker.es/downloads/radiotiker-thin-agent-latest-linux
    chmod +x radiotiker-thin-agent-latest-linux

Optional env file:
    cat > .radiotiker-env <<'EOF'
    SERVER_URL=https://radio.tiker.es/streamer/api/submit-scan
    USER_ID=eacastel
    LIBRARY_PATH=/home/eacastel/NAS/Music
    AGENT_PORT=8765
    VALID_AUDIO_EXTENSIONS=.mp3,.flac,.wav,.m4a
    EOF

    export $(grep -v '^#' .radiotiker-env | xargs)

    ./radiotiker-thin-agent-latest-linux

### 4.2 Raspberry Pi (ARM, 32-bit OS)

    cd ~
    wget https://radio.tiker.es/downloads/radiotiker-thin-agent-latest-arm32
    chmod +x radiotiker-thin-agent-latest-arm32

    cat > .radiotiker-env <<'EOF'
    SERVER_URL=https://radio.tiker.es/streamer/api/submit-scan
    USER_ID=eacastel
    LIBRARY_PATH=/mnt/nas-music
    AGENT_PORT=8765
    VALID_AUDIO_EXTENSIONS=.mp3,.flac,.wav,.m4a
    EOF

    export $(grep -v '^#' .radiotiker-env | xargs)

    ./radiotiker-thin-agent-latest-arm32

On first run, the agent will:
- start the local file server
- do a full scan
- send everything with `replace=true`
- store state in `~/.radiotiker_agent_state.json`

On subsequent runs, it will:
- only send new/changed tracks
- use the same `library_version` and `replace=false`

---

## 5) Optional: clean reset of the library

If you want to wipe the current server library and let the agent do a clean full upload:

    curl -sS -X POST https://radio.tiker.es/streamer/api/submit-scan \
      -H 'Content-Type: application/json' \
      -d '{"user_id":"eacastel","library":[],"library_version":'"$(date +%s)"',"replace":true}'

Then run the agent once from the machine you want as the canonical library.

---

## 6) Optional: run the thin agent as a background service (Pi)

Recommended next step: wrap the Pi binary with a small systemd unit so it runs permanently.
