from pathlib import Path
from typing import Dict, Any, Iterable, List, Optional
import json, time, os
from urllib.parse import urlparse, unquote

try:
    import pymysql  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    pymysql = None

# radio-tiker-core/  (two levels up from this file)
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "user-libraries"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# In-memory state caches
LIBS: Dict[str, Dict[str, Any]] = {}
AGENTS: Dict[str, Dict[str, Any]] = {}

DB_DSN = os.getenv("RADIO_DB_DSN") or os.getenv("DATABASE_URL") or ""


def _db_cfg() -> Optional[Dict[str, Any]]:
    if not DB_DSN:
        return None
    # EnvironmentFile values may preserve escaped percent signs as "%%".
    # Normalize here so URL-encoded credentials still parse correctly.
    dsn = DB_DSN.replace("%%", "%")
    if not dsn.startswith("mysql"):
        return None
    u = urlparse(dsn)
    if not u.hostname or not u.username or not u.path:
        return None
    user = unquote(u.username)
    password = unquote(u.password or "")
    return {
        "host": u.hostname,
        "port": int(u.port or 3306),
        "user": user,
        "password": password,
        "database": u.path.lstrip("/"),
    }


def _db_conn():
    cfg = _db_cfg()
    if not cfg or pymysql is None:
        return None
    try:
        return pymysql.connect(
            host=cfg["host"],
            port=cfg["port"],
            user=cfg["user"],
            password=cfg["password"],
            database=cfg["database"],
            autocommit=True,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
        )
    except Exception as e:
        print(f"[db] connect failed: {e}")
        return None


def _json_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return None

def _safe_name(s: str) -> str:
    return "".join(ch for ch in s if ch.isalnum() or ch in ("-", "_", "."))

def lib_path(user_id: str) -> Path:
    return DATA_DIR / f"{_safe_name(user_id)}.json"

def agent_path(user_id: str) -> Path:
    return DATA_DIR / f"{_safe_name(user_id)}.agent.json"

def playlists_path(user_id: str) -> Path:
    return DATA_DIR / f"{_safe_name(user_id)}.playlists.json"

def metadata_library_path(user_id: str) -> Path:
    return DATA_DIR / f"{_safe_name(user_id)}.metadata-library.json"

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


def db_upsert_tracks(user_id: str, tracks: List[Dict[str, Any]]) -> bool:
    conn = _db_conn()
    if not conn:
        return False
    sql = """
    INSERT INTO tracks (
      track_uid, user_id, source_path, source_hash, title, artist, album, year, genre,
      artwork_url, artist_image_urls, artist_bio, album_bio, canonical_json, override_json
    ) VALUES (
      %(track_uid)s, %(user_id)s, %(source_path)s, %(source_hash)s, %(title)s, %(artist)s, %(album)s, %(year)s, %(genre)s,
      %(artwork_url)s, %(artist_image_urls)s, %(artist_bio)s, %(album_bio)s, %(canonical_json)s, %(override_json)s
    )
    ON DUPLICATE KEY UPDATE
      source_path=VALUES(source_path),
      source_hash=VALUES(source_hash),
      title=VALUES(title),
      artist=VALUES(artist),
      album=VALUES(album),
      year=VALUES(year),
      genre=VALUES(genre),
      artwork_url=VALUES(artwork_url),
      artist_image_urls=VALUES(artist_image_urls),
      artist_bio=VALUES(artist_bio),
      album_bio=VALUES(album_bio),
      canonical_json=VALUES(canonical_json),
      override_json=VALUES(override_json),
      updated_at=CURRENT_TIMESTAMP
    """
    rows = []
    for t in tracks:
        rows.append(
            {
                "track_uid": str(t.get("track_id") or ""),
                "user_id": user_id,
                "source_path": t.get("rel_path") or t.get("path"),
                "source_hash": None,
                "title": t.get("title"),
                "artist": t.get("artist"),
                "album": t.get("album"),
                "year": t.get("year"),
                "genre": t.get("genre"),
                "artwork_url": t.get("artwork_url"),
                "artist_image_urls": _json_or_none(t.get("artist_image_urls") or []),
                "artist_bio": t.get("artist_bio"),
                "album_bio": t.get("album_bio"),
                "canonical_json": _json_or_none(t),
                "override_json": None,
            }
        )
    try:
        with conn:
            with conn.cursor() as cur:
                cur.executemany(sql, rows)
        return True
    except Exception as e:
        print(f"[db] upsert tracks failed user={user_id}: {e}")
        return False


