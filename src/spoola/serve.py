"""Local web server for the shared station."""

import json
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from spoola.station import OnAir, Station, resolve_inside

PLAYER_HTML = Path(__file__).with_name("player.html").read_bytes()

AUDIO_TYPES = {
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".aac": "audio/aac",
    ".wav": "audio/wav",
    ".aiff": "audio/aiff",
    ".aif": "audio/aiff",
}

_ID = set("0123456789abcdefABCDEF-")


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_server(station: Station, host: str, port: int) -> Server:
    return Server((host, port), handler_for(station))


def handler_for(station: Station) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            dispatch(self, station, head=False)

        def do_HEAD(self) -> None:
            dispatch(self, station, head=True)

        def log_message(self, format: str, *args) -> None:
            return

    return Handler


def dispatch(handler: BaseHTTPRequestHandler, station: Station, *, head: bool) -> None:
    path = urlparse(handler.path).path
    if path == "/":
        send_bytes(handler, 200, PLAYER_HTML, "text/html; charset=utf-8", head=head)
        return
    if path == "/now":
        body = json.dumps(now_payload(station.on_air()), ensure_ascii=False).encode()
        send_bytes(handler, 200, body, "application/json; charset=utf-8", head=head, cache="no-store")
        return
    if path.startswith("/tracks/") and path.endswith("/audio"):
        track_id = path.removeprefix("/tracks/").removesuffix("/audio")
        send_audio(handler, station, track_id, head=head)
        return
    if path.startswith("/artworks/"):
        artwork_id = path.removeprefix("/artworks/")
        send_artwork(handler, station, artwork_id, head=head)
        return
    send_bytes(handler, 404, b"Not found\n", "text/plain; charset=utf-8", head=head)


def now_payload(on_air: OnAir | None) -> dict:
    if on_air is None:
        return {"track": None, "position": 0}
    track = on_air.track
    return {
        "position": round(on_air.position, 2),
        "track": {
            "id": track.id,
            "title": track.display_title,
            "artist": track.artist,
            "album": track.album,
            "duration": track.duration,
            "audio": f"/tracks/{track.id}/audio",
            "artwork": f"/artworks/{track.artwork_id}" if track.artwork_id else None,
        },
    }


def send_audio(handler: BaseHTTPRequestHandler, station: Station, track_id: str, *, head: bool) -> None:
    if not _safe_id(track_id):
        send_bytes(handler, 404, b"Not found\n", "text/plain; charset=utf-8", head=head)
        return
    found = station.track(track_id)
    if found is None:
        send_bytes(handler, 404, b"Not found\n", "text/plain; charset=utf-8", head=head)
        return
    track, root = found
    path = resolve_inside(root, track.path)
    if path is None:
        send_bytes(handler, 404, b"Not found\n", "text/plain; charset=utf-8", head=head)
        return
    mime = AUDIO_TYPES.get(path.suffix.lower(), "application/octet-stream")
    send_file(handler, path, mime, head=head)


def send_artwork(handler: BaseHTTPRequestHandler, station: Station, artwork_id: str, *, head: bool) -> None:
    if not _safe_id(artwork_id):
        send_bytes(handler, 404, b"Not found\n", "text/plain; charset=utf-8", head=head)
        return
    found = station.artwork(artwork_id)
    if found is None:
        send_bytes(handler, 404, b"Not found\n", "text/plain; charset=utf-8", head=head)
        return
    artwork, root = found
    path = resolve_inside(root, artwork.path)
    if path is None:
        send_bytes(handler, 404, b"Not found\n", "text/plain; charset=utf-8", head=head)
        return
    send_file(handler, path, artwork.mime_type, head=head, cache="public, max-age=86400")


def send_file(
    handler: BaseHTTPRequestHandler,
    path: Path,
    mime: str,
    *,
    head: bool,
    cache: str = "no-cache",
) -> None:
    size = path.stat().st_size
    header = handler.headers.get("Range")
    if header:
        try:
            start, end = parse_range(header, size)
        except ValueError:
            handler.send_response(416)
            handler.send_header("Content-Range", f"bytes */{size}")
            handler.send_header("Accept-Ranges", "bytes")
            handler.end_headers()
            return
        status = 206
        length = end - start + 1
    else:
        start, end = 0, max(size - 1, 0)
        status = 200
        length = size

    handler.send_response(status)
    handler.send_header("Content-Type", mime)
    handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("Content-Length", str(length))
    handler.send_header("Cache-Control", cache)
    handler.send_header("X-Content-Type-Options", "nosniff")
    if status == 206:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    handler.end_headers()
    if head or length == 0:
        return
    try:
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining:
                chunk = handle.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                handler.wfile.write(chunk)
                remaining -= len(chunk)
    except (BrokenPipeError, ConnectionResetError):
        return


def parse_range(header: str, size: int) -> tuple[int, int]:
    """Return an inclusive byte span. Raise ValueError when it cannot be served."""
    if size <= 0 or not header.startswith("bytes="):
        raise ValueError(header)
    spec = header.removeprefix("bytes=").split(",", 1)[0].strip()
    if "-" not in spec:
        raise ValueError(header)
    start_text, end_text = spec.split("-", 1)
    if start_text == "":
        try:
            suffix = int(end_text)
        except ValueError as exc:
            raise ValueError(header) from exc
        if suffix <= 0:
            raise ValueError(header)
        suffix = min(suffix, size)
        return size - suffix, size - 1
    try:
        start = int(start_text)
        end = int(end_text) if end_text else size - 1
    except ValueError as exc:
        raise ValueError(header) from exc
    if start < 0 or start >= size or end < start:
        raise ValueError(header)
    return start, min(end, size - 1)


def send_bytes(
    handler: BaseHTTPRequestHandler,
    status: int,
    body: bytes,
    content_type: str,
    *,
    head: bool,
    cache: str = "no-cache",
) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", cache)
    handler.end_headers()
    if not head:
        handler.wfile.write(body)


def listen_urls(host: str, port: int) -> list[str]:
    if host in {"0.0.0.0", "::"}:
        urls = [f"http://127.0.0.1:{port}"]
        lan = _lan_ip()
        if lan and lan not in {"127.0.0.1", "0.0.0.0"}:
            urls.append(f"http://{lan}:{port}")
        return urls
    return [f"http://{host}:{port}"]


def _lan_ip() -> str | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 1))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def _safe_id(value: str) -> bool:
    return bool(value) and len(value) <= 64 and set(value) <= _ID
