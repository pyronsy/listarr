# Listarr

A small self-hosted list manager for Plex. Build lists of movies and TV shows,
sync each list to a Plex collection, and browse your Plex library filtered by
which lists a title belongs to. Single user, no login: run it on your local network only.

## Run with Docker Compose

1. Copy `docker-compose.yml` and replace `YOUR_GITHUB_USERNAME` in the image name.
2. `docker compose up -d`, then open http://localhost:4747
3. Open **Settings**, sign in with Plex, pick your server, then pick your movie and TV libraries.

Update later with `docker compose pull && docker compose up -d`.

## Publish your own image

1. Create a GitHub repo named `listarr` and push this code.
2. The workflow in `.github/workflows/docker.yml` builds and pushes
   `ghcr.io/<you>/listarr` on every push to `main` (tag `latest`) and on `v1.2.3` tags.
3. In GitHub, open Packages, then listarr, then Package settings, and set visibility to
   Public (or run `docker login ghcr.io` on the host if you keep it private).

## Local development

    pip install -r requirements.txt
    DATA_DIR=./data uvicorn app.main:app --reload --port 4747

## Searching for titles

Open **Search**, pick a source or several, find a title, tick the results you want and
choose the list to add them to. Results from different sources that are the same title are
merged into one row. Add your keys under **Settings**:

- **TMDB**: free key from themoviedb.org (a v3 key or a v4 read token both work).
- **IMDb**: IMDb has no free public search API, so Listarr uses OMDb (free key from omdbapi.com).
- **TVDB**: a v4 API key from thetvdb.com, plus a subscriber PIN only if your key requires one.

Titles added from search keep their IMDb, TMDB and TVDB ids.

## How matching works

When syncing, Listarr matches a list item to Plex by external id (IMDb, TMDB or TVDB) when
both sides have one, and otherwise by title and year (ignoring case and punctuation). Id
matching depends on your Plex library exposing its ids; if it doesn't, title and year are used.
Titles pasted in by hand have no ids, so add a year such as `Dune (2021)` for remakes. Titles not
found in Plex are reported after each sync. Sync adds and removes titles in the matching
collection; deleting a list does not delete its Plex collection.

## Settings

Plex sign-in, server, library choices and search API keys are made in the web interface and saved to
`/data/settings.json` (mode 600). Keep the `./data` volume private and out of git.
The Settings page also accepts a server address and token manually if you prefer.

| Variable | Purpose |
|---|---|
| `SYNC_INTERVAL_MINUTES` | Automatic sync interval; `0` disables it |
| `DATA_DIR` | SQLite and settings location (default `/data`) |
