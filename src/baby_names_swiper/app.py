"""FastAPI app: routes for swipe / overview / upload / user-picker."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Annotated
from urllib.parse import urlencode, urlparse

from fastapi import Cookie, FastAPI, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from baby_names_swiper.config import COOKIE_NAME, MAX_UPLOAD_BYTES, USERS
from baby_names_swiper.db import init_db
from baby_names_swiper.deps import read_user, sign_user
from baby_names_swiper.lists_view import NameRow, build_rows, normalise_states, normalise_view
from baby_names_swiper.names import (
    add_manual_name,
    list_available_lists,
    load_manual_names,
    load_names,
    remove_manual_name,
    save_upload,
)
from baby_names_swiper.swipes import (
    ALL_STATES,
    DISLIKE,
    LIKE,
    MODE_RANDOM,
    VALID_MODES,
    absorb_added_name,
    get_deck,
    invalidate_decks,
    invalidate_list_decks,
    is_match,
    overview,
    record,
    remove_swipe,
    reset_list,
    undo_last,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_PKG_DIR = Path(__file__).resolve().parent
_STATIC_DIR = _PKG_DIR / "static"
_TEMPLATES_DIR = _PKG_DIR / "templates"

WhoCookie = Annotated[str | None, Cookie(alias=COOKIE_NAME)]


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


app = FastAPI(title="Baby Names Swiper", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)


def _user_or_redirect(who: str | None) -> str | RedirectResponse:
    user = read_user(who)
    if user is None:
        return RedirectResponse(url="/who", status_code=303)
    return user


def _resolve_list(slug: str | None) -> str:
    """Pick the requested list or fall back to the first available."""
    available = list_available_lists()
    if not available:
        raise HTTPException(
            status_code=503,
            detail="No name lists found. Run `uv run task scrape` to generate them.",
        )
    if slug:
        for nl in available:
            if nl.slug == slug:
                return slug
    return available[0].slug


def _resolve_mode(mode: str | None) -> str:
    if mode in VALID_MODES:
        return mode
    return MODE_RANDOM


@app.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"ok": True})


@app.get("/", include_in_schema=False)
def root(who: WhoCookie = None) -> RedirectResponse:
    if read_user(who) is None:
        return RedirectResponse(url="/who", status_code=303)
    return RedirectResponse(url="/swipe", status_code=303)


@app.get("/who", response_class=HTMLResponse)
def pick_user_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "pick_user.html",
        {"users": USERS},
    )


@app.post("/who")
def set_user(user: Annotated[str, Form()]) -> RedirectResponse:
    if user not in USERS:
        raise HTTPException(status_code=400, detail="Unknown user")
    response = RedirectResponse(url="/swipe", status_code=303)
    response.set_cookie(
        key=COOKIE_NAME,
        value=sign_user(user),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 365,
    )
    return response


@app.get("/logout")
def logout() -> RedirectResponse:
    response = RedirectResponse(url="/who", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


def _deck_context(
    *,
    user: str,
    list_slug: str,
    active_mode: str,
    reswipe_flag: bool,
    current: str | None,
    lookahead: str | None,
) -> dict[str, object]:
    return {
        "user": user,
        "active_list": list_slug,
        "active_mode": active_mode,
        "reswipe": reswipe_flag,
        "current_name": current,
        "next_name": lookahead,
    }


@app.get("/swipe", response_class=HTMLResponse)
def swipe_page(
    request: Request,
    list: str | None = None,  # noqa: A002
    mode: str | None = None,
    reswipe: int = 0,
    who: WhoCookie = None,
) -> Response:
    user_or_redirect = _user_or_redirect(who)
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    user = user_or_redirect
    list_slug = _resolve_list(list)
    active_mode = _resolve_mode(mode)
    reswipe_flag = bool(reswipe)
    deck = get_deck(user, list_slug, mode=active_mode, reswipe_disliked=reswipe_flag)
    ctx: dict[str, object] = {
        "lists": list_available_lists(),
        **_deck_context(
            user=user,
            list_slug=list_slug,
            active_mode=active_mode,
            reswipe_flag=reswipe_flag,
            current=deck.current(),
            lookahead=deck.lookahead(),
        ),
    }
    return templates.TemplateResponse(request, "swipe.html", ctx)


@app.post("/swipe", response_class=HTMLResponse)
def post_swipe(
    request: Request,
    name: Annotated[str, Form()],
    direction: Annotated[int, Form()],
    list: Annotated[str, Form(alias="list")],  # noqa: A002
    mode: Annotated[str | None, Form()] = None,
    reswipe: Annotated[int, Form()] = 0,
    who: WhoCookie = None,
) -> HTMLResponse:
    """Record a swipe and return the next lookahead card.

    The deck has a fixed order. Recording the swipe and re-fetching the deck
    makes get_deck's reconcile step move the cursor past the just-swiped name,
    so deck.current() is what the client just promoted and deck.lookahead()
    is the fresh card to send back.
    """
    user_or_redirect = _user_or_redirect(who)
    if isinstance(user_or_redirect, RedirectResponse):
        raise HTTPException(status_code=401, detail="No user")
    user = user_or_redirect
    if direction not in (LIKE, DISLIKE):
        raise HTTPException(status_code=400, detail="bad direction")
    list_slug = _resolve_list(list)
    active_mode = _resolve_mode(mode)
    reswipe_flag = bool(reswipe)

    swiped_name = name.strip()
    record(user, list_slug, swiped_name, direction)
    # a new match exists only when this swipe was a like and the partner had
    # already liked the same name
    new_match = direction == LIKE and is_match(user, list_slug, swiped_name)
    deck = get_deck(user, list_slug, mode=active_mode, reswipe_disliked=reswipe_flag)

    lookahead = deck.lookahead()
    template = "_card_next.html" if lookahead else "_card_next_empty.html"
    ctx = _deck_context(
        user=user,
        list_slug=list_slug,
        active_mode=active_mode,
        reswipe_flag=reswipe_flag,
        current=deck.current(),
        lookahead=lookahead,
    )
    ctx["match_name"] = swiped_name if new_match else None
    return templates.TemplateResponse(request, template, ctx)


@app.post("/swipe/undo", response_class=HTMLResponse)
def post_undo(
    request: Request,
    list: Annotated[str, Form(alias="list")],  # noqa: A002
    mode: Annotated[str | None, Form()] = None,
    reswipe: Annotated[int, Form()] = 0,
    who: WhoCookie = None,
) -> HTMLResponse:
    user_or_redirect = _user_or_redirect(who)
    if isinstance(user_or_redirect, RedirectResponse):
        raise HTTPException(status_code=401, detail="No user")
    user = user_or_redirect
    list_slug = _resolve_list(list)
    active_mode = _resolve_mode(mode)
    reswipe_flag = bool(reswipe)

    restored = undo_last(user, list_slug)
    deck = get_deck(user, list_slug, mode=active_mode, reswipe_disliked=reswipe_flag)
    if restored:
        deck.rewind()
    return templates.TemplateResponse(
        request,
        "_deck.html",
        _deck_context(
            user=user,
            list_slug=list_slug,
            active_mode=active_mode,
            reswipe_flag=reswipe_flag,
            current=deck.current(),
            lookahead=deck.lookahead(),
        ),
    )


def _overview_context(user: str, list_slug: str) -> dict[str, object]:
    return {
        "user": user,
        "active_list": list_slug,
        "ov": overview(user, list_slug),
        # names manually added to this list, so the overview can show the
        # extra "delete from list" action on those rows
        "manual_names": set(load_manual_names(list_slug)),
    }


@app.get("/overview", response_class=HTMLResponse)
def overview_page(
    request: Request,
    list: str | None = None,  # noqa: A002
    who: WhoCookie = None,
) -> Response:
    user_or_redirect = _user_or_redirect(who)
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    user = user_or_redirect
    list_slug = _resolve_list(list)
    return templates.TemplateResponse(
        request,
        "overview.html",
        {
            "lists": list_available_lists(),
            **_overview_context(user, list_slug),
        },
    )


@app.post("/overview/remove", response_class=HTMLResponse)
def overview_remove(
    request: Request,
    name: Annotated[str, Form()],
    list: Annotated[str, Form(alias="list")],  # noqa: A002
    who: WhoCookie = None,
) -> HTMLResponse:
    """Remove one of the current user's own swipes, return the refreshed body."""
    user_or_redirect = _user_or_redirect(who)
    if isinstance(user_or_redirect, RedirectResponse):
        raise HTTPException(status_code=401, detail="No user")
    user = user_or_redirect
    list_slug = _resolve_list(list)
    # the DELETE is scoped to `user` (the cookie identity), so a user can
    # only ever remove their own swipes
    remove_swipe(user, list_slug, name.strip())
    return templates.TemplateResponse(
        request,
        "_overview_body.html",
        _overview_context(user, list_slug),
    )


