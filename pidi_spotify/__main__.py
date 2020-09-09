import argparse
import signal
import time
from pathlib import Path
from threading import Thread

import requests
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

from .fifo import FIFO
from .st7789 import DisplayST7789

FIFO_NAME = "/tmp/spotify_album_display"
CACHE_DIR = "${HOME}/pidi-spotify/cache/"

running = False


class State:
    def __init__(self):
        self.running = True
        self.started = 0
        self.duration_ms = 0
        self.position_ms = 0

        # Arguments listed in order for update_overlay
        self.shuffle = False
        self.repeat = False
        self.state = "play"
        self.volume = 100
        # self.progress = attribute
        self.elapsed = 0
        self.album_name = ""
        self.artist_name = ""
        self.track_name = ""

        self._index = 0

    @property
    def progress(self):
        elapsed_ms = (time.time() - self.started) * 1000
        try:
            return (self.position_ms + elapsed_ms) / self.duration_ms
        except ZeroDivisionError:
            return 0

    def __iter__(self):
        self._index = 0
        return self

    def __next__(self):
        try:
            result = [
                self.shuffle,
                self.repeat,
                self.state,
                self.volume,
                self.progress,
                self.elapsed,
                self.album_name,
                self.artist_name,
                self.track_name,
            ][self._index]
            self._index += 1
            return result
        except IndexError:
            raise StopIteration


def command_volume(volume):
    state.volume = int(volume)


def command_seek(position_ms):
    try:
        state.position_ms = int(position_ms)
    except ValueError:
        state.position_ms = 0
    state.started = time.time()


def command_track(track_id, position_ms):
    track = spotify.track(track_id)

    image_url = None
    album_id = track["album"]["id"]

    state.duration_ms = int(track["duration_ms"])
    command_seek(position_ms)

    state.album_name = track["album"]["name"]
    state.artist_name = track["album"]["artists"][0]["name"]
    state.track_name = track["name"]

    for image in track["album"]["images"]:
        if image["height"] == 300:
            image_url = image["url"]

    image_cache_path = Path(f"{CACHE_DIR}/{album_id}.png")
    if not image_cache_path.is_file():
        print("Missing cached image, loading now..")
        image = requests.get(image_url)
        with open(image_cache_path, "wb+") as f:
            f.write(image.content)

    display.update_album_art(image_cache_path)


def display_update():
    while state.running:
        display.update_overlay(*state)
        display.redraw()
        time.sleep(1.0 / 15)


def signal_handler(sig, frame):
    state.running = False


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fifo-name", default=FIFO_NAME, type=str)
    DisplayST7789.add_args(parser)

    args = parser.parse_args()
    args.size = 240

    fifo = FIFO(args.fifo_name)
    display = DisplayST7789(args)

    auth_manager = SpotifyClientCredentials()
    spotify = spotipy.Spotify(auth_manager=auth_manager)

    state = State()

    signal.signal(signal.SIGINT, signal_handler)

    _t_display_update = Thread(target=display_update)
    _t_display_update.start()

    print(f"""PiDi Spotify Running

Listening on FIFO: {args.fifo_name}

Press Ctrl+C to exit
""")

    with fifo as fifo:
        while state.running:
            command = fifo.read()
            if command is None or len(command) == 0:
                time.sleep(0.1)
                continue

            command = command.split(":")
            command_args = command[1:]
            command_fn = command[0]

            try:
                globals()[f"command_{command_fn}"](*command_args)
                print(f"Command {command_fn} args: {','.join(command_args)}")
            except KeyError:
                print(f"Unrecognised command {command_fn}")

    state.running = False
    _t_display_update.join()
