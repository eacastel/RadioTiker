from pathlib import Path
from typing import Dict, Any, Iterable, List, Optional
import json, time, os
from decimal import Decimal
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
DB_CANONICAL_READS = str(os.getenv("RT_DB_CANONICAL_READS", "0")).strip().lower() in {"1", "true", "yes", "on"}


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
        return json.dumps(value, ensure_ascii=False, default=_json_default)
    except Exception:
        return None


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        try:
            as_int = int(value)
            if Decimal(as_int) == value:
                return as_int
        except Exception:
            pass
        try:
            return float(value)
        except Exception:
            return str(value)
    return str(value)

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


def db_load_library(user_id: str) -> Optional[Dict[str, Any]]:
    conn = _db_conn()
    if not conn:
        return None
    sql = """
    SELECT track_uid, canonical_json, UNIX_TIMESTAMP(updated_at) AS updated_ts
    FROM tracks
    WHERE user_id = %s
    ORDER BY updated_at DESC
    """
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id,))
                rows = cur.fetchall() or []
                cur.execute(
                    """
                    SELECT track_uid, source_path, file_size, mtime, duration_sec, codec,
                           bitrate_kbps, sample_rate, channels, source_rank, is_available,
                           UNIX_TIMESTAMP(last_seen_at) AS last_seen_ts
                    FROM track_sources
                    WHERE user_id = %s
                    ORDER BY track_uid ASC, is_available DESC, source_rank ASC, last_seen_at DESC, updated_at DESC
                    """,
                    (user_id,),
                )
                source_rows = cur.fetchall() or []
        preferred_sources: Dict[str, Dict[str, Any]] = {}
        for row in source_rows:
            tid = str(row.get("track_uid") or "")
            if not tid or tid in preferred_sources:
                continue
            preferred_sources[tid] = row
        tracks: Dict[str, Dict[str, Any]] = {}
        version = 0
        for row in rows:
            raw = row.get("canonical_json")
            if isinstance(raw, (bytes, bytearray)):
                try:
                    raw = raw.decode("utf-8")
                except Exception:
                    raw = None
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except Exception:
                    raw = None
            if not isinstance(raw, dict):
                continue
            tid = str(raw.get("track_id") or row.get("track_uid") or "")
            if not tid:
                continue
            raw["track_id"] = tid
            src = preferred_sources.get(tid)
            if src:
                source_path = src.get("source_path")
                if source_path:
                    raw["rel_path"] = source_path
                    raw["source_path"] = source_path
                for field in ("file_size", "mtime", "duration_sec", "codec", "bitrate_kbps", "sample_rate", "channels"):
                    if src.get(field) is not None:
                        raw[field] = src.get(field)
                raw["source_rank"] = src.get("source_rank")
                raw["source_available"] = bool(src.get("is_available"))
                raw["source_last_seen_at"] = src.get("last_seen_ts")
            tracks[tid] = raw
            try:
                version = max(version, int(row.get("updated_ts") or 0))
            except Exception:
                pass
        return {"tracks": tracks, "version": version or int(time.time()), "_cleared_for": 0}
    except Exception as e:
        print(f"[db] load library failed user={user_id}: {e}")
        return None

def load_lib(user_id: str) -> Dict[str, Any]:
    if user_id in LIBS:
        return LIBS[user_id]
    if DB_CANONICAL_READS:
        db_lib = db_load_library(user_id)
        if db_lib is not None:
            LIBS[user_id] = db_lib
            return db_lib
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
    lib_path(user_id).write_text(json.dumps(lib, indent=2, default=_json_default))


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