@app.post("/overview/reset", response_class=HTMLResponse)
def overview_reset(
    request: Request,
    list: Annotated[str, Form(alias="list")],  # noqa: A002
    who: WhoCookie = None,
) -> HTMLResponse:
    """Wipe all of the current user's swipes for a list, return refreshed body."""
    user_or_redirect = _user_or_redirect(who)
    if isinstance(user_or_redirect, RedirectResponse):
        raise HTTPException(status_code=401, detail="No user")
    user = user_or_redirect
    list_slug = _resolve_list(list)
    reset_list(user, list_slug)
    return templates.TemplateResponse(
        request,
        "_overview_body.html",
        _overview_context(user, list_slug),
    )


@app.post("/overview/delete-from-list", response_class=HTMLResponse)
def overview_delete_from_list(
    request: Request,
    name: Annotated[str, Form()],
    list: Annotated[str, Form(alias="list")],  # noqa: A002
    who: WhoCookie = None,
) -> HTMLResponse:
    """Delete a manually-added name: drop it from the list AND remove the swipe.

    Only manual additions can be deleted this way; the request is ignored for
    names that come from the base CSV.
    """
    user_or_redirect = _user_or_redirect(who)
    if isinstance(user_or_redirect, RedirectResponse):
        raise HTTPException(status_code=401, detail="No user")
    user = user_or_redirect
    list_slug = _resolve_list(list)
    clean = name.strip()
    if remove_manual_name(list_slug, clean):
        # drop the swipe for every user, since the name no longer exists
        for u in USERS:
            remove_swipe(u, list_slug, clean)
        invalidate_list_decks(list_slug)
    return templates.TemplateResponse(
        request,
        "_overview_body.html",
        _overview_context(user, list_slug),
    )


