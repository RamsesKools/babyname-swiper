# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

All tasks go through `taskipy` via `uv`:

```bash
uv run task dev     # uvicorn baby_names_swiper.app:app --reload on :8000
uv run task scrape  # scripts/scrape_studiopoppy.py -> data/names/{boys,girls}.csv
uv run task format  # ruff format
uv run task check   # mypy (strict) + ruff check
uv run task test    # pytest with coverage (fail_under=60)
uv run task all     # format + check + test + clean
```

Run a single test: `uv run pytest tests/test_swipes.py::test_name -x`.
Run without slow tests: `uv run pytest -m "not slow"`.

`COOKIE_SECRET` defaults to a dev value, so `task dev` works without `.env`. In prod it must be set (Docker compose enforces it).

## Architecture

Two-user FastAPI app (`Ramses` + `Chiara`, hardcoded in [config.py](src/baby_names_swiper/config.py)). User identity lives in a signed cookie (`itsdangerous`, see [deps.py](src/baby_names_swiper/deps.py)); there is no auth beyond "pick a user on `/who`".

Three persistence surfaces, all routed through [config.py](src/baby_names_swiper/config.py) env vars so tests can swap them:

- **SQLite** at `DB_PATH` (`data/swipes.db`): the single `swipes` table, one row per (user, list_slug, name). See [db.py](src/baby_names_swiper/db.py) — one global connection, WAL mode, a module-level lock serialises writes.
- **`NAMES_DIR`** (`data/names/`): committed base CSVs (`boys.csv`, `girls.csv`) — one name per line.
- **`UPLOAD_DIR`** (`data/uploads/`): user-uploaded CSVs, slug becomes `upload_<stem>`.
- **`MANUAL_DIR`** (`data/manual/`): per-list `manual_<slug>.csv` for names added via the in-app "add name" button. `load_names(slug)` merges base + manual.

### The deck (in-memory, important)

[swipes.py](src/baby_names_swiper/swipes.py) holds `_decks: dict[DeckKey, Deck]` where `DeckKey = (user, list_slug, mode, reswipe)`. A deck is a frozen-order list of names plus a cursor. Modes:

- `random` — weighted shuffle (Efraimidis-Spirakis) seeded deterministically from the key. Partner-likes get 5x weight, partner-dislikes 0.2x.
- `alpha` — case-folded sort.
- `partner_likes` — only names the partner already liked, alpha-sorted.

Deck order is fixed for the lifetime of the process. The cursor is **reconciled** on every `get_deck` call: it skips forward past any name that's already been swiped (handles process restart and cross-deck swipes). On data mutations that change the *pool*, the deck must be invalidated:

- `remove_swipe` / `reset_list` → `invalidate_decks(user, list_slug)` (frees names back into that user's decks).
- Manual name added or removed → `invalidate_list_decks(list_slug)` (affects both users).

`POST /swipe` records the swipe, then re-fetches the deck — the reconcile step advances the cursor, so `deck.current()` is the card the client just promoted from lookahead and `deck.lookahead()` is the fresh card to return. The view is HTMX-driven and returns `_card_next.html` / `_card_next_empty.html` partials, not full pages.

### Match detection

`is_match(user, list, name)` checks both users have a `LIKE` row. `POST /swipe` only flags `new_match` when the *current* swipe is the one that completes the pair (direction == LIKE and partner already liked it). The template then renders the match modal via `_match_preview.html`.

## Conventions

- Python 3.13, strict mypy. Tests have relaxed mypy (`disallow_incomplete_defs = false`).
- `from __future__ import annotations` everywhere.
- Ruff config in [ruff.toml](ruff.toml) — keep imports sorted, follow existing style.
- Tests use the autouse `_isolate_paths` fixture in [tests/conftest.py](tests/conftest.py): every test gets a tmp `NAMES_DIR`/`UPLOAD_DIR`/`MANUAL_DIR` and a fresh SQLite. If you add new module-level path bindings, patch them there too.
- Templates live in [src/baby_names_swiper/templates/](src/baby_names_swiper/templates/); HTMX partials are the `_*.html` files. The main pages are `swipe.html`, `overview.html`, `pick_user.html`, `upload.html`.
