"""FastAPI app: routes for swipe / overview / upload / user-picker."""

from __future__ import annotations

from contextlib import asynccontextmanager
import json
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
from baby_names_swiper.lists_view import NameRow, build_rows, normalise_view
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
    ORDER_RANDOM,
    STATE_UNSWIPED,
    VALID_ORDERS,
    absorb_added_name,
    get_deck,
    invalidate_decks,
    invalidate_list_decks,
    is_match,
    new_shuffle_token,
    overview,
    record,
    remove_swipe,
    reset_list,
    undo_last,
    undo_last_across,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_PKG_DIR = Path(__file__).resolve().parent
_STATIC_DIR = _PKG_DIR / "static"
_TEMPLATES_DIR = _PKG_DIR / "templates"

WhoCookie = Annotated[str | None, Cookie(alias=COOKIE_NAME)]

# Session cookies shared by /swipe and /lists. SameSite=Lax, no max-age so they
# drop when the browser closes.
BNS_SHUFFLE = "bns_shuffle"
BNS_LISTS = "bns_view_lists"
BNS_STATES = "bns_view_states"


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


def _resolve_order(order: str | None) -> str:
    if order in VALID_ORDERS:
        return order
    return ORDER_RANDOM


# --------------------------------------------------------------------------- #
#                              cookie / param resolution                      #
# --------------------------------------------------------------------------- #


def _split_cookie_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [s for s in value.split(",") if s]


def _resolve_shuffle(
    query_shuffle: str | None,
    cookie_shuffle: str | None,
    *,
    order: str,
) -> str:
    """Resolve the active shuffle token.

    URL override > cookie > freshly minted. Returns a non-empty token; for
    non-random orders the value is still tracked so cookies stay consistent
    when the user toggles back to random.
    """
    if query_shuffle:
        return query_shuffle[:32]
    if cookie_shuffle:
        return cookie_shuffle[:32]
    if order == ORDER_RANDOM:
        return new_shuffle_token()
    # Non-random first visit: store a token anyway so a future random selection
    # gets a stable seed.
    return new_shuffle_token()


def _resolve_list_slugs(
    query_lists: list[str] | None,
    cookie_lists: str | None,
) -> list[str]:
    """URL > cookie > []. Filters to slugs that currently exist."""
    available = {nl.slug for nl in list_available_lists()}
    if query_lists is not None:
        # Empty list in URL is explicit "deselect all" -> empty.
        return [s for s in query_lists if s in available]
    cookie_slugs = _split_cookie_csv(cookie_lists)
    return [s for s in cookie_slugs if s in available]


def _resolve_state_filters(
    query_states: list[str] | None,
    cookie_states: str | None,
    *,
    first_visit_default: frozenset[str] = frozenset({STATE_UNSWIPED}),
) -> list[str]:
    """URL > cookie > first-visit default. Empty selection is honored."""
    if query_states is not None:
        return [s for s in query_states if s in ALL_STATES]
    if cookie_states is not None:
        return [s for s in _split_cookie_csv(cookie_states) if s in ALL_STATES]
    return sorted(first_visit_default)


def _set_view_cookies(
    response: Response,
    *,
    shuffle: str,
    list_slugs: list[str],
    state_filters: list[str],
) -> None:
    """Write all three view cookies on `response`. Session-lifetime, Lax."""
    response.set_cookie(BNS_SHUFFLE, shuffle, httponly=True, samesite="lax", path="/")
    response.set_cookie(BNS_LISTS, ",".join(list_slugs), httponly=True, samesite="lax", path="/")
    response.set_cookie(
        BNS_STATES, ",".join(state_filters), httponly=True, samesite="lax", path="/"
    )


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


# --------------------------------------------------------------------------- #
#                                  swipe page                                 #
# --------------------------------------------------------------------------- #


def _deck_context(
    *,
    user: str,
    list_slugs: list[str],
    active_order: str,
    state_filters: list[str],
    current: str | None,
    current_source: str | None,
    lookahead: str | None,
    lookahead_source: str | None,
    shuffle: str,
) -> dict[str, object]:
    # `active_list` is the single-selected list when there's exactly one (used
    # by the header's "add single name" form). Otherwise None.
    active_list = list_slugs[0] if len(list_slugs) == 1 else None
    return {
        "user": user,
        "selected_slugs": list_slugs,
        "selected_set": set(list_slugs),
        "active_list": active_list,
        "active_order": active_order,
        "active_states": state_filters,
        "active_states_set": set(state_filters),
        "current_name": current,
        "current_source": current_source,
        "next_name": lookahead,
        "next_source": lookahead_source,
        "active_shuffle": shuffle,
    }