# --------------------------------------------------------------------------- #
#                               name-lists page                               #
# --------------------------------------------------------------------------- #

LISTS_PAGE_SIZE = 50


def _resolve_list_slugs(values: list[str] | None) -> list[str]:
    """Filter the requested slugs down to ones that actually exist.

    The lists page is fine with an empty selection (renders an empty body), so
    unlike /swipe it does NOT fall back to the first available list.
    """
    if not values:
        return []
    available = {nl.slug for nl in list_available_lists()}
    return [s for s in values if s in available]


def _lists_context(
    *,
    user: str,
    list_slugs: list[str],
    mode: str,
    view: str,
    states: list[str],
    offset: int,
    rows_all: list[NameRow],
) -> dict[str, object]:
    end = offset + LISTS_PAGE_SIZE
    visible = rows_all[offset:end]
    has_more = end < len(rows_all)
    next_offset = end if has_more else None
    # `add_list_slug` is the slug the in-page add-name form should target.
    # Auto-selects when exactly one list is checked, else None (form disabled).
    add_list_slug = list_slugs[0] if len(list_slugs) == 1 else None
    # URL the infinite-scroll trigger calls when the last row of this batch
    # becomes visible. Pre-built here so the template stays simple.
    next_rows_url: str | None = None
    if next_offset is not None:
        params: list[tuple[str, str]] = [("list", s) for s in list_slugs]
        params.append(("mode", mode))
        params.append(("view", view))
        params.extend(("state", st) for st in states)
        params.append(("offset", str(next_offset)))
        next_rows_url = "/lists/rows?" + urlencode(params)
    return {
        "user": user,
        "lists": list_available_lists(),
        "selected_slugs": list_slugs,
        "selected_set": set(list_slugs),
        "active_mode": mode,
        "active_view": view,
        "active_states": states,
        "active_states_set": set(states),
        "rows": visible,
        "rows_total": len(rows_all),
        "has_more": has_more,
        "next_offset": next_offset,
        "next_rows_url": next_rows_url,
        "add_list_slug": add_list_slug,
        # Drives the header's "Add single name" form: it only renders when a
        # single list is the unambiguous target.
        "active_list": add_list_slug,
    }


@app.get("/lists", response_class=HTMLResponse)
def lists_page(
    request: Request,
    list: Annotated[list[str] | None, Query()] = None,  # noqa: A002
    mode: str | None = None,
    view: str | None = None,
    state: Annotated[list[str] | None, Query()] = None,
    who: WhoCookie = None,
) -> Response:
    user_or_redirect = _user_or_redirect(who)
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    user = user_or_redirect
    list_slugs = _resolve_list_slugs(list)
    active_mode = _resolve_mode(mode)
    active_view = normalise_view(view)
    states = normalise_states(state)
    rows_all = build_rows(
        user=user,
        list_slugs=list_slugs,
        mode=active_mode,
        states=states,
    )
    ctx = _lists_context(
        user=user,
        list_slugs=list_slugs,
        mode=active_mode,
        view=active_view,
        states=states,
        offset=0,
        rows_all=rows_all,
    )
    return templates.TemplateResponse(request, "lists.html", ctx)


