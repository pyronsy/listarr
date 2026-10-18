# Listarr

A small self-hosted list manager for Plex. Build lists of movies and TV shows,
sync each list to a Plex collection, and browse your Plex library filtered by
which lists a title belongs to. Single user, no login: run it on your local network only.

## How matching works

Titles are matched to Plex by name (ignoring case and punctuation). Add a year,
for example `Dune (2021)`, when a title has remakes. Titles not found in Plex are
reported after each sync. Sync adds and removes titles in the matching collection;
deleting a list does not delete its Plex collection.

## Settings

Plex sign-in, server and library choices are made in the web interface and saved to
`/data/settings.json` (mode 600). Keep the `./data` volume private and out of git.
The Settings page also accepts a server address and token manually if you prefer.

| Variable | Purpose |
|---|---|
| `SYNC_INTERVAL_MINUTES` | Automatic sync interval; `0` disables it |
| `DATA_DIR` | SQLite and settings location (default `/data`) |
