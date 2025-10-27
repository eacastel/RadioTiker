from pathlib import Path
from typing import Dict, Any
import json, time

# radio-tiker-core/  (two levels up from this file)
ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data" / "user-libraries"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# In-memory state caches
LIBS: Dict[str, Dict[str, Any]] = {}
AGENTS: Dict[str, Dict[str, Any]] = {}

def _safe_name(s: str) -> str:
    return "".join(ch for ch in s if ch.isalnum() or ch in ("-", "_", "."))

def lib_path(user_id: str) -> Path:
    return DATA_DIR / f"{_safe_name(user_id)}.json"

def agent_path(user_id: str) -> Path:
    return DATA_DIR / f"{_safe_name(user_id)}.agent.json"

def load_lib(user_id: str) -> Dict[str, Any]:
    if user_id in LIBS:
        return LIBS[user_id]
    p = lib_path(user_id)
    if p.exists():
        try:
            obj = json.loads(p.read_text())
            if isinstance(obj, dict) and "tracks" in obj:
                LIBS[user_id] = obj
                return obj
        except Exception:
            pass
    LIBS[user_id] = {"tracks": {}, "version": int(time.time()), "_cleared_for": 0}
    return LIBS[user_id]

def save_lib(user_id: str, lib: Dict[str, Any]):
    LIBS[user_id] = lib
    lib_path(user_id).write_text(json.dumps(lib, indent=2))

def load_agent(user_id: str) -> Dict[str, Any]:
    if user_id in AGENTS:
        return AGENTS[user_id]
    p = agent_path(user_id)
    st: Dict[str, Any] = {}
    if p.exists():
        try:
            st = json.loads(p.read_text())
        except Exception:
            st = {}
    st.setdefault("last_seen", 0)  # volatile
    AGENTS[user_id] = st
    return st

def save_agent_stable(user_id: str, st: Dict[str, Any]):
    """Persist only stable fields (like base_url). Do not persist last_seen."""
    import json as _json
    stable = {"base_url": st.get("base_url")}
    AGENTS[user_id] = {**st}
    agent_path(user_id).write_text(_json.dumps(stable, indent=2))