@app.get("/lists/rows", response_class=HTMLResponse)
def lists_rows(
    request: Request,
    list: Annotated[list[str] | None, Query()] = None,  # noqa: A002
    mode: str | None = None,
    view: str | None = None,
    state: Annotated[list[str] | None, Query()] = None,
    offset: int = 0,
    who: WhoCookie = None,
) -> Response:
    """Return one page of rows for infinite scroll (HTMX revealed-trigger)."""
    user_or_redirect = _user_or_redirect(who)
    if isinstance(user_or_redirect, RedirectResponse):
        raise HTTPException(status_code=401, detail="No user")
    user = user_or_redirect
    list_slugs = _resolve_list_slugs(list)
    active_mode = _resolve_mode(mode)
    active_view = normalise_view(view)
    states = normalise_states(state)
    rows_all = build_rows(
        user=user,
        list_slugs=list_slugs,
        mode=active_mode,
        states=states,
    )
    ctx = _lists_context(
        user=user,
        list_slugs=list_slugs,
        mode=active_mode,
        view=active_view,
        states=states,
        offset=max(0, offset),
        rows_all=rows_all,
    )
    return templates.TemplateResponse(request, "_lists_rows.html", ctx)


def _resolve_row_list(slug: str) -> str:
    """Validate a per-row list slug. Unlike _resolve_list, no fallback."""
    available = {nl.slug for nl in list_available_lists()}
    if slug not in available:
        raise HTTPException(status_code=400, detail="Unknown list")
    return slug


def _render_row(
    request: Request,
    *,
    user: str,
    list_slug: str,
    name: str,
) -> HTMLResponse:
    """Render the single-row partial after a per-row mutation."""
    rows = build_rows(
        user=user,
        list_slugs=[list_slug],
        mode=MODE_RANDOM,  # mode doesn't matter for a single-row lookup
        states=list(ALL_STATES),
    )
    row = next((r for r in rows if r.name == name), None)
    return templates.TemplateResponse(
        request,
        "_lists_row.html",
        {"row": row, "list_slug": list_slug, "name": name},
    )


@app.post("/lists/swipe", response_class=HTMLResponse)
def lists_swipe(
    request: Request,
    name: Annotated[str, Form()],
    list: Annotated[str, Form(alias="list")],  # noqa: A002
    direction: Annotated[int, Form()],
    who: WhoCookie = None,
) -> HTMLResponse:
    user_or_redirect = _user_or_redirect(who)
    if isinstance(user_or_redirect, RedirectResponse):
        raise HTTPException(status_code=401, detail="No user")
    user = user_or_redirect
    list_slug = _resolve_row_list(list)
    if direction not in (LIKE, DISLIKE):
        raise HTTPException(status_code=400, detail="bad direction")
    clean = name.strip()
    record(user, list_slug, clean, direction)
    invalidate_decks(user, list_slug)
    return _render_row(request, user=user, list_slug=list_slug, name=clean)


@app.post("/lists/unswipe", response_class=HTMLResponse)
def lists_unswipe(
    request: Request,
    name: Annotated[str, Form()],
    list: Annotated[str, Form(alias="list")],  # noqa: A002
    who: WhoCookie = None,
) -> HTMLResponse:
    user_or_redirect = _user_or_redirect(who)
    if isinstance(user_or_redirect, RedirectResponse):
        raise HTTPException(status_code=401, detail="No user")
    user = user_or_redirect
    list_slug = _resolve_row_list(list)
    clean = name.strip()
    remove_swipe(user, list_slug, clean)
    return _render_row(request, user=user, list_slug=list_slug, name=clean)


@app.post("/lists/delete", response_class=HTMLResponse)
def lists_delete(
    name: Annotated[str, Form()],
    list: Annotated[str, Form(alias="list")],  # noqa: A002
    who: WhoCookie = None,
) -> HTMLResponse:
    """Delete a manually-added name from a list (per-row delete button)."""
    user_or_redirect = _user_or_redirect(who)
    if isinstance(user_or_redirect, RedirectResponse):
        raise HTTPException(status_code=401, detail="No user")
    list_slug = _resolve_row_list(list)
    clean = name.strip()
    if remove_manual_name(list_slug, clean):
        for u in USERS:
            remove_swipe(u, list_slug, clean)
        invalidate_list_decks(list_slug)
    # HTMX swaps the row's outerHTML with this empty body -> row disappears.
    return HTMLResponse("")


