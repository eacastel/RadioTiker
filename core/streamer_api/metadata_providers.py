from __future__ import annotations

from typing import Dict, Any, List, Optional, Tuple, Union
import os
import re
import time
from difflib import SequenceMatcher
import requests


RT_USER_AGENT = "RadioTiker-vnext/metadata-enricher (admin@radio.tiker.es)"
_URL_STATUS_CACHE: Dict[str, bool] = {}
_DISCOGS_JSON_CACHE: Dict[str, Dict[str, Any]] = {}
_MB_JSON_CACHE: Dict[str, Dict[str, Any]] = {}
_ACOUSTID_LAST_REQUEST_TS: float = 0.0


def _norm(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "").strip().lower())


def _sim(a: Any, b: Any) -> float:
    x, y = _norm(a), _norm(b)
    if not x and not y:
        return 1.0
    if not x or not y:
        return 0.0
    return SequenceMatcher(None, x, y).ratio()


def _year_from_date(v: Any) -> Optional[int]:
    m = re.search(r"(19|20)\d{2}", str(v or ""))
    if not m:
        return None
    try:
        return int(m.group(0))
    except Exception:
        return None


def _candidate(
    provider: str,
    score: float,
    patch: Dict[str, Any],
    ref: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "provider": provider,
        "score": round(max(0.0, min(1.0, score)), 4),
        "patch": {k: v for k, v in patch.items() if v is not None and v != ""},
        "reference": ref,
    }


