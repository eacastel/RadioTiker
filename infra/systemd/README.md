# Systemd: Streamer API (rt-streamer.service)

This folder tracks the production `rt-streamer.service` unit and provides a quick
runbook for rebuilding the venv and restarting the service after repo moves.

## Rebuild the venv

Run these commands **from the monorepo root** on the server:

```bash
cd /home/eacastel/RadioTiker/core/streamer_api

# (re)create the venv
python3 -m venv .venv
source .venv/bin/activate

# install deps
pip install -U pip wheel
pip install -r requirements.txt
pip install "uvicorn[standard]"

deactivate
```

## Restart the service

```bash
sudo systemctl daemon-reload
sudo systemctl restart rt-streamer.service
sudo systemctl status rt-streamer.service --no-pager
```

## Import sanity checks (optional)

These checks verify the module imports from the expected working directory:

```bash
cd /home/eacastel/RadioTiker/core
source /home/eacastel/RadioTiker/core/streamer_api/.venv/bin/activate
python -c "import streamer_api; import streamer_api.main"
```

If you see `ModuleNotFoundError: No module named 'routes'`, verify that
`streamer_api/main.py` uses package or relative imports (e.g. `from .routes...`)
so the module resolves correctly when run via `streamer_api.main:app`.
