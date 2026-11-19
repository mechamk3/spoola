import json
import random
import sqlite3
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

from test_scan import PNG, write_mp3

from scan.walk import scan
from serve.cli import main
from serve.http import make_server, parse_range
from serve.station import Station


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def fetch(server, path: str, headers: dict[str, str] | None = None):
    host, port = server.server_address
    request = urllib.request.Request(f"http://{host}:{port}{path}", headers=headers or {})
    try:
        return urllib.request.urlopen(request, timeout=5)
    except urllib.error.HTTPError as error:
        return error


class StationTests(unittest.TestCase):
    def test_listeners_share_one_clock(self) -> None:
        with TemporaryDirectory() as raw:
            data = self._catalog(Path(raw), [("First", 10), ("Second", 5)])
            clock = Clock()
            station = Station(data, clock=clock, rng=random.Random(0))

            opening = station.on_air()
            assert opening is not None
            clock.now = 4
            later = station.on_air()
            assert later is not None
            self.assertEqual(later.track.id, opening.track.id)
            self.assertAlmostEqual(later.position, 4)

            clock.now = opening.track.duration + 0.4
            nxt = station.on_air()
            assert nxt is not None
            self.assertNotEqual(nxt.track.id, opening.track.id)
            self.assertAlmostEqual(nxt.position, 0.4)

    def test_next_song_is_known_before_this_one_ends(self) -> None:
        with TemporaryDirectory() as raw:
            data = self._catalog(Path(raw), [("First", 10), ("Second", 5), ("Third", 8)])
            clock = Clock()
            station = Station(data, clock=clock, rng=random.Random(0))
            opening = station.on_air()
            assert opening is not None and opening.upcoming is not None
            self.assertNotEqual(opening.upcoming.id, opening.track.id)
            clock.now = 1
            again = station.on_air()
            assert again is not None and again.upcoming is not None
            self.assertEqual(again.track.id, opening.track.id)
            self.assertEqual(again.upcoming.id, opening.upcoming.id)

            clock.now = opening.track.duration + 0.2
            following = station.on_air()
            assert following is not None and following.upcoming is not None
            self.assertEqual(following.track.id, opening.upcoming.id)
            self.assertNotEqual(following.upcoming.id, following.track.id)

    def test_one_track_starts_again(self) -> None:
        with TemporaryDirectory() as raw:
            data = self._catalog(Path(raw), [("Only", 10)])
            clock = Clock()
            station = Station(data, clock=clock, rng=random.Random(0))
            opening = station.on_air()
            assert opening is not None
            clock.now = 25
            again = station.on_air()
            assert again is not None
            self.assertEqual(again.track.id, opening.track.id)
            self.assertAlmostEqual(again.position, 5)
            assert opening.upcoming is not None
            self.assertEqual(opening.upcoming.id, opening.track.id)

    def test_zero_duration_is_skipped(self) -> None:
        with TemporaryDirectory() as raw:
            data = self._catalog(Path(raw), [("Empty", 0), ("Real", 8)])
            station = Station(data, clock=Clock(), rng=random.Random(0))
            on_air = station.on_air()
            assert on_air is not None
            self.assertEqual(on_air.track.display_title, "Real")

    def test_audio_outside_the_library_is_refused(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            data = self._catalog(root, [("Song", 30)])
            secret = root / "secret.mp3"
            secret.write_bytes(b"SECRET-BYTES")
            database = data / "library.db"
            with sqlite3.connect(database) as connection:
                connection.execute("UPDATE tracks SET path = '../secret.mp3'")
            bumped = database.stat().st_mtime_ns + 1_000_000
            database.touch()
            os_utime = __import__("os").utime
            os_utime(database, ns=(bumped, bumped))

            station = Station(data, clock=Clock(), rng=random.Random(0))
            on_air = station.on_air()
            assert on_air is not None
            located = station.track(on_air.track.id)
            assert located is not None
            from serve.station import resolve_inside

            self.assertIsNone(resolve_inside(located[1], located[0].path))

    def _catalog(self, root: Path, songs: list[tuple[str, float]]) -> Path:
        library = root / "music"
        data = root / "data"
        for title, _duration in songs:
            write_mp3(library / f"{title}.mp3", title=title, artwork=PNG)
        scan(library, data)
        if any(duration == 0 for _title, duration in songs):
            with sqlite3.connect(data / "library.db") as connection:
                for title, duration in songs:
                    connection.execute(
                        "UPDATE tracks SET duration = ? WHERE title = ?",
                        (duration, title),
                    )
        elif any(duration != 0 for _title, duration in songs):
            with sqlite3.connect(data / "library.db") as connection:
                for title, duration in songs:
                    connection.execute(
                        "UPDATE tracks SET duration = ? WHERE title = ?",
                        (duration, title),
                    )
        return data


class RangeTests(unittest.TestCase):
    def test_parse_range(self) -> None:
        self.assertEqual(parse_range("bytes=0-15", 100), (0, 15))
        self.assertEqual(parse_range("bytes=50-", 100), (50, 99))
        self.assertEqual(parse_range("bytes=-10", 100), (90, 99))
        with self.assertRaises(ValueError):
            parse_range("bytes=100-120", 100)
        with self.assertRaises(ValueError):
            parse_range("bytes=0-10", 0)


class ServerTests(unittest.TestCase):
    def test_page_station_audio_and_artwork(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            library = root / "music"
            data = root / "data"
            write_mp3(library / "song.mp3", title="Hello", artist="Band", artwork=PNG)
            outside = root / "secret.mp3"
            outside.write_bytes(b"SECRET-BYTES")
            scan(library, data)

            server = make_server(Station(data, rng=random.Random(0)), "127.0.0.1", 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            self.addCleanup(server.shutdown)
            self.addCleanup(server.server_close)
            thread.start()

            page = fetch(server, "/")
            self.assertEqual(page.status, 200)
            self.assertIn(b"/player.css", page.read())
            sheet = fetch(server, "/player.css")
            self.assertEqual(sheet.status, 200)
            self.assertIn("text/css", sheet.headers["Content-Type"])
            script = fetch(server, "/player.js")
            self.assertEqual(script.status, 200)
            self.assertIn(b"startListening", script.read())

            first = json.load(fetch(server, "/now"))
            second = json.load(fetch(server, "/now"))
            self.assertEqual(first["track"]["title"], "Hello")
            self.assertEqual(first["track"]["artist"], "Band")
            self.assertEqual(first["track"]["id"], second["track"]["id"])
            self.assertEqual(first["next"]["id"], first["track"]["id"])
            self.assertEqual(first["next"]["title"], "Hello")
            self.assertEqual(first["next"]["artworkType"], "image/png")
            self.assertIsNotNone(first["track"]["artwork"])

            artwork = fetch(server, first["track"]["artwork"])
            self.assertEqual(artwork.status, 200)
            self.assertEqual(artwork.headers["Content-Type"], "image/png")
            self.assertEqual(artwork.read(), PNG)

            audio_path = library / "song.mp3"
            audio = fetch(server, first["track"]["audio"], {"Range": "bytes=0-15"})
            self.assertEqual(audio.status, 206)
            self.assertEqual(audio.read(), audio_path.read_bytes()[:16])
            self.assertTrue(audio.headers["Content-Range"].startswith("bytes 0-15/"))

            rejected = fetch(server, first["track"]["audio"], {"Range": "bytes=999999-"})
            self.assertEqual(rejected.status, 416)

            with sqlite3.connect(data / "library.db") as connection:
                connection.execute("UPDATE tracks SET path = '../secret.mp3'")
            (data / "library.db").touch()
            slipped = fetch(server, first["track"]["audio"])
            self.assertEqual(slipped.status, 404)

    def test_missing_catalog(self) -> None:
        with TemporaryDirectory() as raw:
            code = main(["serve", "--data", raw, "--host", "127.0.0.1", "--port", "9"])
        self.assertEqual(code, 2)
