import asyncio
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from . import plex_sync, providers, settings
from .db import Item, MediaList, SessionLocal, init_db

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
SYNC_MINUTES = int(os.getenv("SYNC_INTERVAL_MINUTES", "0"))
last_sync: dict = {"at": None, "report": {}, "error": None}


def run_sync():
    with SessionLocal() as s:
        lists = s.query(MediaList).all()
        try:
            last_sync.update(report=plex_sync.sync_all(lists), error=None)
        except Exception as e:  # surface Plex errors in the UI
            last_sync.update(error=str(e))
        last_sync["at"] = datetime.now().strftime("%Y-%m-%d %H:%M")


async def scheduler():
    while True:
        await asyncio.sleep(SYNC_MINUTES * 60)
        if plex_sync.configured():
            await asyncio.to_thread(run_sync)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(scheduler()) if SYNC_MINUTES > 0 else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="Listarr", lifespan=lifespan)


def render(request: Request, name: str, **ctx):
    return templates.TemplateResponse(request, name, {"plex": plex_sync.configured(), **ctx})


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def index(request: Request):
    with SessionLocal() as s:
        lists = s.query(MediaList).order_by(MediaList.name).all()
        return render(request, "index.html", lists=lists, last=last_sync)


@app.post("/lists")
def create_list(name: str = Form(...), description: str = Form("")):
    name = name.strip()
    with SessionLocal() as s:
        if name and not s.query(MediaList).filter_by(name=name).first():
            lst = MediaList(name=name, description=description.strip())
            s.add(lst)
            s.commit()
            return RedirectResponse(f"/lists/{lst.id}", status_code=303)
    return RedirectResponse("/", status_code=303)


@app.get("/lists/{list_id}")
def show_list(request: Request, list_id: int, added: int = 0, skipped: int = 0):
    with SessionLocal() as s:
        lst = s.get(MediaList, list_id)
        if not lst:
            return RedirectResponse("/", status_code=303)
        return render(request, "list.html", lst=lst, items=list(lst.items), added=added, skipped=skipped)


LINE = re.compile(r"^(.*?)\s*\((\d{4})\)\s*$")


@app.post("/lists/{list_id}/items")
def add_items(list_id: int, titles: str = Form(...), media_type: str = Form("movie")):
    media_type = "show" if media_type == "show" else "movie"
    with SessionLocal() as s:
        lst = s.get(MediaList, list_id)
        if lst:
            for line in titles.splitlines():
                line = line.strip()
                if not line:
                    continue
                m = LINE.match(line)
                title, year = (m.group(1), int(m.group(2))) if m else (line, None)
                s.add(Item(list_id=list_id, title=title, year=year, media_type=media_type))
            s.commit()
    return RedirectResponse(f"/lists/{list_id}", status_code=303)


@app.post("/lists/{list_id}/items/{item_id}/delete")
def delete_item(list_id: int, item_id: int):
    with SessionLocal() as s:
        item = s.get(Item, item_id)
        if item and item.list_id == list_id:
            s.delete(item)
            s.commit()
    return RedirectResponse(f"/lists/{list_id}", status_code=303)


@app.post("/lists/{list_id}/delete")
def delete_list(list_id: int):
    with SessionLocal() as s:
        lst = s.get(MediaList, list_id)
        if lst:
            s.delete(lst)
            s.commit()
    return RedirectResponse("/", status_code=303)


@app.post("/sync")
async def sync():
    if plex_sync.configured():
        await asyncio.to_thread(run_sync)
    return RedirectResponse("/", status_code=303)


