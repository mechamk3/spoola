"""Read tags, duration, and cover art from one audio file."""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from mutagen import File
from mutagen.mp4 import MP4Cover

AUDIO_EXTENSIONS = {
    ".mp3",
    ".flac",
    ".ogg",
    ".opus",
    ".m4a",
    ".mp4",
    ".aac",
    ".wav",
    ".aiff",
    ".aif",
}

_YEAR = re.compile(r"\d{4}")


@dataclass(frozen=True)
class ExtractedTrack:
    content_hash: str
    title: str | None
    artist: str | None
    album: str | None
    album_artist: str | None
    genre: str | None
    year: int | None
    duration: float
    artwork: tuple[bytes, str] | None


def file_hash(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def parse_year(value: str | None) -> int | None:
    if not value:
        return None
    match = _YEAR.search(value)
    if match is None:
        return None
    return int(match.group(0))


def extract_track(path: Path) -> ExtractedTrack:
    audio = File(path)
    if audio is None or getattr(audio, "info", None) is None:
        raise ValueError("unreadable audio file")
    try:
        duration = round(float(audio.info.length), 2)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("audio file has no duration") from exc

    easy = File(path, easy=True)
    return ExtractedTrack(
        content_hash=file_hash(path),
        title=_tag(easy, "title"),
        artist=_tag(easy, "artist"),
        album=_tag(easy, "album"),
        album_artist=_tag(easy, "albumartist"),
        genre=_tag(easy, "genre"),
        year=parse_year(_tag(easy, "date")),
        duration=duration,
        artwork=_artwork(audio),
    )


def _tag(audio, key: str) -> str | None:
    tags = getattr(audio, "tags", None)
    if not tags:
        return None
    try:
        values = tags.get(key)
    except (AttributeError, TypeError):
        return None
    if not values:
        return None
    text = str(values[0]).strip()
    return text or None


def _artwork(audio) -> tuple[bytes, str] | None:
    for picture in getattr(audio, "pictures", None) or []:
        data = bytes(picture.data)
        if data:
            return data, picture.mime or "image/jpeg"

    tags = getattr(audio, "tags", None)
    if not tags:
        return None

    try:
        items = list(tags.items())
    except AttributeError:
        items = []
    for key, value in items:
        if not str(key).startswith("APIC"):
            continue
        data = bytes(getattr(value, "data", b""))
        if data:
            return data, getattr(value, "mime", None) or "image/jpeg"

    getter = getattr(tags, "get", None)
    covers = getter("covr") if getter else None
    if not covers:
        return None
    image = covers[0]
    data = bytes(image)
    if not data:
        return None
    if getattr(image, "imageformat", None) == MP4Cover.FORMAT_PNG:
        return data, "image/png"
    return data, "image/jpeg"
