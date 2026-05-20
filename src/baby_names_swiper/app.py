"""FastAPI app: routes for swipe / overview / upload / user-picker."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from fastapi import Cookie, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from baby_names_swiper.config import COOKIE_NAME, MAX_UPLOAD_BYTES, USERS
from baby_names_swiper.db import init_db
from baby_names_swiper.deps import read_user, sign_user
from baby_names_swiper.names import list_available_lists, save_upload
from baby_names_swiper.swipes import (
    DISLIKE,
    LIKE,
    MODE_RANDOM,
    VALID_MODES,
    get_deck,
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

    record(user, list_slug, name.strip(), direction)
    deck = get_deck(user, list_slug, mode=active_mode, reswipe_disliked=reswipe_flag)

    lookahead = deck.lookahead()
    template = "_card_next.html" if lookahead else "_card_next_empty.html"
    return templates.TemplateResponse(
        request,
        template,
        _deck_context(
            user=user,
            list_slug=list_slug,
            active_mode=active_mode,
            reswipe_flag=reswipe_flag,
            current=deck.current(),
            lookahead=lookahead,
        ),
    )


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
            "user": user,
            "lists": list_available_lists(),
            "active_list": list_slug,
            "ov": overview(user, list_slug),
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
        {"user": user, "active_list": list_slug, "ov": overview(user, list_slug)},
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
        {"user": user, "active_list": list_slug, "ov": overview(user, list_slug)},
    )


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
