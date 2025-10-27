from typing import Dict, Any, Optional
from urllib.parse import quote, unquote
from .storage import load_agent

def normalize_rel_path(rel: str) -> str:
    """Normalize rel_path: ensure exactly one level of URL-encoding per segment."""
    rel = rel.lstrip("/")
    segs = [quote(unquote(s), safe="@!$&'()*+,;=:_-.") for s in rel.split("/")]
    return "/".join(segs)

def build_stream_url(user_id: str, t: Dict[str, Any]) -> Optional[str]:
    """Rebuild file URL using agent's base_url + stored rel_path (no re-encode here)."""
    st = load_agent(user_id)
    base = st.get("base_url")
    rel  = t.get("rel_path")
    if not base or not rel:
        return None
    return f"{base.rstrip('/')}/{rel.lstrip('/')}"
