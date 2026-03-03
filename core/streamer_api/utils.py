from typing import Dict, Any, Optional
import re
import unicodedata
from urllib.parse import quote, unquote
from .storage import load_agent

def normalize_rel_path(rel: str) -> str:
    """Normalize rel_path: ensure exactly one level of URL-encoding per segment."""
    rel = rel.lstrip("/")
    segs = [quote(unquote(s), safe="@!$&'()*+,;=:_-.") for s in rel.split("/")]
    return "/".join(segs)


def normalize_text(value: Any) -> str:
    """
    Text normalization for metadata fields while preserving display text semantics.
    """
    txt = unicodedata.normalize("NFKC", str(value or "")).strip()
    return re.sub(r"\s+", " ", txt)


def normalize_text_key(value: Any) -> str:
    """
    Search/group key normalization (case-insensitive, accent/punctuation tolerant).
    """
    txt = normalize_text(value).lower()
    txt = "".join(ch for ch in unicodedata.normalize("NFKD", txt) if not unicodedata.combining(ch))
    txt = re.sub(r"[^a-z0-9]+", " ", txt)
    return re.sub(r"\s+", " ", txt).strip()


def _codec_from_track(track: Dict[str, Any]) -> Optional[str]:
    raw_codec = normalize_text(track.get("codec"))
    if raw_codec:
        return raw_codec.lower()
    rel = str(track.get("rel_path") or track.get("path") or "")
    if "." not in rel:
        return None
    ext = rel.rsplit(".", 1)[-1].strip().lower()
    return ext or None


def enrich_track_metadata(track: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build normalized metadata fields + quality scoring used by browse/search/AI layers.
    """
    title = normalize_text(track.get("title") or "")
    artist = normalize_text(track.get("artist") or "")
    album = normalize_text(track.get("album") or "")
    album_artist = normalize_text(track.get("album_artist") or "")
    genre = normalize_text(track.get("genre") or "")
    composer = normalize_text(track.get("composer") or "")
    musical_key = normalize_text(track.get("musical_key") or "")

    duration_sec = None
    try:
        raw_duration = track.get("duration_sec")
        if raw_duration is not None:
            duration_sec = float(raw_duration)
    except Exception:
        duration_sec = None

    year = track.get("year")
    bpm = track.get("bpm")
    codec = _codec_from_track(track)

    flags: list[str] = []
    score = 100

    if not title or title.lower().startswith("unknown"):
        flags.append("missing_title")
        score -= 20
    if not artist or artist.lower().startswith("unknown"):
        flags.append("missing_artist")
        score -= 20
    if not album or album.lower().startswith("unknown"):
        flags.append("missing_album")
        score -= 10
    if duration_sec is None or duration_sec <= 0:
        flags.append("invalid_duration")
        score -= 20
    if not codec:
        flags.append("missing_codec")
        score -= 10
    if year is not None:
        try:
            y = int(year)
            if y < 1900 or y > 2100:
                flags.append("year_out_of_range")
                score -= 5
        except Exception:
            flags.append("invalid_year")
            score -= 5
    if bpm is not None:
        try:
            b = float(bpm)
            if b <= 0 or b > 300:
                flags.append("bpm_out_of_range")
                score -= 5
        except Exception:
            flags.append("invalid_bpm")
            score -= 5

    score = max(0, min(100, score))
    format_family = None
    if codec in {"flac", "alac", "wav", "aiff", "ape", "wv", "dsf", "dff"}:
        format_family = "lossless"
    elif codec in {"mp3", "aac", "m4a", "ogg", "opus", "wma"}:
        format_family = "lossy"

    search_text = " ".join(
        part for part in [
            normalize_text_key(title),
            normalize_text_key(artist),
            normalize_text_key(album),
            normalize_text_key(album_artist),
            normalize_text_key(genre),
            normalize_text_key(composer),
            normalize_text_key(musical_key),
        ] if part
    )

    return {
        "title_norm": normalize_text_key(title),
        "artist_norm": normalize_text_key(artist),
        "album_norm": normalize_text_key(album),
        "album_artist_norm": normalize_text_key(album_artist),
        "genre_norm": normalize_text_key(genre),
        "composer_norm": normalize_text_key(composer),
        "musical_key_norm": normalize_text_key(musical_key),
        "codec_norm": codec,
        "format_family": format_family,
        "metadata_quality": score,
        "metadata_flags": flags,
        "search_text": search_text,
    }

def build_stream_url(user_id: str, t: Dict[str, Any]) -> Optional[str]:
    """Rebuild file URL using agent's base_url + stored rel_path (no re-encode here)."""
    st = load_agent(user_id)
    base = st.get("base_url")
    rel  = t.get("rel_path")
    if not base or not rel:
        return None
    return f"{base.rstrip('/')}/{rel.lstrip('/')}"