@app.get("/swipe", response_class=HTMLResponse)
def swipe_page(
    request: Request,
    list: Annotated[list[str] | None, Query()] = None,  # noqa: A002
    order: str | None = None,
    state: Annotated[list[str] | None, Query()] = None,
    shuffle: str | None = None,
    who: WhoCookie = None,
    bns_shuffle: Annotated[str | None, Cookie(alias=BNS_SHUFFLE)] = None,
    bns_view_lists: Annotated[str | None, Cookie(alias=BNS_LISTS)] = None,
    bns_view_states: Annotated[str | None, Cookie(alias=BNS_STATES)] = None,
) -> Response:
    user_or_redirect = _user_or_redirect(who)
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    user = user_or_redirect
    active_order = _resolve_order(order)
    list_slugs = _resolve_list_slugs(list, bns_view_lists)
    state_filters = _resolve_state_filters(state, bns_view_states)
    active_shuffle = _resolve_shuffle(shuffle, bns_shuffle, order=active_order)

    deck = get_deck(
        user,
        list_slugs,
        order=active_order,
        state_filters=frozenset(state_filters),
        shuffle=active_shuffle,
    )
    current = deck.current()
    lookahead = deck.lookahead()
    ctx: dict[str, object] = {
        "lists": list_available_lists(),
        **_deck_context(
            user=user,
            list_slugs=list_slugs,
            active_order=active_order,
            state_filters=state_filters,
            current=current,
            current_source=deck.source_of(current) if current else None,
            lookahead=lookahead,
            lookahead_source=deck.source_of(lookahead) if lookahead else None,
            shuffle=active_shuffle,
        ),
    }
    response = templates.TemplateResponse(request, "swipe.html", ctx)
    _set_view_cookies(
        response,
        shuffle=active_shuffle,
        list_slugs=list_slugs,
        state_filters=state_filters,
    )
    return response


@app.post("/swipe", response_class=HTMLResponse)
def post_swipe(
    request: Request,
    name: Annotated[str, Form()],
    direction: Annotated[int, Form()],
    list: Annotated[str, Form(alias="list")],  # noqa: A002
    order: Annotated[str | None, Form()] = None,
    state: Annotated[list[str] | None, Form()] = None,
    shuffle: Annotated[str | None, Form()] = None,
    who: WhoCookie = None,
    bns_shuffle: Annotated[str | None, Cookie(alias=BNS_SHUFFLE)] = None,
    bns_view_lists: Annotated[str | None, Cookie(alias=BNS_LISTS)] = None,
    bns_view_states: Annotated[str | None, Cookie(alias=BNS_STATES)] = None,
) -> HTMLResponse:
    """Record a swipe and return the next lookahead card.

    `list` here is the source list for the swiped card (from data-source-list
    on the active card), not the user's whole selection. The deck rebuild
    uses the full session selection so the next lookahead is correct.
    """
    user_or_redirect = _user_or_redirect(who)
    if isinstance(user_or_redirect, RedirectResponse):
        raise HTTPException(status_code=401, detail="No user")
    user = user_or_redirect
    if direction not in (LIKE, DISLIKE):
        raise HTTPException(status_code=400, detail="bad direction")
    available = {nl.slug for nl in list_available_lists()}
    if list not in available:
        raise HTTPException(status_code=400, detail="Unknown list")
    active_order = _resolve_order(order)
    list_slugs = _resolve_list_slugs(None, bns_view_lists)
    state_filters = _resolve_state_filters(state, bns_view_states)
    active_shuffle = _resolve_shuffle(shuffle, bns_shuffle, order=active_order)

    swiped_name = name.strip()
    record(user, list, swiped_name, direction)
    new_match = direction == LIKE and is_match(user, list, swiped_name)

    deck = get_deck(
        user,
        list_slugs,
        order=active_order,
        state_filters=frozenset(state_filters),
        shuffle=active_shuffle,
    )

    current = deck.current()
    lookahead = deck.lookahead()
    template = "_card_next.html" if lookahead else "_card_next_empty.html"
    ctx = _deck_context(
        user=user,
        list_slugs=list_slugs,
        active_order=active_order,
        state_filters=state_filters,
        current=current,
        current_source=deck.source_of(current) if current else None,
        lookahead=lookahead,
        lookahead_source=deck.source_of(lookahead) if lookahead else None,
        shuffle=active_shuffle,
    )
    ctx["match_name"] = swiped_name if new_match else None
    return templates.TemplateResponse(request, template, ctx)


