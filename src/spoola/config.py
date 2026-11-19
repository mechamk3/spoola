"""The config file setup writes and scan and serve read."""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


class ConfigError(Exception):
    """The config file is missing, unreadable, or incomplete."""


@dataclass(frozen=True)
class Config:
    library: Path
    data: Path
    host: str
    port: int
    path: Path


def default_config_path() -> Path:
    root = os.environ.get("XDG_CONFIG_HOME")
    base = Path(root).expanduser() if root else Path.home() / ".config"
    return base / "spoola" / "config.toml"


def default_data_dir() -> Path:
    root = os.environ.get("XDG_DATA_HOME")
    base = Path(root).expanduser() if root else Path.home() / ".local" / "share"
    return base / "spoola"


def locate(explicit: Path | None) -> Path | None:
    """Find the config file.

    --config and SPOOLA_CONFIG must exist. Otherwise a spoola.toml in the
    current directory wins over the default in the config home.
    """
    if explicit is not None:
        path = explicit.expanduser()
        if not path.is_file():
            raise ConfigError(f"Config not found: {path}")
        return path
    env = os.environ.get("SPOOLA_CONFIG")
    if env:
        path = Path(env).expanduser()
        if not path.is_file():
            raise ConfigError(f"Config not found: {path}")
        return path
    local = Path("spoola.toml")
    if local.is_file():
        return local.resolve()
    default = default_config_path()
    if default.is_file():
        return default
    return None


def load(path: Path) -> Config:
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except OSError as exc:
        raise ConfigError(f"Could not read {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Could not read {path}: {exc}") from exc

    missing = [key for key in ("library", "data") if key not in raw]
    if missing:
        names = ", ".join(missing)
        raise ConfigError(f"{path} is missing {names}")
    library = raw["library"]
    data = raw["data"]
    host = raw.get("host", "0.0.0.0")
    port = raw.get("port", 8080)
    if not isinstance(library, str) or not library:
        raise ConfigError(f"{path} has an invalid library path")
    if not isinstance(data, str) or not data:
        raise ConfigError(f"{path} has an invalid data path")
    if not isinstance(host, str) or not host:
        raise ConfigError(f"{path} has an invalid host")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ConfigError(f"{path} has an invalid port")
    return Config(
        library=Path(library).expanduser(),
        data=Path(data).expanduser(),
        host=host,
        port=port,
        path=path,
    )


def save(path: Path, config: Config) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".toml.tmp")
    temporary.write_text(
        "\n".join(
            (
                "# Written by `spoola setup`. `spoola scan` and `spoola serve` read this file.",
                f"library = {_quote(str(config.library))}",
                f"data = {_quote(str(config.data))}",
                f"host = {_quote(config.host)}",
                f"port = {config.port}",
                "",
            )
        )
    )
    temporary.replace(path)


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