def db_insert_provider_snapshot(track_uid: str, best: Dict[str, Any]) -> bool:
    conn = _db_conn()
    if not conn:
        return False
    sql = """
    INSERT INTO provider_snapshots (track_uid, provider, provider_ref, score, payload_json)
    VALUES (%s, %s, %s, %s, %s)
    """
    provider = str(best.get("provider") or "unknown")
    ref = None
    reference = best.get("reference") or {}
    if isinstance(reference, dict):
        ref = reference.get("recording_id") or reference.get("release_id") or reference.get("id")
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        str(track_uid),
                        provider,
                        str(ref) if ref else None,
                        float(best.get("score") or 0.0),
                        _json_or_none(best) or "{}",
                    ),
                )
        return True
    except Exception as e:
        print(f"[db] insert provider snapshot failed track={track_uid}: {e}")
        return False


def db_upsert_override(track_uid: str, user_id: str, patch: Dict[str, Any]) -> bool:
    conn = _db_conn()
    if not conn:
        return False
    sql = """
    INSERT INTO metadata_overrides (track_uid, user_id, override_json)
    VALUES (%s, %s, %s)
    ON DUPLICATE KEY UPDATE
      override_json=VALUES(override_json),
      updated_at=CURRENT_TIMESTAMP
    """
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, (str(track_uid), user_id, _json_or_none(patch) or "{}"))
        return True
    except Exception as e:
        print(f"[db] upsert override failed track={track_uid} user={user_id}: {e}")
        return False


def db_delete_overrides(user_id: str, track_ids: List[str]) -> bool:
    conn = _db_conn()
    if not conn or not track_ids:
        return False
    placeholders = ",".join(["%s"] * len(track_ids))
    sql = f"DELETE FROM metadata_overrides WHERE user_id = %s AND track_uid IN ({placeholders})"
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple([user_id] + [str(tid) for tid in track_ids]))
        return True
    except Exception as e:
        print(f"[db] delete overrides failed user={user_id}: {e}")
        return False


def db_delete_provider_snapshots(track_ids: List[str]) -> bool:
    conn = _db_conn()
    if not conn or not track_ids:
        return False
    placeholders = ",".join(["%s"] * len(track_ids))
    sql = f"DELETE FROM provider_snapshots WHERE track_uid IN ({placeholders})"
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(str(tid) for tid in track_ids))
        return True
    except Exception as e:
        print(f"[db] delete provider snapshots failed: {e}")
        return False


def db_delete_tracks(user_id: str, track_ids: List[str], purge_related: bool = True) -> bool:
    conn = _db_conn()
    if not conn or not track_ids:
        return False
    placeholders = ",".join(["%s"] * len(track_ids))
    sql = f"DELETE FROM tracks WHERE user_id = %s AND track_uid IN ({placeholders})"
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple([user_id] + [str(tid) for tid in track_ids]))
        if purge_related:
            db_delete_overrides(user_id, track_ids)
            db_delete_provider_snapshots(track_ids)
        return True
    except Exception as e:
        print(f"[db] delete tracks failed user={user_id}: {e}")
        return False


