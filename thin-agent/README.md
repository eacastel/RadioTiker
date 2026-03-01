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

To start immediately after onboarding:

```bash
./onboard_and_start.sh \
  --user-id eacastel \
  --library-path /path/to/Music \
  --run
```
