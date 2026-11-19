"""One shared station. Every listener follows the same track and clock."""

import random
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Track:
    id: str
    path: str
    filename: str
    title: str | None
    artist: str | None
    album: str | None
    duration: float
    artwork_id: str | None

    @property
    def display_title(self) -> str:
        if self.title:
            return self.title
        return Path(self.filename).stem or self.filename


@dataclass(frozen=True)
class Artwork:
    path: str
    mime_type: str


@dataclass(frozen=True)
class Library:
    root: Path | None
    tracks: dict[str, Track]
    artworks: dict[str, Artwork]


@dataclass(frozen=True)
class OnAir:
    track: Track
    position: float


class Station:
    def __init__(
        self,
        data_dir: Path,
        *,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ):
        self.data_dir = data_dir
        self._clock = clock
        self._rng = rng or random.Random()
        self._lock = threading.Lock()
        self._library = Library(None, {}, {})
        self._mtime: int | None = None
        self._order: list[str] = []
        self._index = 0
        self._started = 0.0

    def on_air(self) -> OnAir | None:
        with self._lock:
            self._prepare()
            if not self._order:
                return None
            track = self._library.tracks[self._order[self._index]]
            position = min(max(self._clock() - self._started, 0.0), track.duration)
            return OnAir(track, position)

    def track(self, track_id: str) -> tuple[Track, Path] | None:
        with self._lock:
            self._prepare()
            found = self._library.tracks.get(track_id)
            root = self._library.root
        if found is None or root is None:
            return None
        return found, root

    def artwork(self, artwork_id: str) -> tuple[Artwork, Path] | None:
        with self._lock:
            self._prepare()
            found = self._library.artworks.get(artwork_id)
        if found is None:
            return None
        return found, self.data_dir

    def track_count(self) -> int:
        with self._lock:
            self._prepare()
            return len(self._library.tracks)

    def library_root(self) -> Path | None:
        with self._lock:
            self._prepare()
            return self._library.root

    def _prepare(self) -> None:
        self._reload_if_needed()
        if not self._order and _playable(self._library):
            self._queue(avoid=None)
            self._started = self._clock()
        self._catch_up()

    def _reload_if_needed(self) -> None:
        database = self.data_dir / "library.db"
        if not database.is_file():
            self._library = Library(None, {}, {})
            self._order = []
            self._mtime = None
            return
        mtime = database.stat().st_mtime_ns
        if self._mtime == mtime:
            return
        current = self._order[self._index] if self._order and self._index < len(self._order) else None
        started = self._started
        self._library = read_library(database)
        self._mtime = mtime
        if current and current in self._library.tracks and self._library.tracks[current].duration > 0:
            self._queue_keeping(current)
            self._started = started
            return
        self._order = []
        self._index = 0

    def _catch_up(self) -> None:
        now = self._clock()
        steps = 0
        while self._order:
            track = self._library.tracks.get(self._order[self._index])
            if track is None or track.duration <= 0:
                self._advance()
                continue
            if now - self._started < track.duration:
                return
            self._started += track.duration
            self._advance()
            steps += 1
            if steps > 100_000:
                self._started = now
                self._queue(avoid=None)
                return

    def _advance(self) -> None:
        finished = self._order[self._index] if self._order else None
        if self._index + 1 < len(self._order):
            self._index += 1
            return
        self._queue(avoid=finished)

    def _queue(self, avoid: str | None) -> None:
        ids = [track.id for track in self._library.tracks.values() if track.duration > 0]
        self._rng.shuffle(ids)
        if avoid and len(ids) > 1 and ids[0] == avoid:
            ids.append(ids.pop(0))
        self._order = ids
        self._index = 0

    def _queue_keeping(self, current: str) -> None:
        rest = [
            track.id
            for track in self._library.tracks.values()
            if track.duration > 0 and track.id != current
        ]
        self._rng.shuffle(rest)
        self._order = [current, *rest]
        self._index = 0


def read_library(database: Path) -> Library:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        setting = connection.execute(
            "SELECT value FROM settings WHERE key = 'library_path'"
        ).fetchone()
        root = Path(setting["value"]) if setting else None
        rows = connection.execute(
            """
            SELECT
                tracks.id,
                tracks.path,
                tracks.filename,
                tracks.title,
                tracks.artist,
                tracks.album,
                tracks.duration,
                artworks.id AS artwork_id,
                artworks.path AS artwork_path,
                artworks.mime_type AS artwork_mime
            FROM tracks
            LEFT JOIN artworks ON artworks.id = tracks.artwork_id
            """
        ).fetchall()
    finally:
        connection.close()

    tracks: dict[str, Track] = {}
    artworks: dict[str, Artwork] = {}
    for row in rows:
        artwork_id = row["artwork_id"]
        if artwork_id and row["artwork_path"]:
            artworks[artwork_id] = Artwork(row["artwork_path"], row["artwork_mime"] or "application/octet-stream")
        tracks[row["id"]] = Track(
            id=row["id"],
            path=row["path"],
            filename=row["filename"],
            title=row["title"],
            artist=row["artist"],
            album=row["album"],
            duration=float(row["duration"]),
            artwork_id=artwork_id,
        )
    return Library(root, tracks, artworks)


def resolve_inside(root: Path, relative: str) -> Path | None:
    base = root.resolve()
    candidate = (base / relative).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


def _playable(library: Library) -> bool:
    return any(track.duration > 0 for track in library.tracks.values())