def db_find_metadata_seed(user_id: str, track: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Find best same-user historical metadata row for this track by normalized keys.
    Prefers title+artist+album, falls back to title+artist.
    """
    conn = _db_conn()
    if not conn:
        return None

    title_norm = str(track.get("title_norm") or "").strip()
    artist_norm = str(track.get("artist_norm") or "").strip()
    album_norm = str(track.get("album_norm") or "").strip()
    track_uid = str(track.get("track_id") or "")
    if not title_norm or not artist_norm:
        return None

    where_base = "user_id = %s AND track_uid <> %s"
    expr_title = "JSON_UNQUOTE(JSON_EXTRACT(canonical_json, '$.title_norm'))"
    expr_artist = "JSON_UNQUOTE(JSON_EXTRACT(canonical_json, '$.artist_norm'))"
    expr_album = "JSON_UNQUOTE(JSON_EXTRACT(canonical_json, '$.album_norm'))"

    sql_primary = f"""
    SELECT
      title, artist, album, year, genre, artwork_url, artist_image_urls, artist_bio, album_bio, canonical_json
    FROM tracks
    WHERE {where_base}
      AND {expr_title} = %s
      AND {expr_artist} = %s
      AND {expr_album} = %s
    ORDER BY updated_at DESC
    LIMIT 1
    """
    sql_fallback = f"""
    SELECT
      title, artist, album, year, genre, artwork_url, artist_image_urls, artist_bio, album_bio, canonical_json
    FROM tracks
    WHERE {where_base}
      AND {expr_title} = %s
      AND {expr_artist} = %s
    ORDER BY updated_at DESC
    LIMIT 1
    """

    def _normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(row or {})
        # Parse JSON fields when driver returns strings.
        for k in ("artist_image_urls", "canonical_json"):
            v = out.get(k)
            if isinstance(v, (bytes, bytearray)):
                try:
                    v = v.decode("utf-8")
                except Exception:
                    v = None
            if isinstance(v, str):
                try:
                    out[k] = json.loads(v)
                except Exception:
                    out[k] = None
        return out

    try:
        with conn:
            with conn.cursor() as cur:
                if album_norm:
                    cur.execute(sql_primary, (user_id, track_uid, title_norm, artist_norm, album_norm))
                    row = cur.fetchone()
                    if row:
                        return _normalize_row(row)
                cur.execute(sql_fallback, (user_id, track_uid, title_norm, artist_norm))
                row = cur.fetchone()
                return _normalize_row(row) if row else None
    except Exception as e:
        print(f"[db] metadata seed lookup failed user={user_id}: {e}")
        return None

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

def save_agent_record(user_id: str, st: Dict[str, Any]):
    """
    Persist agent record with tunnel details (vnext).
    """
    import json as _json
    stable = {
        "base_url": st.get("base_url"),
        "agent_id": st.get("agent_id"),
        "public_key": st.get("public_key"),
        "ssh_host": st.get("ssh_host"),
        "ssh_user": st.get("ssh_user"),
        "remote_port": st.get("remote_port"),
        "local_port": st.get("local_port"),
        "last_scan": st.get("last_scan"),
    }
    AGENTS[user_id] = {**st}
    agent_path(user_id).write_text(_json.dumps(stable, indent=2))

def load_playlists(user_id: str) -> Dict[str, Any]:
    p = playlists_path(user_id)
    if p.exists():
        try:
            obj = json.loads(p.read_text())
            if isinstance(obj, dict) and "playlists" in obj:
                return obj
        except Exception:
            pass
    return {"playlists": [], "version": int(time.time())}

def save_playlists(user_id: str, payload: Dict[str, Any]):
    playlists_path(user_id).write_text(json.dumps(payload, indent=2))

def load_metadata_library(user_id: str) -> Dict[str, Any]:
    p = metadata_library_path(user_id)
    if p.exists():
        try:
            obj = json.loads(p.read_text())
            if isinstance(obj, dict) and "entries" in obj:
                return obj
        except Exception:
            pass
    return {"entries": [], "version": int(time.time())}

def save_metadata_library(user_id: str, payload: Dict[str, Any]):
    metadata_library_path(user_id).write_text(json.dumps(payload, indent=2))

def list_assigned_ports() -> Iterable[int]:
    """
    Read all saved agent records and return any assigned remote ports.
    """
    ports = []
    for p in DATA_DIR.glob("*.agent.json"):
        try:
            obj = json.loads(p.read_text())
            rp = obj.get("remote_port")
            if isinstance(rp, int):
                ports.append(rp)
        except Exception:
            continue
    for st in AGENTS.values():
        rp = st.get("remote_port")
        if isinstance(rp, int):
            ports.append(rp)
    return ports
