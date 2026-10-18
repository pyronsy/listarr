import threading
import time

from plexapi.myplex import MyPlexAccount, MyPlexPinLogin
from plexapi.server import PlexServer

from . import settings

_cache: dict = {}
_lock = threading.Lock()
_pin: dict = {"login": None}


def reset_cache():
    with _lock:
        _cache.clear()


def norm(title: str) -> str:
    return "".join(c for c in title.lower() if c.isalnum())


# ---- connection state -------------------------------------------------------

def signed_in() -> bool:
    return bool(settings.load().get("token"))


def server_chosen() -> bool:
    s = settings.load()
    return bool(s.get("server_url") or s.get("server_name"))


def libraries() -> dict:
    s = settings.load()
    libs = {"movie": s.get("movie_library", ""), "show": s.get("tv_library", "")}
    return {k: v for k, v in libs.items() if v}


def configured() -> bool:
    return signed_in() and server_chosen() and bool(libraries())


# ---- Plex sign-in (browser PIN flow) ----------------------------------------

def start_login(forward_url: str) -> str:
    pin = MyPlexPinLogin(
        oauth=True,
        headers={
            "X-Plex-Product": "Listarr",
            "X-Plex-Client-Identifier": settings.client_id(),
        },
    )
    _pin["login"] = pin
    return pin.oauthUrl(forward_url)


def login_pending() -> bool:
    return _pin["login"] is not None


def poll_login() -> bool:
    """Check whether the user finished signing in at plex.tv; stores the token."""
    pin = _pin["login"]
    if pin is None:
        return False
    try:
        if pin.checkLogin() and pin.token:
            settings.update(token=pin.token)
            _pin["login"] = None
            return True
        if pin.expired:
            _pin["login"] = None
    except Exception:
        _pin["login"] = None
    return False


def account() -> MyPlexAccount:
    return MyPlexAccount(token=settings.load()["token"])


def servers() -> list:
    return [r.name for r in account().resources() if "server" in (r.provides or "")]


def connect() -> PlexServer:
    s = settings.load()
    if s.get("server_url"):
        return PlexServer(s["server_url"], s["token"], timeout=30)
    return account().resource(s["server_name"]).connect(timeout=30)


def sections() -> list:
    return [(sec.title, sec.type) for sec in connect().library.sections()]


# ---- library access ---------------------------------------------------------

def library_items(srv: PlexServer, media_type: str, force: bool = False, ttl: int = 60):
    """All titles in the chosen Plex library for a media type (cached briefly)."""
    name = libraries().get(media_type)
    if not name:
        kind = "movie" if media_type == "movie" else "TV"
        raise ValueError(f"No {kind} library selected in Settings.")
    with _lock:
        hit = _cache.get(media_type)
        if hit and not force and time.time() - hit[0] < ttl:
            return hit[1]
        items = srv.library.section(name).all()
        _cache[media_type] = (time.time(), items)
        return items


def plex_ids(p) -> set:
    ids = set()
    for g in getattr(p, "guids", None) or []:
        scheme, _, val = (getattr(g, "id", "") or "").partition("://")
        if val:
            ids.add((scheme, val))
    return ids


def item_ids(it) -> list:
    pairs = (("imdb", it.imdb_id), ("tmdb", it.tmdb_id), ("tvdb", it.tvdb_id))
    return [(k, v) for k, v in pairs if v]


def build_index(plex_items):
    by_title, by_id = {}, {}
    for p in plex_items:
        by_title.setdefault(norm(p.title), []).append(p)
        for key in plex_ids(p):
            by_id[key] = p
    return by_title, by_id


def match(index, it):
    """Match a list item to a Plex item by external id, else by title and year."""
    by_title, by_id = index
    for key in item_ids(it):
        if key in by_id:
            return by_id[key]
    for p in by_title.get(norm(it.title), []):
        if it.year is None or getattr(p, "year", None) == it.year:
            return p
    return None


def sync_all(lists) -> dict:
    """Create/update one Plex collection per list. Returns a per-list report."""
    srv = connect()
    report = {}
    for media_type, lib_name in libraries().items():
        section = srv.library.section(lib_name)
        idx = build_index(library_items(srv, media_type, force=True))
        for lst in lists:
            items = [i for i in lst.items if i.media_type == media_type]
            matched, missing = [], []
            for it in items:
                p = match(idx, it)
                if p:
                    matched.append(p)
                else:
                    missing.append(it.title)
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
