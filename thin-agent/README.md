# RadioTiker Thin Agent

This is a lightweight music library scanner for RadioTiker.  
It scans your local folders and sends metadata to the central server.

## 🧰 Requirements

- Python 3.8+
- Your music folder must be locally accessible

## 🛠️ Setup

```bash
git clone https://github.com/YOURUSERNAME/streamer-thin-agent.git
cd streamer-thin-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Reverse Tunnel (Phase 1)

If you want the agent to "just work" without port forwarding:

1. Set `TUNNEL_ENABLE=1` in `.env`.
2. Provide SSH settings (`SSH_HOST`, `SSH_USER`, `SSH_KEY_PATH`, `REMOTE_PORT`).
3. Set `PUBLIC_BASE_URL` to `http://127.0.0.1:<REMOTE_PORT>`.

The agent will keep the SSH tunnel alive in the background.

## One-Command Onboarding (vNext)

From this folder:

```bash
./onboard_and_start.sh \
  --user-id eacastel \
  --library-path /path/to/Music
```

This will:
1. Generate a persistent SSH key in `~/.radiotiker/agent_ed25519` (if missing).
2. Call `link/start`, `link/complete`, and `register-key`.
3. Write tunnel + API config into `.env`.
4. Persist scan resume checkpoints in `~/.radiotiker/scan_resume_<user_id>.json` so restarts resume from last committed uploaded batch.

To start immediately after onboarding:

```bash
./onboard_and_start.sh \
  --user-id eacastel \
  --library-path /path/to/Music \
  --run
```

To start the GUI immediately after onboarding:

```bash
./onboard_and_start.sh \
  --user-id eacastel \
  --library-path /path/to/Music \
  --gui
```

In the GUI agent, use `Open Library` to launch the browser player at:
`https://next.radio.tiker.es/streamer/api/user/<user>/play`

By default the script also installs a Linux user auto-start service:
By default the script runs in temporary mode (no persistent service).

To enable Linux user auto-start service explicitly:

```bash
./onboard_and_start.sh --user-id eacastel --library-path /path/to/Music --autostart
```

Auto-start files:
1. `~/.config/systemd/user/radiotiker-vnext-agent.service`
2. `~/.config/radiotiker-vnext/agent.env`

Resume controls:
1. `RT_SCAN_RESUME_ENABLED=1` (default) enables checkpointed scan resume.
2. `RT_SCAN_RESUME_RESET=1` forces a fresh full scan and resets checkpoint.

## Publish vNext Thin Distribution (binary + onboard script)

On Hetzner (from this folder):

```bash
./build_and_publish_vnext_thin.sh v0.5
```

This publishes:
1. `radiotiker-thin-agent-vnext-latest-linux` (binary)
2. `radiotiker-vnext-onboard-latest.sh` (helper script)
