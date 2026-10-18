import json
import os
import threading
import uuid

from .db import DATA_DIR

PATH = os.path.join(DATA_DIR, "settings.json")
_lock = threading.Lock()


def load() -> dict:
    try:
        with open(PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def update(**values) -> dict:
    with _lock:
        data = load()
        data.update(values)
        tmp = PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.chmod(tmp, 0o600)  # holds the Plex token
        os.replace(tmp, PATH)
        return data


def client_id() -> str:
    """Stable id so Listarr shows up as one device in your Plex account."""
    cid = load().get("client_id")
    if not cid:
        cid = str(uuid.uuid4())
        update(client_id=cid)
    return cid


def clear_plex():
    update(token="", server_name="", server_url="", movie_library="", tv_library="")
