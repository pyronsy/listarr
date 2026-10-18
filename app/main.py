import asyncio
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from . import plex_sync, settings
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
def show_list(request: Request, list_id: int):
    with SessionLocal() as s:
        lst = s.get(MediaList, list_id)
        if not lst:
            return RedirectResponse("/", status_code=303)
        return render(request, "list.html", lst=lst, items=list(lst.items))


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
        membership: dict = {}
        for lst in lists:
            for it in lst.items:
                if it.media_type == media_type:
                    membership.setdefault(plex_sync.norm(it.title), []).append((it.year, lst))
        try:
            plex_items = plex_sync.library_items(plex_sync.connect(), media_type)
        except Exception as e:
            return render(request, "library.html", rows=[], lists=lists, error=f"Couldn't reach Plex: {e}", type=media_type, sel=list, q=q)
        rows = []
        for p in plex_items:
            if q and q.lower() not in p.title.lower():
                continue
            in_lists = [
                l for (y, l) in membership.get(plex_sync.norm(p.title), [])
                if y is None or y == getattr(p, "year", None)
            ]
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
