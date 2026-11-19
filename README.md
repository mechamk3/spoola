# Spoola

Spoola is a music station for one local network. It plays a folder of music you already have. Every device that opens the page hears the same song, at the same moment, and the station keeps going when nobody is listening.

Your audio files stay where they are. Spoola reads them. It does not upload them, and it does not expect an account.

## How it fits together

Three pieces:

| Piece | What it is |
| --- | --- |
| Music folder | The files on disk. Spoola only reads this folder. |
| Catalog | A local index of titles, artists, albums, and cover art, built from that folder. |
| Station | One shared playback clock and a web page. `serve` runs it. |

`setup` writes the config and builds the catalog. `scan` refreshes the catalog when the folder changes. `serve` plays it.

```text
music folder
    │
    ▼
spoola scan  →  catalog (library.db + artwork)
    │
    ▼
spoola serve →  one station, one page
    │
    ▼
devices on the same network
```

The page shows the current track and its cover. Play joins the station at the live position. Pause stops only that device. The station keeps the clock, and the next press of Play catches up. A song that ends is followed by the next one in the shuffle.

## Setup

Python 3.11 or newer.

```bash
git clone https://github.com/mechamk3/spoola
cd spoola
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/spoola setup /path/to/music
.venv/bin/spoola serve
```

`setup` scans the folder and prints an address. Open that address from any device on the network.

After setup, the same two commands work from any directory:

```bash
spoola scan
spoola serve
```

## Where files live

| Path | What it holds |
| --- | --- |
| `~/.config/spoola/config.toml` | Music folder, catalog folder, address, and port. |
| `~/.local/share/spoola/library.db` | The catalog. One row per audio file. |
| `~/.local/share/spoola/artwork/` | Cover images, one file per distinct image. |

The music folder is not copied. Track paths in the database are relative to it.

`scan` and `serve` look for a config in this order:

1. `--config`, if you passed one
2. `SPOOLA_CONFIG`, if that variable is set
3. `./spoola.toml`
4. `~/.config/spoola/config.toml`

A `spoola.toml` in the current directory wins over the file in your home config, which is useful for a second library. `SPOOLA_CONFIG` and `--config` have to point at a file that exists.

To point the station at a different folder later:

```bash
spoola setup --replace /path/to/other-music
```

`--replace` keeps the catalog location, address, and port, and replaces the music folder. `--data` chooses a different catalog folder.

## What gets scanned

Audio files in the folder and its subfolders. The formats are mp3, flac, ogg, opus, m4a, mp4, aac, wav, and aiff. Hidden files and folders are skipped.

A later scan reads a file again only when its size or modification time changed. Files that disappeared are removed from the catalog. A scan made while `serve` is running is picked up without a restart: the current song finishes, and new files join the queue after it.

`spoola scan --force` re-reads every file. `spoola scan /some/other/folder` scans that folder once without changing the config.

## The network

`serve` listens on every interface of the machine, port 8080. That is how a phone on the same Wi-Fi reaches the page. There is no login. Anyone who can open the address can play the library, so this is meant for a home network, not the public internet.

To listen only on this computer:

```bash
spoola setup /path/to/music --host 127.0.0.1
```

`--port` chooses a different port. Pass either flag again with `--replace` to change an existing config.

## Run on boot

Copy [`contrib/spoola.service`](contrib/spoola.service) to `~/.config/systemd/user/spoola.service`. Set `ExecStart` to the `spoola` command inside the virtual environment, for example `/home/you/spoola/.venv/bin/spoola`. Then:

```bash
systemctl --user enable --now spoola
loginctl enable-linger "$USER"
```

The user service reads your config file and can see a music folder in your home directory. Linger keeps the station running after you log out.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m unittest discover -s tests
```
