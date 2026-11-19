"""Walk a music folder and make the catalog match it.

A file is opened only when its size or modification time differs from the
catalog, or when --force is set. One scan is one database transaction: a
crash leaves the previous catalog in place.
"""

import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from scan.catalog import Catalog
from scan.extract import AUDIO_EXTENSIONS, extract_track


@dataclass(frozen=True)
class Failure:
    path: str
    error: str


@dataclass
class Summary:
    library: Path
    catalog: Path
    scanned: int = 0
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    failed: list[Failure] = field(default_factory=list)


def scan(library: Path, data_dir: Path, *, force: bool = False) -> Summary:
    library = library.expanduser().resolve()
    data_dir = data_dir.expanduser().resolve()
    if not library.is_dir():
        raise NotADirectoryError(f"Music folder not found: {library}")
    data_dir.mkdir(parents=True, exist_ok=True)

    db_path = data_dir / "library.db"
    started = _now()
    try:
        with Catalog(db_path) as catalog:
            summary, artwork_files = _sync(catalog, library, db_path, force=force)
            catalog.record_ingestion(
                started_at=started,
                finished_at=_now(),
                status="completed",
                scanned=summary.scanned,
                added=summary.added,
                updated=summary.updated,
                unchanged=summary.unchanged,
                removed=summary.removed,
                failed=len(summary.failed),
                error_message=_failure_text(summary.failed),
            )
    except Exception as exc:
        _record_failure(db_path, started, exc)
        raise
    _delete_files(artwork_files)
    return summary


def _sync(
    catalog: Catalog, library: Path, db_path: Path, *, force: bool
) -> tuple[Summary, list[Path]]:
    summary = Summary(library=library, catalog=db_path)
    files, walk_failures = _audio_files(library)
    summary.failed.extend(walk_failures)
    # A directory we could not read looks the same as one that was deleted.
    # Skip removals so a permissions error cannot wipe the catalog.
    allow_removals = not walk_failures

    catalog.set_library_path(str(library))
    existing = catalog.tracks_by_path()
    keep: set[str] = set()

    for path in files:
        relative = path.relative_to(library).as_posix()
        summary.scanned += 1
        try:
            stat = path.stat()
        except OSError as exc:
            summary.failed.append(Failure(relative, str(exc)))
            if relative in existing:
                keep.add(relative)
            continue

        previous = existing.get(relative)
        if (
            previous is not None
            and not force
            and previous.file_size == stat.st_size
            and previous.modified_ns == stat.st_mtime_ns
        ):
            summary.unchanged += 1
            keep.add(relative)
            continue

        try:
            extracted = extract_track(path)
        except Exception as exc:
            summary.failed.append(Failure(relative, str(exc)))
            if previous is not None:
                keep.add(relative)
            continue

        artwork_id = None
        if extracted.artwork is not None:
            artwork_id = catalog.store_artwork(*extracted.artwork)
        status = catalog.upsert_track(
            None if previous is None else previous.id,
            path=relative,
            content_hash=extracted.content_hash,
            filename=path.name,
            title=extracted.title,
            artist=extracted.artist,
            album=extracted.album,
            album_artist=extracted.album_artist,
            genre=extracted.genre,
            year=extracted.year,
            duration=extracted.duration,
            file_size=stat.st_size,
            modified_ns=stat.st_mtime_ns,
            artwork_id=artwork_id,
        )
        if status == "added":
            summary.added += 1
        else:
            summary.updated += 1
        keep.add(relative)

    if allow_removals:
        summary.removed = catalog.delete_tracks([path for path in existing if path not in keep])
    return summary, catalog.prune_artworks()


def _audio_files(root: Path) -> tuple[list[Path], list[Failure]]:
    found: list[Path] = []
    failures: list[Failure] = []

    def onerror(error: OSError) -> None:
        name = error.filename or str(root)
        failures.append(Failure(name, str(error)))

    for dirpath, dirnames, filenames in os.walk(root, onerror=onerror):
        dirnames[:] = sorted(name for name in dirnames if not name.startswith("."))
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            if Path(name).suffix.lower() in AUDIO_EXTENSIONS:
                found.append(Path(dirpath) / name)
    return found, failures


def _failure_text(failures: list[Failure]) -> str | None:
    if not failures:
        return None
    text = "\n".join(f"{failure.path}: {failure.error}" for failure in failures)
    return text[:4000]


def _record_failure(db_path: Path, started: str, exc: Exception) -> None:
    try:
        with Catalog(db_path) as catalog:
            catalog.record_ingestion(
                started_at=started,
                finished_at=_now(),
                status="failed",
                error_message=str(exc)[:4000],
            )
    except Exception:
        print("Could not record the failed scan.", file=sys.stderr)


def _delete_files(paths: list[Path]) -> None:
    for path in paths:
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            print(f"Could not remove {path}: {exc}", file=sys.stderr)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
