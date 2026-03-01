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
    path: Optional[str] = None
    rel_path: Optional[str] = None   # canonical relative path under agent root
    file_size: Optional[int] = None
    mtime: Optional[int] = None
    duration_sec: Optional[float] = None
    track_id: str

class ScanPayload(BaseModel):
    user_id: str
    library: List[Track]
    library_version: Optional[int] = None
    replace: Optional[bool] = False

class AnnouncePayload(BaseModel):
    user_id: str
    base_url: str