def db_upsert_track_sources(user_id: str, tracks: List[Dict[str, Any]]) -> bool:
    conn = _db_conn()
    if not conn or not tracks:
        return False
    sql_source = """
    INSERT INTO track_sources (
      track_uid, user_id, agent_id, source_path, file_size, mtime, checksum,
      duration_sec, codec, bitrate_kbps, sample_rate, channels, source_rank, is_available, last_seen_at
    ) VALUES (
      %(track_uid)s, %(user_id)s, %(agent_id)s, %(source_path)s, %(file_size)s, %(mtime)s, %(checksum)s,
      %(duration_sec)s, %(codec)s, %(bitrate_kbps)s, %(sample_rate)s, %(channels)s, %(source_rank)s, 1, CURRENT_TIMESTAMP
    )
    ON DUPLICATE KEY UPDATE
      track_uid=VALUES(track_uid),
      agent_id=VALUES(agent_id),
      file_size=VALUES(file_size),
      mtime=VALUES(mtime),
      checksum=VALUES(checksum),
      duration_sec=VALUES(duration_sec),
      codec=VALUES(codec),
      bitrate_kbps=VALUES(bitrate_kbps),
      sample_rate=VALUES(sample_rate),
      channels=VALUES(channels),
      source_rank=VALUES(source_rank),
      is_available=1,
      last_seen_at=CURRENT_TIMESTAMP,
      updated_at=CURRENT_TIMESTAMP
    """
    sql_select_id = "SELECT id FROM track_sources WHERE user_id=%s AND source_path=%s LIMIT 1"
    sql_tags = """
    INSERT INTO source_tags (
      track_source_id, title, artist, album, track_no, disc_no, year, genre,
      album_artist, composer, bpm, musical_key, raw_json
    ) VALUES (
      %(track_source_id)s, %(title)s, %(artist)s, %(album)s, %(track_no)s, %(disc_no)s, %(year)s, %(genre)s,
      %(album_artist)s, %(composer)s, %(bpm)s, %(musical_key)s, %(raw_json)s
    )
    ON DUPLICATE KEY UPDATE
      title=VALUES(title),
      artist=VALUES(artist),
      album=VALUES(album),
      track_no=VALUES(track_no),
      disc_no=VALUES(disc_no),
      year=VALUES(year),
      genre=VALUES(genre),
      album_artist=VALUES(album_artist),
      composer=VALUES(composer),
      bpm=VALUES(bpm),
      musical_key=VALUES(musical_key),
      raw_json=VALUES(raw_json),
      updated_at=CURRENT_TIMESTAMP
    """
    try:
        with conn:
            with conn.cursor() as cur:
                for t in tracks:
                    source_path = t.get("rel_path") or t.get("path")
                    if not source_path:
                        continue
                    source_row = {
                        "track_uid": str(t.get("track_id") or ""),
                        "user_id": user_id,
                        "agent_id": user_id,
                        "source_path": source_path,
                        "file_size": t.get("file_size"),
                        "mtime": t.get("mtime"),
                        "checksum": None,
                        "duration_sec": t.get("duration_sec"),
                        "codec": t.get("codec"),
                        "bitrate_kbps": t.get("bitrate_kbps"),
                        "sample_rate": t.get("sample_rate"),
                        "channels": t.get("channels"),
                        "source_rank": 100,
                    }
                    cur.execute(sql_source, source_row)
                    cur.execute(sql_select_id, (user_id, source_path))
                    found = cur.fetchone() or {}
                    source_id = found.get("id")
                    if not source_id:
                        continue
                    tag_row = {
                        "track_source_id": int(source_id),
                        "title": t.get("_scan_title", t.get("title")),
                        "artist": t.get("_scan_artist", t.get("artist")),
                        "album": t.get("_scan_album", t.get("album")),
                        "track_no": str(t.get("track_no")) if t.get("track_no") is not None else None,
                        "disc_no": str(t.get("disc_no")) if t.get("disc_no") is not None else None,
                        "year": str(t.get("_scan_year", t.get("year"))) if t.get("_scan_year", t.get("year")) is not None else None,
                        "genre": t.get("_scan_genre", t.get("genre")),
                        "album_artist": t.get("album_artist"),
                        "composer": t.get("composer"),
                        "bpm": t.get("bpm"),
                        "musical_key": t.get("musical_key"),
                        "raw_json": _json_or_none(t),
                    }
                    cur.execute(sql_tags, tag_row)
        return True
    except Exception as e:
        print(f"[db] upsert track sources failed user={user_id}: {e}")
        return False


