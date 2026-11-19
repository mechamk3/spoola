import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from mutagen.id3 import APIC, TALB, TDRC, TIT2, TPE1, TPE2, TCON
from mutagen.mp3 import MP3

from scan.extract import parse_year
from scan.walk import scan
from serve.cli import main

# 1x1 PNG. The scanner stores the bytes; it does not decode them.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def write_mp3(path: Path, *, title: str, artist: str = "Artist", artwork: bytes | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # MPEG1 Layer III, 128 kbps, 44100 Hz. Frame length is 417 bytes.
    frame = bytes([0xFF, 0xFB, 0x90, 0x64]) + b"\x00" * 413
    path.write_bytes(frame * 2)
    audio = MP3(path)
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    assert tags is not None
    tags.setall("TIT2", [TIT2(encoding=3, text=title)])
    tags.setall("TPE1", [TPE1(encoding=3, text=artist)])
    tags.setall("TALB", [TALB(encoding=3, text="Album")])
    tags.setall("TPE2", [TPE2(encoding=3, text="Album Artist")])
    tags.setall("TCON", [TCON(encoding=3, text="Jazz")])
    tags.setall("TDRC", [TDRC(encoding=3, text="1998")])
    if artwork is not None:
        tags.setall(
            "APIC",
            [APIC(encoding=3, mime="image/png", type=3, desc="cover", data=artwork)],
        )
    audio.save()


def tracks(db_path: Path) -> list[sqlite3.Row]:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        return list(connection.execute("SELECT * FROM tracks ORDER BY path"))


class ScanTests(unittest.TestCase):
    def test_parse_year(self) -> None:
        self.assertEqual(parse_year("1998-01-01"), 1998)
        self.assertIsNone(parse_year("no year"))
        self.assertIsNone(parse_year(None))

    def test_scan_adds_updates_and_removes(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            library = root / "music"
            data = root / "data"
            song = library / "Band" / "Album" / "song.mp3"
            other = library / "other.mp3"
            write_mp3(song, title="First", artwork=PNG)
            write_mp3(other, title="Second", artwork=PNG)
            (library / "notes.txt").write_text("ignore me")
            (library / ".hidden").mkdir()
            write_mp3(library / ".hidden" / "secret.mp3", title="Secret")

            first = scan(library, data)
            self.assertEqual((first.added, first.updated, first.unchanged, first.removed), (2, 0, 0, 0))
            self.assertEqual(first.failed, [])

            rows = tracks(data / "library.db")
            self.assertEqual([row["path"] for row in rows], ["Band/Album/song.mp3", "other.mp3"])
            self.assertEqual(rows[0]["title"], "First")
            self.assertEqual(rows[0]["artist"], "Artist")
            self.assertEqual(rows[0]["year"], 1998)
            self.assertEqual(rows[0]["artwork_id"], rows[1]["artwork_id"])
            song_id = rows[0]["id"]

            with sqlite3.connect(data / "library.db") as connection:
                library_path = connection.execute(
                    "SELECT value FROM settings WHERE key = 'library_path'"
                ).fetchone()[0]
                artworks = connection.execute("SELECT path, mime_type FROM artworks").fetchall()
            self.assertEqual(library_path, str(library.resolve()))
            self.assertEqual(len(artworks), 1)
            artwork_file = data / artworks[0][0]
            self.assertEqual(artwork_file.read_bytes(), PNG)
            self.assertEqual(artworks[0][1], "image/png")

            second = scan(library, data)
            self.assertEqual((second.added, second.updated, second.unchanged), (0, 0, 2))
            self.assertEqual(tracks(data / "library.db")[0]["id"], song_id)

            write_mp3(song, title="Retitled", artwork=PNG)
            third = scan(library, data)
            self.assertEqual((third.added, third.updated, third.unchanged), (0, 1, 1))
            refreshed = tracks(data / "library.db")
            self.assertEqual(refreshed[0]["id"], song_id)
            self.assertEqual(refreshed[0]["title"], "Retitled")

            song.unlink()
            other.unlink()
            fourth = scan(library, data)
            self.assertEqual(fourth.removed, 2)
            self.assertEqual(tracks(data / "library.db"), [])
            self.assertFalse(artwork_file.exists())

    def test_bad_file_does_not_drop_its_old_row(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            library = root / "music"
            data = root / "data"
            song = library / "song.mp3"
            write_mp3(song, title="Keep me")
            scan(library, data)

            song.write_bytes(b"this is not audio")
            summary = scan(library, data)
            self.assertEqual(len(summary.failed), 1)
            self.assertEqual(summary.removed, 0)
            self.assertEqual(tracks(data / "library.db")[0]["title"], "Keep me")

    def test_cli_missing_folder(self) -> None:
        with TemporaryDirectory() as raw:
            code = main(["scan", str(Path(raw) / "missing")])
        self.assertEqual(code, 2)

    def test_cli_scan(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            library = root / "music"
            write_mp3(library / "song.mp3", title="Hello")
            code = main(["scan", str(library), "--data", str(root / "data")])
            self.assertEqual(code, 0)
            self.assertEqual(len(tracks(root / "data" / "library.db")), 1)


if __name__ == "__main__":
    unittest.main()
