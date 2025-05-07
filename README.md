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
