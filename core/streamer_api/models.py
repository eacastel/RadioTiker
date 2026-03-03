from typing import List, Optional
from pydantic import BaseModel

class Track(BaseModel):
    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    album_artist: Optional[str] = None
    genre: Optional[str] = None
    year: Optional[int] = None
    track_no: Optional[int] = None
    disc_no: Optional[int] = None
    composer: Optional[str] = None
    bpm: Optional[float] = None
    musical_key: Optional[str] = None
    codec: Optional[str] = None
    sample_rate: Optional[int] = None
    bit_depth: Optional[int] = None
    bitrate_kbps: Optional[int] = None
    channels: Optional[int] = None
    title_norm: Optional[str] = None
    artist_norm: Optional[str] = None
    album_norm: Optional[str] = None
    album_artist_norm: Optional[str] = None
    genre_norm: Optional[str] = None
    composer_norm: Optional[str] = None
    musical_key_norm: Optional[str] = None
    codec_norm: Optional[str] = None
    format_family: Optional[str] = None
    metadata_quality: Optional[int] = None
    metadata_flags: Optional[List[str]] = None
    search_text: Optional[str] = None
    artwork_url: Optional[str] = None
    artwork_urls: Optional[List[str]] = None
    artist_image_urls: Optional[List[str]] = None
    artist_bio: Optional[str] = None
    album_bio: Optional[str] = None
    path: Optional[str] = None
    rel_path: Optional[str] = None   # canonical relative path under agent root
    file_size: Optional[int] = None
    mtime: Optional[int] = None
    duration_sec: Optional[float] = None
    acoustid_fingerprint: Optional[str] = None
    acoustid_duration: Optional[float] = None
    track_id: str

class ScanPayload(BaseModel):
    user_id: str
    library: List[Track]
    library_version: Optional[int] = None
    replace: Optional[bool] = False

class AnnouncePayload(BaseModel):
    user_id: str
    base_url: str


class PlaylistCreatePayload(BaseModel):
    name: str


class PlaylistTrackUpdatePayload(BaseModel):
    track_ids: List[str]


class MetadataLibraryUpsertPayload(BaseModel):
    # Match fields are optional; at least one should be supplied.
    match_title: Optional[str] = None
    match_artist: Optional[str] = None
    match_album: Optional[str] = None
    # Patch fields applied when the match condition is met.
    patch: dict


class MetadataEnrichPayload(BaseModel):
    apply: Optional[bool] = False
    providers: Optional[List[str]] = None  # e.g. ["musicbrainz", "discogs"]
    min_score: Optional[float] = 0.78


class MetadataEnrichLibraryPayload(BaseModel):
    limit: Optional[int] = 25
    apply: Optional[bool] = False
    providers: Optional[List[str]] = None
    min_score: Optional[float] = 0.78
