"""Title search against TMDB, IMDb (via OMDb) and TVDB, merged into one result list."""
import re
import time
from concurrent.futures import ThreadPoolExecutor

import requests

TIMEOUT = 8
LIMIT = 8
SOURCES = ("tmdb", "omdb", "tvdb")
LABELS = {"tmdb": "TMDB", "omdb": "IMDb", "tvdb": "TVDB"}


class ProviderError(Exception):
    pass


def norm(title: str) -> str:
    return "".join(c for c in title.lower() if c.isalnum())


def _year(value):
    m = re.match(r"\s*(\d{4})", str(value or ""))
    return int(m.group(1)) if m else None


def _r(source, title, year, media_type, overview="", poster="", imdb="", tmdb="", tvdb=""):
    return {
        "source": source, "title": title, "year": year, "media_type": media_type,
        "overview": overview or "", "poster": poster or "",
        "imdb_id": imdb or "", "tmdb_id": tmdb or "", "tvdb_id": tvdb or "",
    }


# ---- TMDB -------------------------------------------------------------------

def _tmdb_get(path, key, **params):
    headers = {}
    if key.startswith("eyJ"):  # v4 read access token
        headers["Authorization"] = f"Bearer {key}"
    else:
        params["api_key"] = key
    r = requests.get(f"https://api.themoviedb.org/3{path}", params=params, headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _tmdb_img(path):
    return f"https://image.tmdb.org/t/p/w92{path}" if path else ""


def search_tmdb(q, key, media):
    out = []
    if media in ("movie", "both"):
        for x in _tmdb_get("/search/movie", key, query=q, include_adult="false").get("results", [])[:LIMIT]:
            if x.get("title"):
                out.append(_r("tmdb", x["title"], _year(x.get("release_date")), "movie",
                              x.get("overview"), _tmdb_img(x.get("poster_path")), tmdb=str(x["id"])))
    if media in ("show", "both"):
        for x in _tmdb_get("/search/tv", key, query=q).get("results", [])[:LIMIT]:
            if x.get("name"):
                out.append(_r("tmdb", x["name"], _year(x.get("first_air_date")), "show",
                              x.get("overview"), _tmdb_img(x.get("poster_path")), tmdb=str(x["id"])))
    return out


def enrich_ids(entry, tmdb_key):
    """Fill in missing IMDb/TVDB ids for a TMDB-sourced entry. Best effort."""
    is_show = entry["media_type"] == "show"
    if not (tmdb_key and entry.get("tmdb_id")):
        return entry
    if entry.get("imdb_id") and (not is_show or entry.get("tvdb_id")):
        return entry
    try:
        ids = _tmdb_get(f"/{'tv' if is_show else 'movie'}/{entry['tmdb_id']}/external_ids", tmdb_key)
    except Exception:
        return entry
    entry["imdb_id"] = entry.get("imdb_id") or ids.get("imdb_id") or ""
    if is_show:
        entry["tvdb_id"] = entry.get("tvdb_id") or str(ids.get("tvdb_id") or "")
    return entry


# ---- IMDb via OMDb ----------------------------------------------------------

def search_omdb(q, key, media):
    params = {"apikey": key, "s": q}
    if media == "movie":
        params["type"] = "movie"
    elif media == "show":
        params["type"] = "series"
    r = requests.get("https://www.omdbapi.com/", params=params, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if data.get("Response") != "True":
        if "api key" in str(data.get("Error", "")).lower():
            raise ProviderError("API key rejected")
        return []
    out = []
    for x in data.get("Search", [])[:LIMIT]:
        kind = {"movie": "movie", "series": "show"}.get(x.get("Type"))
        if not kind:
            continue
        poster = x.get("Poster") if str(x.get("Poster", "")).startswith("http") else ""
        out.append(_r("omdb", x.get("Title", ""), _year(x.get("Year")), kind, poster=poster, imdb=x.get("imdbID", "")))
    return out


# ---- TVDB (API v4) ----------------------------------------------------------

_tvdb = {"key": None, "token": None, "at": 0.0}


def _tvdb_token(key, pin, force=False):
    if not force and _tvdb["key"] == key and _tvdb["token"] and time.time() - _tvdb["at"] < 20 * 86400:
        return _tvdb["token"]
    body = {"apikey": key}
    if pin:
        body["pin"] = pin
    r = requests.post("https://api4.thetvdb.com/v4/login", json=body, timeout=TIMEOUT)
    r.raise_for_status()
    _tvdb.update(key=key, token=r.json()["data"]["token"], at=time.time())
    return _tvdb["token"]


def _tvdb_search(q, kind, key, pin):
    for attempt in (0, 1):
        token = _tvdb_token(key, pin, force=bool(attempt))
        r = requests.get("https://api4.thetvdb.com/v4/search",
                         params={"query": q, "type": kind, "limit": LIMIT},
                         headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT)
        if r.status_code == 401 and attempt == 0:
            continue
        r.raise_for_status()
        return r.json().get("data") or []
    return []


def search_tvdb(q, key, pin, media):
    kinds = []
    if media in ("movie", "both"):
        kinds.append(("movie", "movie"))
    if media in ("show", "both"):
        kinds.append(("series", "show"))
    out = []
    for tvdb_type, kind in kinds:
        for x in _tvdb_search(q, tvdb_type, key, pin)[:LIMIT]:
            title = x.get("name") or x.get("title")
            if not title:
                continue
            imdb = next((str(i.get("id")) for i in x.get("remote_ids") or []
                         if str(i.get("sourceName", "")).upper() == "IMDB"), "")
            poster = x.get("image_url") or x.get("thumbnail") or ""
            out.append(_r("tvdb", title, _year(x.get("year")), kind, x.get("overview"),
                          poster if poster.startswith("http") else "",
                          imdb=imdb, tvdb=str(x.get("tvdb_id") or "")))
    return out


# ---- combine ----------------------------------------------------------------

def _fail(e):
    if isinstance(e, ProviderError):
        return str(e)
    if isinstance(e, requests.HTTPError):  # never include the URL: it can carry the key
        code = getattr(e.response, "status_code", None)
        return "API key rejected" if code in (401, 403) else f"request failed (HTTP {code})"
    if isinstance(e, requests.RequestException):
        return "couldn't reach the service"
    return "unexpected error"


def merge(raw):
    merged, by_imdb, by_key = [], {}, {}
    for r in raw:
        label = LABELS[r["source"]]
        k = (r["media_type"], norm(r["title"]), r["year"])
        target = (by_imdb.get(r["imdb_id"]) if r["imdb_id"] else None) or by_key.get(k)
        if target is None:
            target = {f: v for f, v in r.items() if f != "source"}
            target["sources"] = [label]
            merged.append(target)
        else:
            if label not in target["sources"]:
                target["sources"].append(label)
            for f in ("imdb_id", "tmdb_id", "tvdb_id", "overview", "poster"):
                if not target[f] and r[f]:
                    target[f] = r[f]
        if target["imdb_id"]:
            by_imdb[target["imdb_id"]] = target
        by_key[k] = target
    merged.sort(key=lambda m: -len(m["sources"]))
    return merged


def search_all(q, media, sources, keys):
    def run(src):
        if src == "tmdb":
            return search_tmdb(q, keys["tmdb"], media)
        if src == "omdb":
            return search_omdb(q, keys["omdb"], media)
        return search_tvdb(q, keys["tvdb"], keys.get("tvdb_pin", ""), media)

    ordered = [s for s in SOURCES if s in sources]
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {s: ex.submit(run, s) for s in ordered}
    raw, errors = [], {}
    for s in ordered:
        try:
            raw.extend(futures[s].result())
        except Exception as e:
            errors[LABELS[s]] = _fail(e)
    return merge(raw), errors
