# Listarr

A small self-hosted list manager for Plex. Build lists of movies and TV shows,
sync each list to a Plex collection, and browse your Plex library filtered by
which lists a title belongs to. Single user, no login: run it on your local network only.

## Run with Docker Compose

1. Copy `docker-compose.yml`, set `PLEX_URL`, `PLEX_TOKEN` and your library names.
2. Replace `YOUR_GITHUB_USERNAME` in the image name.
3. `docker compose up -d`, then open http://localhost:4747

Update later with `docker compose pull && docker compose up -d`.

## Publish your own image

1. Create a GitHub repo named `listarr` and push this code.
2. The workflow in `.github/workflows/docker.yml` builds and pushes
   `ghcr.io/<you>/listarr` on every push to `main` (tag `latest`) and on `v1.2.3` tags.
3. In GitHub, open Packages, then listarr, then Package settings, and set visibility to
   Public (or run `docker login ghcr.io` on the host if you keep it private).

## Local development

    pip install -r requirements.txt
    DATA_DIR=./data PLEX_URL=... PLEX_TOKEN=... uvicorn app.main:app --reload --port 4747

## How matching works

Titles are matched to Plex by name (ignoring case and punctuation). Add a year,
for example `Dune (2021)`, when a title has remakes. Titles not found in Plex are
reported after each sync. Sync adds and removes titles in the matching collection;
deleting a list does not delete its Plex collection.

## Settings

| Variable | Purpose |
|---|---|
| `PLEX_URL`, `PLEX_TOKEN` | Plex server address and token |
| `PLEX_MOVIE_LIBRARY`, `PLEX_TV_LIBRARY` | Exact library names in Plex |
| `SYNC_INTERVAL_MINUTES` | Automatic sync interval; `0` disables it |
| `DATA_DIR` | SQLite location (default `/data`) |