@app.get("/library")
def library(request: Request, type: str = "movie", list: str = "", q: str = ""):
    media_type = "show" if type == "show" else "movie"
    if not plex_sync.configured():
        return render(request, "library.html", rows=[], lists=[], error="Plex isn't connected yet.", type=media_type, sel=list, q=q)
    with SessionLocal() as s:
        lists = s.query(MediaList).order_by(MediaList.name).all()
        by_title: dict = {}
        by_id: dict = {}
        for lst in lists:
            for it in lst.items:
                if it.media_type == media_type:
                    by_title.setdefault(plex_sync.norm(it.title), []).append((it.year, lst))
                    for key in plex_sync.item_ids(it):
                        by_id.setdefault(key, []).append(lst)
        try:
            plex_items = plex_sync.library_items(plex_sync.connect(), media_type)
        except Exception as e:
            return render(request, "library.html", rows=[], lists=lists, error=f"Couldn't reach Plex: {e}", type=media_type, sel=list, q=q)
        rows = []
        for p in plex_items:
            if q and q.lower() not in p.title.lower():
                continue
            found = {}
            for key in plex_sync.plex_ids(p):
                for l in by_id.get(key, []):
                    found[l.id] = l
            for (y, l) in by_title.get(plex_sync.norm(p.title), []):
                if y is None or y == getattr(p, "year", None):
                    found[l.id] = l
            in_lists = [*found.values()]
            if list == "none" and in_lists:
                continue
            if list.isdigit() and int(list) not in {l.id for l in in_lists}:
                continue
            rows.append({"title": p.title, "year": getattr(p, "year", ""), "lists": in_lists})
        rows.sort(key=lambda r: r["title"].lower())
        return render(request, "library.html", rows=rows, lists=lists, error=None, type=media_type, sel=list, q=q)


# ---- Settings: connect Plex from the browser ---------------------------------

@app.get("/settings")
def settings_page(request: Request):
    plex_sync.poll_login()
    s = settings.load()
    ctx = {
        "s": s,
        "signed_in": bool(s.get("token")),
        "has_server": plex_sync.server_chosen(),
        "pending": plex_sync.login_pending(),
        "servers": [],
        "movie_libs": [],
        "show_libs": [],
        "error": None,
        "keys": {k: bool(s.get(f"{k}_key")) for k in ("tmdb", "omdb", "tvdb")},
    }
    try:
        if ctx["signed_in"] and not ctx["has_server"]:
            ctx["servers"] = plex_sync.servers()
        elif ctx["has_server"]:
            secs = plex_sync.sections()
            ctx["movie_libs"] = [t for t, k in secs if k == "movie"]
            ctx["show_libs"] = [t for t, k in secs if k == "show"]
    except Exception as e:
        ctx["error"] = f"Couldn't reach Plex: {e}"
    return render(request, "settings.html", **ctx)


@app.post("/settings/plex/login")
def plex_login(request: Request):
    url = plex_sync.start_login(str(request.url_for("settings_page")))
    return RedirectResponse(url, status_code=303)


@app.get("/settings/plex/status")
def plex_status():
    plex_sync.poll_login()
    return {"signed_in": plex_sync.signed_in(), "pending": plex_sync.login_pending()}


@app.post("/settings/plex/token")
def plex_manual(url: str = Form(...), token: str = Form(...)):
    settings.update(token=token.strip(), server_url=url.strip().rstrip("/"), server_name="", movie_library="", tv_library="")
    plex_sync.reset_cache()
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/plex/server")
def plex_server(server: str = Form(...)):
    settings.update(server_name=server, server_url="", movie_library="", tv_library="")
    plex_sync.reset_cache()
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/plex/libraries")
def plex_libraries(movie_library: str = Form(""), tv_library: str = Form("")):
    settings.update(movie_library=movie_library, tv_library=tv_library)
    plex_sync.reset_cache()
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/plex/change-server")
def plex_change_server():
    settings.update(server_name="", server_url="", movie_library="", tv_library="")
    plex_sync.reset_cache()
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/plex/signout")
def plex_signout():
    settings.clear_plex()
    plex_sync.reset_cache()
    return RedirectResponse("/settings", status_code=303)


# ---- Title search (IMDb via OMDb, TMDB, TVDB) -------------------------------

_IMDB = re.compile(r"^tt\d{1,10}$")
_NUM = re.compile(r"^\d{1,10}$")


def _id(value, pattern):
    value = str(value or "").strip()
    return value if pattern.match(value) else ""


