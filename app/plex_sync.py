import os
import threading
import time

from plexapi.server import PlexServer

PLEX_URL = os.getenv("PLEX_URL", "")
PLEX_TOKEN = os.getenv("PLEX_TOKEN", "")
LIBRARIES = {
    "movie": os.getenv("PLEX_MOVIE_LIBRARY", "Movies"),
    "show": os.getenv("PLEX_TV_LIBRARY", "TV Shows"),
}

_cache: dict = {}
_lock = threading.Lock()


def configured() -> bool:
    return bool(PLEX_URL and PLEX_TOKEN)


def norm(title: str) -> str:
    return "".join(c for c in title.lower() if c.isalnum())


def connect() -> PlexServer:
    return PlexServer(PLEX_URL, PLEX_TOKEN, timeout=30)


def library_items(srv: PlexServer, media_type: str, force: bool = False, ttl: int = 60):
    """All titles in the Plex library for a media type (cached briefly)."""
    with _lock:
        hit = _cache.get(media_type)
        if hit and not force and time.time() - hit[0] < ttl:
            return hit[1]
        items = srv.library.section(LIBRARIES[media_type]).all()
        _cache[media_type] = (time.time(), items)
        return items


def index_by_title(plex_items) -> dict:
    idx: dict = {}
    for p in plex_items:
        idx.setdefault(norm(p.title), []).append(p)
    return idx


def match(idx: dict, title: str, year):
    for p in idx.get(norm(title), []):
        if year is None or getattr(p, "year", None) == year:
            return p
    return None


def sync_all(lists) -> dict:
    """Create/update one Plex collection per list. Returns a per-list report."""
    srv = connect()
    report = {}
    for media_type in ("movie", "show"):
        section = srv.library.section(LIBRARIES[media_type])
        idx = index_by_title(library_items(srv, media_type, force=True))
        for lst in lists:
            items = [i for i in lst.items if i.media_type == media_type]
            matched, missing = [], []
            for it in items:
                p = match(idx, it.title, it.year)
                (matched if p else missing).append(p or it.title)
            existing = [c for c in section.collections(title=lst.name) if c.title == lst.name]
            if existing:
                coll = existing[0]
                current = {i.ratingKey: i for i in coll.items()}
                wanted = {p.ratingKey for p in matched}
                to_add = [p for p in matched if p.ratingKey not in current]
                to_remove = [i for k, i in current.items() if k not in wanted]
                if to_add:
                    coll.addItems(to_add)
                if to_remove:
                    coll.removeItems(to_remove)
            elif matched:
                section.createCollection(lst.name, items=matched)
            r = report.setdefault(lst.name, {"matched": 0, "missing": []})
            r["matched"] += len(matched)
            r["missing"] += missing
    return report
