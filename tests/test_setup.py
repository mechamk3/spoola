import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from test_scan import write_mp3

from serve.cli import main
from serve.config import Config, ConfigError, default_config_path, load, locate, save


class ConfigTests(unittest.TestCase):
    def test_round_trip_quotes_paths(self) -> None:
        with TemporaryDirectory() as raw:
            path = Path(raw) / "config.toml"
            library = Path(raw) / 'room "a"'
            config = Config(
                library=library,
                data=Path(raw) / "data",
                host="127.0.0.1",
                port=9000,
                path=path,
            )
            save(path, config)
            loaded = load(path)
            self.assertEqual(loaded.library, library)
            self.assertEqual(loaded.data, Path(raw) / "data")
            self.assertEqual(loaded.host, "127.0.0.1")
            self.assertEqual(loaded.port, 9000)

    def test_missing_key_and_bad_port(self) -> None:
        with TemporaryDirectory() as raw:
            path = Path(raw) / "config.toml"
            path.write_text('library = "/music"\n')
            with self.assertRaises(ConfigError):
                load(path)
            path.write_text('library = "/music"\ndata = "/data"\nport = "8080"\n')
            with self.assertRaises(ConfigError):
                load(path)

    def test_local_file_wins_over_the_config_home(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            with _isolated(root):
                local = Path.cwd() / "spoola.toml"
                home = default_config_path()
                save(local, _sample(local, root / "local-music", 1))
                save(home, _sample(home, root / "home-music", 2))
                found = load(locate(None))
                self.assertEqual(found.port, 1)
                local.unlink()
                found = load(locate(None))
                self.assertEqual(found.port, 2)

    def test_env_path_is_required(self) -> None:
        with TemporaryDirectory() as raw:
            missing = Path(raw) / "missing.toml"
            previous = os.environ.get("SPOOLA_CONFIG")
            os.environ["SPOOLA_CONFIG"] = str(missing)
            try:
                with self.assertRaises(ConfigError):
                    locate(None)
            finally:
                if previous is None:
                    os.environ.pop("SPOOLA_CONFIG", None)
                else:
                    os.environ["SPOOLA_CONFIG"] = previous


class SetupTests(unittest.TestCase):
    def test_setup_scans_and_later_commands_use_the_config(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            library = root / "music"
            write_mp3(library / "song.mp3", title="Hello")
            config_path = root / "config.toml"
            data = root / "catalog"
            code = main(
                [
                    "setup",
                    str(library),
                    "--config",
                    str(config_path),
                    "--data",
                    str(data),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8765",
                ]
            )
            self.assertEqual(code, 0)
            self.assertTrue((data / "library.db").is_file())
            loaded = load(config_path)
            self.assertEqual(loaded.library, library.resolve())
            self.assertEqual(loaded.port, 8765)

            again = main(["setup", str(library), "--config", str(config_path)])
            self.assertEqual(again, 2)

            replaced = main(
                ["setup", str(library), "--config", str(config_path), "--replace", "--port", "8766"]
            )
            self.assertEqual(replaced, 0)
            self.assertEqual(load(config_path).port, 8766)

            scanned = main(["scan", "--config", str(config_path)])
            self.assertEqual(scanned, 0)

    def test_setup_uses_the_default_locations(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            library = root / "music"
            write_mp3(library / "song.mp3", title="Hello")
            with _isolated(root):
                code = main(["setup", str(library), "--host", "127.0.0.1"])
                self.assertEqual(code, 0)
                config = load(default_config_path())
                self.assertEqual(config.library, library.resolve())
                self.assertTrue((config.data / "library.db").is_file())
                scanned = main(["scan"])
                self.assertEqual(scanned, 0)

    def test_scan_without_a_folder_explains_setup(self) -> None:
        with TemporaryDirectory() as raw:
            with _isolated(Path(raw)):
                code = main(["scan"])
        self.assertEqual(code, 2)

    def test_missing_music_folder(self) -> None:
        with TemporaryDirectory() as raw:
            code = main(["setup", str(Path(raw) / "missing"), "--config", str(Path(raw) / "c.toml")])
        self.assertEqual(code, 2)


def _sample(path: Path, library: Path, port: int) -> Config:
    return Config(library=library, data=library.parent / "data", host="127.0.0.1", port=port, path=path)


class _isolated:
    def __init__(self, root: Path):
        self.root = root
        self.previous_dir = ""
        self.previous_env: dict[str, str | None] = {}

    def __enter__(self) -> "_isolated":
        home = self.root / "home"
        (home / ".config").mkdir(parents=True)
        (home / ".local" / "share").mkdir(parents=True)
        work = self.root / "work"
        work.mkdir()
        self.previous_dir = os.getcwd()
        os.chdir(work)
        for key in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "SPOOLA_CONFIG"):
            self.previous_env[key] = os.environ.get(key)
        os.environ["HOME"] = str(home)
        os.environ["XDG_CONFIG_HOME"] = str(home / ".config")
        os.environ["XDG_DATA_HOME"] = str(home / ".local" / "share")
        os.environ.pop("SPOOLA_CONFIG", None)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        os.chdir(self.previous_dir)
        for key, value in self.previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