def _is_duplicate(lst, e):
    new_ids = {(k, e[f"{k}_id"]) for k in ("imdb", "tmdb", "tvdb") if e[f"{k}_id"]}
    for it in lst.items:
        if it.media_type != e["media_type"]:
            continue
        have = {(k, getattr(it, f"{k}_id")) for k in ("imdb", "tmdb", "tvdb") if getattr(it, f"{k}_id")}
        if new_ids & have:
            return True
        if plex_sync.norm(it.title) == plex_sync.norm(e["title"]) and (
            it.year == e["year"] or it.year is None or e["year"] is None
        ):
            return True
    return False


@app.get("/search")
def search_page(
    request: Request,
    q: str = "",
    media: str = Query("both", alias="type"),
    src: list[str] = Query(default=[]),
    list_id: str = Query("", alias="list"),
):
    cfg = settings.load()
    keys = {
        "tmdb": cfg.get("tmdb_key", ""),
        "omdb": cfg.get("omdb_key", ""),
        "tvdb": cfg.get("tvdb_key", ""),
        "tvdb_pin": cfg.get("tvdb_pin", ""),
    }
    available = [x for x in providers.SOURCES if keys[x]]
    chosen = [x for x in available if x in src] or available
    media = media if media in ("movie", "show") else "both"
    q = q.strip()
    results, errors = [], {}
    if q and chosen:
        results, errors = providers.search_all(q, media, chosen, keys)
        for r in results:
            r["payload"] = json.dumps(
                {"t": r["title"], "y": r["year"], "m": r["media_type"],
                 "i": r["imdb_id"], "d": r["tmdb_id"], "v": r["tvdb_id"]},
                separators=(",", ":"),
            )
    with SessionLocal() as s:
        lists = s.query(MediaList).order_by(MediaList.name).all()
        return render(
            request, "search.html", q=q, media=media, results=results, errors=errors, lists=lists,
            list_id=list_id, available=available, chosen=chosen,
            sources=[(k, providers.LABELS[k]) for k in providers.SOURCES],
        )


@app.post("/search/add")
def search_add(list_id: int = Form(...), pick: list[str] = Form(default=[])):
    entries = []
    for raw in pick[:100]:
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        title = str(d.get("t") or "").strip()[:300]
        if not title:
            continue
        year = d.get("y")
        entries.append({
            "title": title,
            "year": year if isinstance(year, int) and 1800 <= year <= 2200 else None,
            "media_type": "show" if d.get("m") == "show" else "movie",
            "imdb_id": _id(d.get("i"), _IMDB),
            "tmdb_id": _id(d.get("d"), _NUM),
            "tvdb_id": _id(d.get("v"), _NUM),
        })
    tmdb_key = settings.load().get("tmdb_key", "")
    if entries and tmdb_key:
        with ThreadPoolExecutor(max_workers=6) as ex:
            entries = [*ex.map(lambda e: providers.enrich_ids(e, tmdb_key), entries)]
    added = skipped = 0
    with SessionLocal() as s:
        lst = s.get(MediaList, list_id)
        if not lst:
            return RedirectResponse("/search", status_code=303)
        for e in entries:
            if _is_duplicate(lst, e):
                skipped += 1
                continue
            lst.items.append(Item(**e))
            added += 1
        s.commit()
    return RedirectResponse(f"/lists/{list_id}?added={added}&skipped={skipped}", status_code=303)


@app.post("/settings/search-keys")
def save_search_keys(
    tmdb_key: str = Form(""), omdb_key: str = Form(""),
    tvdb_key: str = Form(""), tvdb_pin: str = Form(""),
):
    new = {k: v.strip() for k, v in (("tmdb_key", tmdb_key), ("omdb_key", omdb_key),
                                      ("tvdb_key", tvdb_key), ("tvdb_pin", tvdb_pin)) if v.strip()}
    if new:
        settings.update(**new)
    return RedirectResponse("/settings#search", status_code=303)


@app.post("/settings/search-keys/clear")
def clear_search_key(provider: str = Form(...)):
    if provider in ("tmdb", "omdb", "tvdb"):
        vals = {f"{provider}_key": ""}
        if provider == "tvdb":
            vals["tvdb_pin"] = ""
        settings.update(**vals)
    return RedirectResponse("/settings#search", status_code=303)