@app.post("/add-name")
def add_name(
    request: Request,
    name: Annotated[str, Form()],
    list: Annotated[str, Form(alias="list")],  # noqa: A002
    mode: Annotated[str | None, Form()] = None,
    reswipe: Annotated[int, Form()] = 0,
    who: WhoCookie = None,
) -> RedirectResponse:
    """Add a single name to a list's manual CSV and auto-like it for this user."""
    user_or_redirect = _user_or_redirect(who)
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    user = user_or_redirect
    list_slug = _resolve_list(list)
    active_mode = _resolve_mode(mode)
    reswipe_flag = bool(reswipe)
    # If the name already exists in the list, don't add it again -- but still
    # record a LIKE for the current user, so the action is never a no-op from
    # their perspective. Match case-insensitively and like the stored casing
    # so it ties to the canonical entry in the deck.
    cleaned = name.strip()
    target_key = cleaned.casefold()
    existing = next(
        (n for n in load_names(list_slug) if n.casefold() == target_key),
        None,
    )
    if existing is not None:
        record(user, list_slug, existing, LIKE)
        # Name already in the pool: only this user's swipe changed, so the
        # other user's decks don't need to know -- a normal per-user
        # invalidation is enough.
        invalidate_decks(user, list_slug)
    else:
        try:
            added = add_manual_name(list_slug, name)
        except ValueError:
            # invalid (empty / too long) -- bounce back without recording
            added = None
        if added is not None:
            record(user, list_slug, added, LIKE)
            # The list's pool changed: alpha/partner_likes decks rebuild so
            # the new name lands in its natural slot; random decks get the
            # name appended to the end, preserving the existing order so the
            # user doesn't lose their scroll position on /lists or their
            # place in the swipe deck.
            absorb_added_name(list_slug, added)
    # Send the user back to whichever page they submitted from, so adding
    # a name from /overview doesn't kick them to /swipe. We only honour the
    # Referer when it's same-origin (path-only), falling back to /swipe.
    target = _same_origin_path(request, request.headers.get("referer"))
    if target is None:
        target = f"/swipe?list={list_slug}&mode={active_mode}"
        if reswipe_flag:
            target += "&reswipe=1"
    # Flag the redirect so the header can re-open the "Add name(s)" panel,
    # letting the user queue several names in a row.
    sep = "&" if "?" in target else "?"
    target += f"{sep}added=1"
    return RedirectResponse(url=target, status_code=303)


def _same_origin_path(request: Request, referer: str | None) -> str | None:
    """Return the path+query of `referer` if it points at this app, else None."""
    if not referer:
        return None
    parsed = urlparse(referer)
    # Reject anything that targets a different host.
    if parsed.netloc and parsed.netloc != request.url.netloc:
        return None
    path = parsed.path or "/"
    # Don't loop back into /add-name itself.
    if path.startswith("/add-name"):
        return None
    # Strip any pre-existing added=1 flag from the query so we don't double it.
    query = parsed.query
    if query:
        parts = [p for p in query.split("&") if p and p != "added=1"]
        query = "&".join(parts)
    return f"{path}?{query}" if query else path


@app.get("/upload", response_class=HTMLResponse)
def upload_page(
    request: Request,
    who: WhoCookie = None,
) -> Response:
    user_or_redirect = _user_or_redirect(who)
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    return templates.TemplateResponse(
        request,
        "upload.html",
        {"user": user_or_redirect, "error": None},
    )


@app.post("/upload", response_class=HTMLResponse)
async def post_upload(
    request: Request,
    file: UploadFile,
    who: WhoCookie = None,
) -> Response:
    user_or_redirect = _user_or_redirect(who)
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect

    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        return templates.TemplateResponse(
            request,
            "upload.html",
            {"user": user_or_redirect, "error": "File too large (max 1 MiB)."},
            status_code=400,
        )
    try:
        nl = save_upload(file.filename or "list.csv", raw)
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "upload.html",
            {"user": user_or_redirect, "error": str(exc)},
            status_code=400,
        )
    return RedirectResponse(url=f"/swipe?list={nl.slug}", status_code=303)