@app.post("/swipe/undo", response_class=HTMLResponse)
def post_undo(
    request: Request,
    order: Annotated[str | None, Form()] = None,
    state: Annotated[list[str] | None, Form()] = None,
    shuffle: Annotated[str | None, Form()] = None,
    who: WhoCookie = None,
    bns_shuffle: Annotated[str | None, Cookie(alias=BNS_SHUFFLE)] = None,
    bns_view_lists: Annotated[str | None, Cookie(alias=BNS_LISTS)] = None,
    bns_view_states: Annotated[str | None, Cookie(alias=BNS_STATES)] = None,
) -> HTMLResponse:
    user_or_redirect = _user_or_redirect(who)
    if isinstance(user_or_redirect, RedirectResponse):
        raise HTTPException(status_code=401, detail="No user")
    user = user_or_redirect
    active_order = _resolve_order(order)
    list_slugs = _resolve_list_slugs(None, bns_view_lists)
    state_filters = _resolve_state_filters(state, bns_view_states)
    active_shuffle = _resolve_shuffle(shuffle, bns_shuffle, order=active_order)

    restored = undo_last_across(user, list_slugs)
    if restored is not None:
        # invalidate so the unbanned name re-enters the deck pool
        invalidate_decks(user, restored[1])
    deck = get_deck(
        user,
        list_slugs,
        order=active_order,
        state_filters=frozenset(state_filters),
        shuffle=active_shuffle,
    )
    if restored is not None:
        deck.rewind()
    current = deck.current()
    lookahead = deck.lookahead()
    return templates.TemplateResponse(
        request,
        "_deck.html",
        _deck_context(
            user=user,
            list_slugs=list_slugs,
            active_order=active_order,
            state_filters=state_filters,
            current=current,
            current_source=deck.source_of(current) if current else None,
            lookahead=lookahead,
            lookahead_source=deck.source_of(lookahead) if lookahead else None,
            shuffle=active_shuffle,
        ),
    )


# --------------------------------------------------------------------------- #
#                                shuffle reset                                #
# --------------------------------------------------------------------------- #


@app.post("/shuffle/reset")
def shuffle_reset(request: Request) -> RedirectResponse:
    """Drop the shuffle cookie so the next GET mints a fresh token.

    The redirect target is the Referer when it's same-origin, else /swipe.
    """
    target = _same_origin_path(request, request.headers.get("referer")) or "/swipe"
    response = RedirectResponse(url=target, status_code=303)
    response.delete_cookie(BNS_SHUFFLE, path="/")
    return response


# --------------------------------------------------------------------------- #
#                                  overview                                   #
# --------------------------------------------------------------------------- #


def _resolve_overview_list(slug: str | None) -> str:
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