def _dedupe_urls(urls: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for u in urls:
        s = str(u or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _clean_bio_text(value: Any, max_len: int = 2400) -> Optional[str]:
    txt = str(value or "").strip()
    if not txt:
        return None
    # Discogs profiles/notes can contain BBCode.
    txt = re.sub(r"\[/?[a-zA-Z0-9_=:#\-\"' ]+\]", "", txt)
    txt = re.sub(r"<[^>]+>", "", txt)
    txt = re.sub(r"https?://\S+", "", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    if not txt:
        return None
    # Drop obvious release/legal boilerplate that is not useful as a story.
    boilerplate = [
        r"\b(all rights reserved|copyright|remaster(?:ed|ing)?|track listing|tracklist)\b",
        r"[©℗]",
        r"\bmade in\b",
        r"\bbarcode\b",
        r"\bcatalog(?:ue)?\b",
        r"\bmatrix\b",
        r"\bcd\s*\d+\b",
        r"\bdisc\s*\d+\b",
    ]
    hits = sum(1 for p in boilerplate if re.search(p, txt, flags=re.IGNORECASE))
    if hits >= 3 and len(txt) > 280:
        return None
    # Keep stories concise in UI/db; giant blobs reduce quality.
    max_len = min(max_len, 900)
    if len(txt) > max_len:
        txt = txt[: max_len - 1].rstrip() + "…"
    return txt


def _url_alive(url: str) -> bool:
    if url in _URL_STATUS_CACHE:
        return _URL_STATUS_CACHE[url]
    ok = False
    try:
        r = requests.head(url, allow_redirects=True, timeout=(3, 6), headers={"User-Agent": RT_USER_AGENT})
        ok = r.status_code < 400
    except Exception:
        ok = False
    _URL_STATUS_CACHE[url] = ok
    return ok


def _first_alive(urls: List[str]) -> Optional[str]:
    for u in urls:
        if _url_alive(u):
            return u
    return None


def _discogs_get_json(url: str, headers: Dict[str, str]) -> Optional[Dict[str, Any]]:
    key = f"{url}|{headers.get('Authorization','')}"
    if key in _DISCOGS_JSON_CACHE:
        return _DISCOGS_JSON_CACHE[key]
    try:
        r = requests.get(url, headers=headers, timeout=(4, 12))
        r.raise_for_status()
        j = r.json()
        if isinstance(j, dict):
            _DISCOGS_JSON_CACHE[key] = j
            return j
        return None
    except Exception:
        return None


def _mb_get_json(url: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    key = f"{url}|{repr(sorted(params.items()))}"
    if key in _MB_JSON_CACHE:
        return _MB_JSON_CACHE[key]
    try:
        r = requests.get(
            url,
            params=params,
            timeout=(5, 15),
            headers={"User-Agent": RT_USER_AGENT},
        )
        r.raise_for_status()
        j = r.json()
        if isinstance(j, dict):
            _MB_JSON_CACHE[key] = j
            return j
    except Exception:
        return None
    return None


def _mb_release_group_bio(release_group_id: Optional[str]) -> Optional[str]:
    if not release_group_id:
        return None
    j = _mb_get_json(
        f"https://musicbrainz.org/ws/2/release-group/{release_group_id}",
        {"fmt": "json", "inc": "annotation"},
    )
    if not j:
        return None
    return _clean_bio_text(j.get("annotation"))


def _discogs_release_artist_meta(item: Dict[str, Any], headers: Dict[str, str]) -> Tuple[List[str], Optional[str], Optional[str]]:
    resource_url = item.get("resource_url")
    if not resource_url:
        return [], None, None
    rel = _discogs_get_json(str(resource_url), headers=headers)
    if not rel:
        return [], None, None

    urls: List[str] = []
    artist_bio: Optional[str] = None
    album_bio: Optional[str] = _clean_bio_text(rel.get("notes"))

    for art in (rel.get("artists") or []):
        aurl = art.get("resource_url")
        if not aurl:
            continue
        aj = _discogs_get_json(str(aurl), headers=headers)
        if not aj:
            continue
        for img in (aj.get("images") or []):
            u = img.get("uri150") or img.get("uri")
            if u:
                urls.append(str(u))
        if not artist_bio:
            artist_bio = _clean_bio_text(aj.get("profile"))
        if urls:
            break

    return _dedupe_urls(urls), artist_bio, album_bio


def _mb_request(query: str, limit: int) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    try:
        r = requests.get(
            "https://musicbrainz.org/ws/2/recording",
            params={"query": query, "fmt": "json", "limit": limit},
            headers={"User-Agent": RT_USER_AGENT},
            timeout=(5, 20),
        )
        r.raise_for_status()
        data = r.json()
        return data.get("recordings", []), None
    except Exception:
        return [], "request-failed"


def _search_musicbrainz(track: Dict[str, Any], limit: int = 5) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    title = str(track.get("title") or "").strip()
    artist = str(track.get("artist") or "").strip()
    album = str(track.get("album") or "").strip()
    if not title:
        return [], "missing-title"

    query_candidates: List[str] = []
    base = [f'recording:"{title}"']
    if artist:
        q = base + [f'artist:"{artist}"']
        if album:
            query_candidates.append(" AND ".join(q + [f'release:"{album}"']))
        query_candidates.append(" AND ".join(q))
    query_candidates.append(" AND ".join(base))
    query_candidates.append(title)

    recordings: List[Dict[str, Any]] = []
    last_err: Optional[str] = None
    for q in query_candidates:
        rows, err = _mb_request(q, limit=limit)
        if err:
            last_err = err
            continue
        if rows:
            recordings = rows
            last_err = None
            break
    if not recordings:
        return [], last_err

    out: List[Dict[str, Any]] = []
    for rec in recordings:
        rec_title = rec.get("title")
        rec_artist = ""
        ac = rec.get("artist-credit") or []
        if ac:
            first = ac[0]
            if isinstance(first, dict):
                artist_obj = first.get("artist") or {}
                rec_artist = artist_obj.get("name") or first.get("name") or ""
            else:
                rec_artist = str(first)
        releases = rec.get("releases") or []
        rec_album = releases[0].get("title") if releases else ""
        rec_year = _year_from_date(releases[0].get("date")) if releases else None
        rel_id = releases[0].get("id") if releases else None
        rg_id = None
        if releases and isinstance(releases[0].get("release-group"), dict):
            rg_id = releases[0].get("release-group", {}).get("id")
        artwork_candidates = _dedupe_urls([
            f"https://coverartarchive.org/release/{rel_id}/front-500" if rel_id else "",
            f"https://coverartarchive.org/release/{rel_id}/front-250" if rel_id else "",
            f"https://coverartarchive.org/release/{rel_id}/front" if rel_id else "",
            f"https://coverartarchive.org/release-group/{rg_id}/front-500" if rg_id else "",
            f"https://coverartarchive.org/release-group/{rg_id}/front-250" if rg_id else "",
            f"https://coverartarchive.org/release-group/{rg_id}/front" if rg_id else "",
        ])
        artwork_url = _first_alive(artwork_candidates)

        score = (
            0.55 * _sim(title, rec_title)
            + 0.35 * _sim(artist, rec_artist)
            + 0.10 * _sim(album, rec_album)
        )
        patch = {
            "title": rec_title or None,
            "artist": rec_artist or None,
            "album": rec_album or None,
            "year": rec_year,
            "artwork_url": artwork_url,
            "artwork_urls": artwork_candidates,
            "artist_image_urls": [],
            "artist_bio": None,
            "album_bio": _mb_release_group_bio(rg_id),
        }
        ref = {
            "recording_id": rec.get("id"),
            "release_id": rel_id,
            "release_group_id": rg_id,
            "release_date": releases[0].get("date") if releases else None,
        }
        out.append(_candidate("musicbrainz", score, patch, ref))
    out.sort(key=lambda x: x["score"], reverse=True)
    return out, None


def _search_discogs(track: Dict[str, Any], limit: int = 5) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    token = os.getenv("DISCOGS_TOKEN", "").strip()
    if not token:
        return [], "missing-token"

    title = str(track.get("title") or "").strip()
    artist = str(track.get("artist") or "").strip()
    album = str(track.get("album") or "").strip()
    if not title:
        return [], "missing-title"

    headers = {"User-Agent": RT_USER_AGENT, "Authorization": f"Discogs token={token}"}

    query_variants = []
    if artist and album:
        query_variants.append({
            "track": title, "artist": artist, "release_title": album, "per_page": limit, "page": 1
        })
    if artist:
        query_variants.append({
            "track": title, "artist": artist, "per_page": limit, "page": 1
        })
    query_variants.append({"track": title, "per_page": limit, "page": 1})
    query_variants.append({"q": " ".join([artist, title]).strip(), "per_page": limit, "page": 1})

    results = []
    last_err: Optional[str] = None
    for params in query_variants:
        params = {k: v for k, v in params.items() if v}
        try:
            r = requests.get(
                "https://api.discogs.com/database/search",
                params=params,
                headers=headers,
                timeout=(5, 20),
            )
            r.raise_for_status()
            data = r.json()
            rows = data.get("results", [])
            if rows:
                results = rows
                last_err = None
                break
        except Exception:
            last_err = "request-failed"
    if not results:
        return [], last_err

    out: List[Dict[str, Any]] = []
    for item in results:
        title_field = str(item.get("title") or "")
        # Common format: "Artist - Title"
        if " - " in title_field:
            disc_artist, disc_title = title_field.split(" - ", 1)
        else:
            disc_artist, disc_title = "", title_field
        disc_album = str(item.get("master_title") or item.get("title") or "")
        disc_year = item.get("year")
        try:
            disc_year = int(disc_year) if disc_year else None
        except Exception:
            disc_year = None

        score = (
            0.55 * _sim(title, disc_title)
            + 0.35 * _sim(artist, disc_artist)
            + 0.10 * _sim(album, disc_album)
        )
        cover_urls = _dedupe_urls([
            str(item.get("cover_image") or ""),
            str(item.get("thumb") or ""),
        ])
        artist_urls, artist_bio, album_bio = _discogs_release_artist_meta(item, headers=headers)
        patch = {
            "title": disc_title or None,
            "artist": disc_artist or None,
            "album": disc_album or None,
            "year": disc_year,
            "genre": ", ".join(item.get("genre") or []) or None,
            "artwork_url": _first_alive(cover_urls) or (cover_urls[0] if cover_urls else None),
            "artwork_urls": cover_urls,
            "artist_image_urls": artist_urls,
            "artist_bio": artist_bio,
            "album_bio": album_bio,
        }
        ref = {
            "discogs_id": item.get("id"),
            "resource_url": item.get("resource_url"),
        }
        out.append(_candidate("discogs", score, patch, ref))
    out.sort(key=lambda x: x["score"], reverse=True)
    return out, None


def _search_acoustid(track: Dict[str, Any], limit: int = 5) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    key = os.getenv("ACOUSTID_API_KEY", "").strip()
    if not key:
        return [], "missing-key"

    fp = str(track.get("acoustid_fingerprint") or "").strip()
    if not fp:
        return [], "missing-fingerprint"
    try:
        dur = float(track.get("acoustid_duration") or track.get("duration_sec") or 0.0)
    except Exception:
        dur = 0.0
    if dur <= 0:
        return [], "missing-duration"

    global _ACOUSTID_LAST_REQUEST_TS
    # Respect public rate limit guidance (max 3 req/s).
    wait = 0.34 - (time.time() - _ACOUSTID_LAST_REQUEST_TS)
    if wait > 0:
        time.sleep(wait)

    params = {
        "client": key,
        "meta": "recordings+releasegroups+compress",
        "format": "json",
        "duration": int(round(dur)),
        "fingerprint": fp,
    }
    try:
        r = requests.get("https://api.acoustid.org/v2/lookup", params=params, timeout=(5, 20))
        _ACOUSTID_LAST_REQUEST_TS = time.time()
        r.raise_for_status()
        payload = r.json()
    except Exception:
        return [], "request-failed"

    rows = payload.get("results") or []
    if not rows:
        return [], None

    title = str(track.get("title") or "").strip()
    artist = str(track.get("artist") or "").strip()
    album = str(track.get("album") or "").strip()

    out: List[Dict[str, Any]] = []
    for row in rows[: max(1, limit * 2)]:
        base_score = float(row.get("score") or 0.0)
        recs = row.get("recordings") or []
        if not recs:
            continue
        for rec in recs[:2]:
            rec_title = str(rec.get("title") or "").strip()
            rec_id = rec.get("id")
            rec_artist = ""
            artists = rec.get("artists") or []
            if artists:
                rec_artist = str((artists[0] or {}).get("name") or "").strip()
            releasegroups = rec.get("releasegroups") or []
            rg_id = None
            rg_title = ""
            if releasegroups:
                rg = releasegroups[0] or {}
                rg_id = rg.get("id")
                rg_title = str(rg.get("title") or "").strip()

            artwork_candidates = _dedupe_urls([
                f"https://coverartarchive.org/release-group/{rg_id}/front-500" if rg_id else "",
                f"https://coverartarchive.org/release-group/{rg_id}/front-250" if rg_id else "",
                f"https://coverartarchive.org/release-group/{rg_id}/front" if rg_id else "",
            ])
            artwork_url = _first_alive(artwork_candidates)
            score = (
                0.50 * base_score
                + 0.30 * _sim(title, rec_title)
                + 0.15 * _sim(artist, rec_artist)
                + 0.05 * _sim(album, rg_title)
            )
            patch = {
                "title": rec_title or None,
                "artist": rec_artist or None,
                "album": rg_title or None,
                "artwork_url": artwork_url,
                "artwork_urls": artwork_candidates,
                "artist_image_urls": [],
                "artist_bio": None,
                "album_bio": _mb_release_group_bio(rg_id),
            }
            ref = {
                "acoustid_score": base_score,
                "recording_id": rec_id,
                "release_group_id": rg_id,
            }
            out.append(_candidate("acoustid", score, patch, ref))
    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:limit], None


def search_candidates(track: Dict[str, Any], providers: Optional[List[str]] = None, include_errors: bool = False) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    chosen = [p.lower() for p in (providers or ["musicbrainz", "discogs", "acoustid"])]
    out: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    if "musicbrainz" in chosen:
        cands, err = _search_musicbrainz(track)
        out.extend(cands)
        if err:
            errors.append({"provider": "musicbrainz", "error": err})
    if "discogs" in chosen:
        cands, err = _search_discogs(track)
        out.extend(cands)
        if err:
            errors.append({"provider": "discogs", "error": err})
    if "acoustid" in chosen:
        cands, err = _search_acoustid(track)
        out.extend(cands)
        if err:
            errors.append({"provider": "acoustid", "error": err})
    out.sort(key=lambda x: x["score"], reverse=True)

    # Keep strongest metadata match, but enrich image fields from alternate providers.
    # This helps when MusicBrainz wins textual matching while Discogs has artist photos.
    if out:
        best = out[0]
        best_patch = best.setdefault("patch", {})
        best_artwork_urls = _dedupe_urls(list(best_patch.get("artwork_urls") or []))
        best_artist_urls = _dedupe_urls(list(best_patch.get("artist_image_urls") or []))

        for alt in out[1:]:
            alt_patch = alt.get("patch") or {}
            best_artwork_urls = _dedupe_urls(best_artwork_urls + list(alt_patch.get("artwork_urls") or []))
            best_artist_urls = _dedupe_urls(best_artist_urls + list(alt_patch.get("artist_image_urls") or []))

        if best_artwork_urls:
            best_patch["artwork_urls"] = best_artwork_urls
            best_patch["artwork_url"] = _first_alive(best_artwork_urls) or best_artwork_urls[0]
        if best_artist_urls:
            best_patch["artist_image_urls"] = best_artist_urls
        if not best_patch.get("artist_bio"):
            for alt in out[1:]:
                alt_bio = (alt.get("patch") or {}).get("artist_bio")
                if alt_bio:
                    best_patch["artist_bio"] = alt_bio
                    break
        if not best_patch.get("album_bio"):
            for alt in out[1:]:
                alt_bio = (alt.get("patch") or {}).get("album_bio")
                if alt_bio:
                    best_patch["album_bio"] = alt_bio
                    break

    if include_errors:
        return {"candidates": out, "errors": errors}
    return out