def db_mark_all_track_sources_unavailable(user_id: str) -> bool:
    conn = _db_conn()
    if not conn:
        return False
    sql = """
    UPDATE track_sources
    SET is_available = 0,
        updated_at = CURRENT_TIMESTAMP
    WHERE user_id = %s
    """
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id,))
        return True
    except Exception as e:
        print(f"[db] mark all track sources unavailable failed user={user_id}: {e}")
        return False


def db_upsert_track_health(user_id: str, entries: List[Dict[str, Any]]) -> bool:
    conn = _db_conn()
    if not conn or not entries:
        return False
    sql = """
    INSERT INTO track_health (
      track_uid, user_id, source_path, status, source_reachable, probe_ok,
      decode_ok, duration_sec, codec, error_reason, details_json, checked_at
    ) VALUES (
      %(track_uid)s, %(user_id)s, %(source_path)s, %(status)s, %(source_reachable)s, %(probe_ok)s,
      %(decode_ok)s, %(duration_sec)s, %(codec)s, %(error_reason)s, %(details_json)s, CURRENT_TIMESTAMP
    )
    ON DUPLICATE KEY UPDATE
      status=VALUES(status),
      source_reachable=VALUES(source_reachable),
      probe_ok=VALUES(probe_ok),
      decode_ok=VALUES(decode_ok),
      duration_sec=VALUES(duration_sec),
      codec=VALUES(codec),
      error_reason=VALUES(error_reason),
      details_json=VALUES(details_json),
      checked_at=CURRENT_TIMESTAMP,
      updated_at=CURRENT_TIMESTAMP
    """
    rows = []
    for entry in entries:
        track_uid = str(entry.get("track_uid") or "")
        if not track_uid:
            continue
        rows.append(
            {
                "track_uid": track_uid,
                "user_id": user_id,
                "source_path": entry.get("source_path"),
                "status": str(entry.get("status") or "warning")[:32],
                "source_reachable": 1 if entry.get("source_reachable") else 0,
                "probe_ok": 1 if entry.get("probe_ok") else 0,
                "decode_ok": 1 if entry.get("decode_ok") else 0,
                "duration_sec": entry.get("duration_sec"),
                "codec": entry.get("codec"),
                "error_reason": str(entry.get("error_reason") or "")[:2048] or None,
                "details_json": _json_or_none(entry.get("details") or {}),
            }
        )
    if not rows:
        return False
    try:
        with conn:
            with conn.cursor() as cur:
                cur.executemany(sql, rows)
        return True
    except Exception as e:
        print(f"[db] upsert track health failed user={user_id}: {e}")
        return False


def db_list_track_health(user_id: str, status: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    conn = _db_conn()
    if not conn:
        return []
    limit = max(1, min(int(limit or 100), 500))
    sql = """
    SELECT track_uid, source_path, status, source_reachable, probe_ok, decode_ok,
           duration_sec, codec, error_reason, details_json,
           UNIX_TIMESTAMP(checked_at) AS checked_at
    FROM track_health
    WHERE user_id = %s
    """
    params: List[Any] = [user_id]
    if status:
        sql += " AND status = %s"
        params.append(status)
    sql += " ORDER BY checked_at DESC LIMIT %s"
    params.append(limit)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                return cur.fetchall() or []
    except Exception as e:
        print(f"[db] list track health failed user={user_id}: {e}")
        return []


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
    """Persist stable agent/tunnel fields while excluding volatile heartbeat state."""
    import json as _json
    stable = {"base_url": st.get("base_url")}
    for key in (
        "agent_id",
        "public_key",
        "ssh_host",
        "ssh_user",
        "remote_port",
        "local_port",
        "last_scan",
    ):
        if st.get(key) is not None:
            stable[key] = st.get(key)
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