def _overview_context(user: str, list_slug: str) -> dict[str, object]:
    return {
        "user": user,
        "active_list": list_slug,
        "ov": overview(user, list_slug),
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
    list_slug = _resolve_overview_list(list)
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
    user_or_redirect = _user_or_redirect(who)
    if isinstance(user_or_redirect, RedirectResponse):
        raise HTTPException(status_code=401, detail="No user")
    user = user_or_redirect
    list_slug = _resolve_overview_list(list)
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
    user_or_redirect = _user_or_redirect(who)
    if isinstance(user_or_redirect, RedirectResponse):
        raise HTTPException(status_code=401, detail="No user")
    user = user_or_redirect
    list_slug = _resolve_overview_list(list)
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
    user_or_redirect = _user_or_redirect(who)
    if isinstance(user_or_redirect, RedirectResponse):
        raise HTTPException(status_code=401, detail="No user")
    user = user_or_redirect
    list_slug = _resolve_overview_list(list)
    clean = name.strip()
    if remove_manual_name(list_slug, clean):
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


def _lists_context(
    *,
    user: str,
    list_slugs: list[str],
    order: str,
    view: str,
    states: list[str],
    offset: int,
    rows_all: list[NameRow],
    shuffle: str,
) -> dict[str, object]:
    end = offset + LISTS_PAGE_SIZE
    visible = rows_all[offset:end]
    has_more = end < len(rows_all)
    next_offset = end if has_more else None
    add_list_slug = list_slugs[0] if len(list_slugs) == 1 else None
    next_rows_url: str | None = None
    if next_offset is not None:
        params: list[tuple[str, str]] = [("list", s) for s in list_slugs]
        params.append(("order", order))
        params.append(("view", view))
        params.extend(("state", st) for st in states)
        if shuffle:
            params.append(("shuffle", shuffle))
        params.append(("offset", str(next_offset)))
        next_rows_url = "/lists/rows?" + urlencode(params)
    return {
        "user": user,
        "lists": list_available_lists(),
        "selected_slugs": list_slugs,
        "selected_set": set(list_slugs),
        "active_order": order,
        "active_view": view,
        "active_states": states,
        "active_states_set": set(states),
        "active_shuffle": shuffle,
        "rows": visible,
        "rows_total": len(rows_all),
        "has_more": has_more,
        "next_offset": next_offset,
        "next_rows_url": next_rows_url,
        "add_list_slug": add_list_slug,
        "active_list": add_list_slug,
    }


@app.get("/lists", response_class=HTMLResponse)
def lists_page(
    request: Request,
    list: Annotated[list[str] | None, Query()] = None,  # noqa: A002
    order: str | None = None,
    view: str | None = None,
    state: Annotated[list[str] | None, Query()] = None,
    shuffle: str | None = None,
    who: WhoCookie = None,
    bns_shuffle: Annotated[str | None, Cookie(alias=BNS_SHUFFLE)] = None,
    bns_view_lists: Annotated[str | None, Cookie(alias=BNS_LISTS)] = None,
    bns_view_states: Annotated[str | None, Cookie(alias=BNS_STATES)] = None,
) -> Response:
    user_or_redirect = _user_or_redirect(who)
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    user = user_or_redirect
    list_slugs = _resolve_list_slugs(list, bns_view_lists)
    active_order = _resolve_order(order)
    active_view = normalise_view(view)
    states = _resolve_state_filters(state, bns_view_states)
    active_shuffle = _resolve_shuffle(shuffle, bns_shuffle, order=active_order)

    rows_all = build_rows(
        user=user,
        list_slugs=list_slugs,
        order=active_order,
        states=states,
        shuffle=active_shuffle,
    )
    ctx = _lists_context(
        user=user,
        list_slugs=list_slugs,
        order=active_order,
        view=active_view,
        states=states,
        offset=0,
        rows_all=rows_all,
        shuffle=active_shuffle,
    )
    response = templates.TemplateResponse(request, "lists.html", ctx)
    _set_view_cookies(
        response,
        shuffle=active_shuffle,
        list_slugs=list_slugs,
        state_filters=states,
    )
    return response


@app.get("/lists/rows", response_class=HTMLResponse)
def lists_rows(
    request: Request,
    list: Annotated[list[str] | None, Query()] = None,  # noqa: A002
    order: str | None = None,
    view: str | None = None,
    state: Annotated[list[str] | None, Query()] = None,
    shuffle: str | None = None,
    offset: int = 0,
    who: WhoCookie = None,
    bns_shuffle: Annotated[str | None, Cookie(alias=BNS_SHUFFLE)] = None,
    bns_view_lists: Annotated[str | None, Cookie(alias=BNS_LISTS)] = None,
    bns_view_states: Annotated[str | None, Cookie(alias=BNS_STATES)] = None,
) -> Response:
    user_or_redirect = _user_or_redirect(who)
    if isinstance(user_or_redirect, RedirectResponse):
        raise HTTPException(status_code=401, detail="No user")
    user = user_or_redirect
    list_slugs = _resolve_list_slugs(list, bns_view_lists)
    active_order = _resolve_order(order)
    active_view = normalise_view(view)
    states = _resolve_state_filters(state, bns_view_states)
    active_shuffle = _resolve_shuffle(shuffle, bns_shuffle, order=active_order)
    rows_all = build_rows(
        user=user,
        list_slugs=list_slugs,
        order=active_order,
        states=states,
        shuffle=active_shuffle,
    )
    ctx = _lists_context(
        user=user,
        list_slugs=list_slugs,
        order=active_order,
        view=active_view,
        states=states,
        offset=max(0, offset),
        rows_all=rows_all,
        shuffle=active_shuffle,
    )
    return templates.TemplateResponse(request, "_lists_rows.html", ctx)


def _resolve_row_list(slug: str) -> str:
    """Validate a per-row list slug. Unlike _resolve_overview_list, no fallback."""
    available = {nl.slug for nl in list_available_lists()}
    if slug not in available:
        raise HTTPException(status_code=400, detail="Unknown list")
    return slug


def _render_row(
    *,
    user: str,
    list_slug: str,
    name: str,
) -> list[NameRow]:
    rows = build_rows(
        user=user,
        list_slugs=[list_slug],
        order=ORDER_RANDOM,
        states=list(ALL_STATES),
    )
    return [r for r in rows if r.name == name]


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
    new_match = direction == LIKE and is_match(user, list_slug, clean)
    rows = _render_row(user=user, list_slug=list_slug, name=clean)
    row = rows[0] if rows else None
    response = templates.TemplateResponse(
        request,
        "_lists_row.html",
        {"row": row, "list_slug": list_slug, "name": clean},
    )
    if new_match:
        # JS listens for this event on the lists body and runs the match
        # celebration overlay. Payload is JSON so the name survives quoting.
        response.headers["HX-Trigger"] = json.dumps({"matchCreated": {"name": clean}})
    return response


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
    rows = _render_row(user=user, list_slug=list_slug, name=clean)
    row = rows[0] if rows else None
    return templates.TemplateResponse(
        request,
        "_lists_row.html",
        {"row": row, "list_slug": list_slug, "name": clean},
    )


@app.post("/lists/delete", response_class=HTMLResponse)
def lists_delete(
    name: Annotated[str, Form()],
    list: Annotated[str, Form(alias="list")],  # noqa: A002
    who: WhoCookie = None,
) -> HTMLResponse:
    user_or_redirect = _user_or_redirect(who)
    if isinstance(user_or_redirect, RedirectResponse):
        raise HTTPException(status_code=401, detail="No user")
    list_slug = _resolve_row_list(list)
    clean = name.strip()
    if remove_manual_name(list_slug, clean):
        for u in USERS:
            remove_swipe(u, list_slug, clean)
        invalidate_list_decks(list_slug)
    return HTMLResponse("")


@app.post("/add-name")
def add_name(
    request: Request,
    name: Annotated[str, Form()],
    list: Annotated[str, Form(alias="list")],  # noqa: A002
    order: Annotated[str | None, Form()] = None,
    who: WhoCookie = None,
) -> RedirectResponse:
    """Add a single name to a list's manual CSV and auto-like it for this user."""
    user_or_redirect = _user_or_redirect(who)
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    user = user_or_redirect
    available = {nl.slug for nl in list_available_lists()}
    if list not in available:
        raise HTTPException(status_code=400, detail="Unknown list")
    active_order = _resolve_order(order)
    cleaned = name.strip()
    target_key = cleaned.casefold()
    existing = next(
        (n for n in load_names(list) if n.casefold() == target_key),
        None,
    )
    if existing is not None:
        record(user, list, existing, LIKE)
        invalidate_decks(user, list)
    else:
        try:
            added = add_manual_name(list, name)
        except ValueError:
            added = None
        if added is not None:
            record(user, list, added, LIKE)
            absorb_added_name(list, added)
    target = _same_origin_path(request, request.headers.get("referer"))
    if target is None:
        target = f"/swipe?list={list}&order={active_order}"
    sep = "&" if "?" in target else "?"
    target += f"{sep}added=1"
    return RedirectResponse(url=target, status_code=303)


def _same_origin_path(request: Request, referer: str | None) -> str | None:
    """Return the path+query of `referer` if it points at this app, else None."""
    if not referer:
        return None
    parsed = urlparse(referer)
    if parsed.netloc and parsed.netloc != request.url.netloc:
        return None
    path = parsed.path or "/"
    if path.startswith(("/add-name", "/shuffle/reset")):
        return None
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


# `undo_last` is re-exported here only so the import in tests' helper still
# resolves; routes themselves use undo_last_across.
__all__ = ["app", "undo_last"]
