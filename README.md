# Baby Names Swiper

Tiny two-user "Tinder for baby names" web app.
Ramses and Chiara swipe LIKE or NOT on names, and an overview screen surfaces the matches.

## Quick start

```bash
uv sync
uv run task scrape          # one-shot, fetches names from studiopoppy.nl into data/names/
COOKIE_SECRET=dev uv run task dev
open http://localhost:8000
```

## Tasks

```bash
uv run task dev        # uvicorn on :8000 with reload
uv run task scrape     # run the studiopoppy.nl scraper
uv run task format     # ruff format
uv run task check      # ruff check + mypy
uv run task test       # pytest with coverage
```

## Docker

```bash
echo "COOKIE_SECRET=$(openssl rand -hex 32)" > .env
docker compose up --build -d
open http://localhost:8765
```

SQLite lives in the `swipes-db` named volume.
User-uploaded CSVs live in `./data/uploads`.

### Backup

```bash
docker run --rm -v swipes-db:/data -v "$PWD":/backup alpine \
    tar czf /backup/swipes-db-backup.tar.gz /data
```

### Reverse proxy

The compose file publishes port `8765` directly.
If you front it with Traefik, add labels along the lines of:

```yaml
labels:
  - "traefik.enable=true"
  - "traefik.http.routers.babynames.rule=Host(`names.yourdomain`)"
  - "traefik.http.services.babynames.loadbalancer.server.port=8000"
```

## Bring your own list

Upload a plain UTF-8 CSV (one name per line) at `/upload`.
Max 1 MiB, 5000 names, 50 chars per name.

## Layout

```
src/baby_names_swiper/   FastAPI app + templates + static assets
scripts/                 one-shot scraper
data/names/              committed name lists (boys.csv, girls.csv)
data/uploads/            user-uploaded CSVs (gitignored)
tests/                   pytest suite
```
