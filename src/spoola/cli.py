"""Command line for the spoola catalog and station."""

import argparse
import sys
from pathlib import Path

from spoola.config import (
    Config,
    ConfigError,
    default_config_path,
    default_data_dir,
    load,
    locate,
    save,
)
from spoola.scan import Summary, scan
from spoola.serve import listen_urls, make_server
from spoola.station import Station

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="spoola",
        description="Self-hosted music radio for your own network.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    setup_parser = commands.add_parser(
        "setup",
        help="Write a config file and scan a music folder",
    )
    setup_parser.add_argument("library", type=Path, help="Folder of audio files")
    setup_parser.add_argument(
        "--config",
        type=Path,
        help=f"Config file to write (default: {default_config_path()})",
    )
    setup_parser.add_argument(
        "--data",
        type=Path,
        help=f"Catalog folder (default: {default_data_dir()})",
    )
    setup_parser.add_argument(
        "--host",
        help=f"Address the station listens on (default: {DEFAULT_HOST})",
    )
    setup_parser.add_argument(
        "--port",
        type=int,
        help=f"Port the station listens on (default: {DEFAULT_PORT})",
    )
    setup_parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace an existing config file",
    )

    scan_parser = commands.add_parser(
        "scan",
        help="Read a music folder and update the catalog",
    )
    scan_parser.add_argument(
        "library",
        nargs="?",
        type=Path,
        help="Folder of audio files (default: the folder in the config file)",
    )
    _add_config_argument(scan_parser)
    _add_data_argument(scan_parser)
    scan_parser.add_argument(
        "--force",
        action="store_true",
        help="Re-read every file, including files that have not changed",
    )

    serve_parser = commands.add_parser(
        "serve",
        help="Play the catalog as one shared station",
    )
    _add_config_argument(serve_parser)
    _add_data_argument(serve_parser)
    serve_parser.add_argument(
        "--host",
        help=f"Address to listen on (default: {DEFAULT_HOST}, or the config file)",
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        help=f"Port to listen on (default: {DEFAULT_PORT}, or the config file)",
    )

    args = parser.parse_args(argv)
    if args.command == "setup":
        return _setup(args)
    if args.command == "scan":
        return _scan(args)
    return _serve(args)


def _add_config_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        type=Path,
        help="Config file to read (default: ./spoola.toml, then ~/.config/spoola/config.toml)",
    )


def _add_data_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--data",
        type=Path,
        help="Folder for library.db and artwork (default: the config file, or ./data)",
    )


def _setup(args: argparse.Namespace) -> int:
    library = args.library.expanduser()
    if not library.is_dir():
        print(f"Music folder not found: {library}", file=sys.stderr)
        return 2
    port = DEFAULT_PORT if args.port is None else args.port
    host = args.host or DEFAULT_HOST
    if not 1 <= port <= 65535:
        print(f"Port must be from 1 to 65535: {port}", file=sys.stderr)
        return 2
    destination = (args.config or default_config_path()).expanduser()
    if destination.exists() and not args.replace:
        print(f"Config already exists: {destination}", file=sys.stderr)
        print(
            f"Edit it, or replace it with spoola setup --replace {library}",
            file=sys.stderr,
        )
        return 2
    host, port, data_dir = _setup_choices(args, destination, host=host, port=port)
    if data_dir.exists() and not data_dir.is_dir():
        print(f"Data path is not a folder: {data_dir}", file=sys.stderr)
        return 2

    config = Config(
        library=library.resolve(),
        data=data_dir.resolve(),
        host=host,
        port=port,
        path=destination,
    )
    save(destination, config)
    code = _run_scan(config.library, config.data, force=False)
    if code != 0:
        return code
    print()
    print(f"config   {destination}")
    print()
    print("Start the station")
    print("  spoola serve")
    print()
    print("Then open")
    for url in listen_urls(config.host, config.port):
        print(f"  {url}")
    return 0


def _scan(args: argparse.Namespace) -> int:
    try:
        config = _read_config(args.config)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    if args.library is not None:
        library = args.library.expanduser()
    elif config is not None:
        library = config.library
    else:
        print("Pass a music folder, or run spoola setup /path/to/music", file=sys.stderr)
        return 2
    if not library.is_dir():
        print(f"Music folder not found: {library}", file=sys.stderr)
        return 2
    data_dir = _data_dir(args.data, config)
    if data_dir.exists() and not data_dir.is_dir():
        print(f"Data path is not a folder: {data_dir}", file=sys.stderr)
        return 2
    return _run_scan(library, data_dir, force=args.force)


def _serve(args: argparse.Namespace) -> int:
    try:
        config = _read_config(args.config)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    data_dir = _data_dir(args.data, config).resolve()
    host = args.host or (config.host if config is not None else DEFAULT_HOST)
    port = args.port if args.port is not None else (config.port if config is not None else DEFAULT_PORT)
    if not 1 <= port <= 65535:
        print(f"Port must be from 1 to 65535: {port}", file=sys.stderr)
        return 2
    database = data_dir / "library.db"
    if not database.is_file():
        print(f"Catalog not found: {database}", file=sys.stderr)
        print("Run spoola setup /path/to/music", file=sys.stderr)
        return 2

    station = Station(data_dir)
    try:
        server = make_server(station, host, port)
    except OSError as exc:
        print(f"Could not listen on {host}:{port}: {exc}", file=sys.stderr)
        return 1

    bound_port = server.server_address[1]
    if config is not None:
        print(f"config   {config.path}", flush=True)
    print(f"library  {station.library_root()}", flush=True)
    print(f"tracks   {station.track_count()}", flush=True)
    print(flush=True)
    print("Listening", flush=True)
    for url in listen_urls(host, bound_port):
        print(f"  {url}", flush=True)
    print(flush=True)
    print("Anyone on this network who can open the page can play the library.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
    return 0


def _setup_choices(
    args: argparse.Namespace,
    destination: Path,
    *,
    host: str,
    port: int,
) -> tuple[str, int, Path]:
    """Keep host, port, and data from an existing file unless this run sets them."""
    data_dir = args.data
    current: Config | None = None
    if destination.is_file():
        try:
            current = load(destination)
        except ConfigError:
            current = None
    if current is not None:
        if args.host is None:
            host = current.host
        if args.port is None:
            port = current.port
        if data_dir is None:
            data_dir = current.data
    if data_dir is None:
        data_dir = default_data_dir()
    return host, port, data_dir.expanduser()


def _read_config(explicit: Path | None) -> Config | None:
    found = locate(explicit)
    if found is None:
        return None
    return load(found)


def _data_dir(explicit: Path | None, config: Config | None) -> Path:
    if explicit is not None:
        return explicit.expanduser()
    if config is not None:
        return config.data
    return Path("data")


def _run_scan(library: Path, data_dir: Path, *, force: bool) -> int:
    summary = scan(library, data_dir, force=force)
    _print_summary(summary)
    return 0


def _print_summary(summary: Summary) -> None:
    print(f"library  {summary.library}")
    print(f"catalog  {summary.catalog}")
    print()
    rows = (
        ("scanned", summary.scanned),
        ("added", summary.added),
        ("updated", summary.updated),
        ("unchanged", summary.unchanged),
        ("removed", summary.removed),
        ("failed", len(summary.failed)),
    )
    for label, value in rows:
        print(f"{label:<12}{value}")
    for failure in summary.failed:
        print(f"failed  {failure.path}: {failure.error}", file=sys.stderr)
