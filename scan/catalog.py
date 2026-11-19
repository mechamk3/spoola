"""SQLite catalog the player reads. One row per audio file."""

import hashlib
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artworks (
    id TEXT PRIMARY KEY,
    hash TEXT NOT NULL UNIQUE,
    path TEXT NOT NULL,
    mime_type TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tracks (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    content_hash TEXT NOT NULL,
    filename TEXT NOT NULL,
    title TEXT,
    artist TEXT,
    album TEXT,
    album_artist TEXT,
    genre TEXT,
    year INTEGER,
    duration REAL NOT NULL,
    file_size INTEGER NOT NULL,
    modified_ns INTEGER NOT NULL,
    artwork_id TEXT,
    FOREIGN KEY (artwork_id) REFERENCES artworks(id)
);

CREATE INDEX IF NOT EXISTS tracks_by_artwork ON tracks(artwork_id);
CREATE INDEX IF NOT EXISTS tracks_by_hash ON tracks(content_hash);

CREATE TABLE IF NOT EXISTS ingestions (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    files_scanned INTEGER NOT NULL DEFAULT 0,
    files_added INTEGER NOT NULL DEFAULT 0,
    files_updated INTEGER NOT NULL DEFAULT 0,
    files_unchanged INTEGER NOT NULL DEFAULT 0,
    files_removed INTEGER NOT NULL DEFAULT 0,
    files_failed INTEGER NOT NULL DEFAULT 0,
    error_message TEXT
);
"""

_MIME_SUFFIX = {
    "jpeg": "jpg",
    "jpg": "jpg",
    "png": "png",
    "gif": "gif",
    "webp": "webp",
    "bmp": "bmp",
    "tif": "tif",
    "tiff": "tif",
}


@dataclass(frozen=True)
class StoredTrack:
    id: str
    file_size: int
    modified_ns: int


class Catalog:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.data_dir = db_path.parent
        self.connection: sqlite3.Connection | None = None

    def __enter__(self) -> "Catalog":
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self._ensure_schema()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        assert self.connection is not None
        try:
            if exc_type is None:
                self.connection.commit()
            else:
                self.connection.rollback()
        finally:
            self.connection.close()
            self.connection = None

    def set_library_path(self, path: str) -> None:
        self._db().execute(
            """
            INSERT INTO settings (key, value)
            VALUES ('library_path', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (path,),
        )

    def tracks_by_path(self) -> dict[str, StoredTrack]:
        rows = self._db().execute(
            "SELECT id, path, file_size, modified_ns FROM tracks"
        )
        return {
            row["path"]: StoredTrack(row["id"], row["file_size"], row["modified_ns"])
            for row in rows
        }

    def store_artwork(self, data: bytes, mime_type: str) -> str:
        digest = hashlib.sha256(data).hexdigest()
        existing = self._db().execute(
            "SELECT id FROM artworks WHERE hash = ?",
            (digest,),
        ).fetchone()
        if existing:
            return existing["id"]

        extension = _MIME_SUFFIX.get(mime_type.split("/")[-1].split(";")[0].strip().lower(), "bin")
        relative = f"artwork/{digest}.{extension}"
        destination = self.data_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)

        artwork_id = str(uuid4())
        self._db().execute(
            """
            INSERT INTO artworks (id, hash, path, mime_type)
            VALUES (?, ?, ?, ?)
            """,
            (artwork_id, digest, relative, mime_type),
        )
        return artwork_id

    def upsert_track(
        self,
        track_id: str | None,
        *,
        path: str,
        content_hash: str,
        filename: str,
        title: str | None,
        artist: str | None,
        album: str | None,
        album_artist: str | None,
        genre: str | None,
        year: int | None,
        duration: float,
        file_size: int,
        modified_ns: int,
        artwork_id: str | None,
    ) -> str:
        if track_id is not None:
            self._db().execute(
                """
                UPDATE tracks
                SET
                    content_hash = ?,
                    filename = ?,
                    title = ?,
                    artist = ?,
                    album = ?,
                    album_artist = ?,
                    genre = ?,
                    year = ?,
                    duration = ?,
                    file_size = ?,
                    modified_ns = ?,
                    artwork_id = ?
                WHERE id = ?
                """,
                (
                    content_hash,
                    filename,
                    title,
                    artist,
                    album,
                    album_artist,
                    genre,
                    year,
                    duration,
                    file_size,
                    modified_ns,
                    artwork_id,
                    track_id,
                ),
            )
            return "updated"

        self._db().execute(
            """
            INSERT INTO tracks (
                id, path, content_hash, filename,
                title, artist, album, album_artist,
                genre, year, duration, file_size,
                modified_ns, artwork_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                path,
                content_hash,
                filename,
                title,
                artist,
                album,
                album_artist,
                genre,
                year,
                duration,
                file_size,
                modified_ns,
                artwork_id,
            ),
        )
        return "added"

    def delete_tracks(self, paths: list[str]) -> int:
        if not paths:
            return 0
        self._db().executemany("DELETE FROM tracks WHERE path = ?", [(path,) for path in paths])
        return len(paths)

    def prune_artworks(self) -> list[Path]:
        """Drop artwork rows nothing references.

        The image files are returned and not deleted here. The caller removes
        them after the catalog transaction commits, so a failed scan cannot
        erase a cover the database still points at.
        """
        db = self._db()
        orphans = db.execute(
            """
            SELECT id, path FROM artworks
            WHERE id NOT IN (
                SELECT artwork_id FROM tracks WHERE artwork_id IS NOT NULL
            )
            """
        ).fetchall()
        for row in orphans:
            db.execute("DELETE FROM artworks WHERE id = ?", (row["id"],))

        pending = {self.data_dir / row["path"] for row in orphans}
        known = {row["path"] for row in db.execute("SELECT path FROM artworks")}
        artwork_dir = self.data_dir / "artwork"
        if artwork_dir.is_dir():
            for file in artwork_dir.iterdir():
                relative = f"artwork/{file.name}"
                if file.is_file() and relative not in known:
                    pending.add(file)
        return list(pending)

    def record_ingestion(
        self,
        *,
        started_at: str,
        finished_at: str,
        status: str,
        scanned: int = 0,
        added: int = 0,
        updated: int = 0,
        unchanged: int = 0,
        removed: int = 0,
        failed: int = 0,
        error_message: str | None = None,
    ) -> None:
        self._db().execute(
            """
            INSERT INTO ingestions (
                id, started_at, finished_at, status,
                files_scanned, files_added, files_updated,
                files_unchanged, files_removed, files_failed,
                error_message
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                started_at,
                finished_at,
                status,
                scanned,
                added,
                updated,
                unchanged,
                removed,
                failed,
                error_message,
            ),
        )

    def _ensure_schema(self) -> None:
        db = self._db()
        version = db.execute("PRAGMA user_version").fetchone()[0]
        has_tracks = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'tracks'"
        ).fetchone()
        if version != SCHEMA_VERSION and has_tracks:
            print("Rebuilding catalog because its schema is out of date.", file=sys.stderr)
            db.executescript(
                """
                DROP TABLE IF EXISTS tracks;
                DROP TABLE IF EXISTS artworks;
                DROP TABLE IF EXISTS ingestions;
                DROP TABLE IF EXISTS settings;
                """
            )
        if version != SCHEMA_VERSION or not has_tracks:
            db.executescript(SCHEMA)
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            db.commit()

    def _db(self) -> sqlite3.Connection:
        if self.connection is None:
            raise RuntimeError("Catalog is not open")
        return self.connection
