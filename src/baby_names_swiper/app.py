"""FastAPI app: routes for swipe / overview / upload / user-picker."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import Cookie, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from baby_names_swiper.config import COOKIE_NAME, MAX_UPLOAD_BYTES, USERS
from baby_names_swiper.db import init_db
from baby_names_swiper.deps import read_user, sign_user
from baby_names_swiper.names import get_list, list_available_lists, save_upload
from baby_names_swiper.swipes import DISLIKE, LIKE, next_name, overview, record, undo_last

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_PKG_DIR = Path(__file__).resolve().parent
_STATIC_DIR = _PKG_DIR / "static"
_TEMPLATES_DIR = _PKG_DIR / "templates"


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


@app.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"ok": True})


@app.get("/", include_in_schema=False)
def root(who: str | None = Cookie(default=None)) -> RedirectResponse:  # noqa: B008
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
def set_user(user: str = Form(...)) -> RedirectResponse:
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


@app.get("/swipe", response_class=HTMLResponse)
def swipe_page(
    request: Request,
    list: str | None = None,  # noqa: A002
    who: str | None = Cookie(default=None),  # noqa: B008
) -> Response:
    user_or_redirect = _user_or_redirect(who)
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    user = user_or_redirect
    list_slug = _resolve_list(list)
    current_name = next_name(user, list_slug)
    return templates.TemplateResponse(
        request,
        "swipe.html",
        {
            "user": user,
            "lists": list_available_lists(),
            "active_list": list_slug,
            "current_name": current_name,
        },
    )


@app.get("/swipe/card", response_class=HTMLResponse)
def swipe_card(
    request: Request,
    list: str,  # noqa: A002
    who: str | None = Cookie(default=None),  # noqa: B008
) -> HTMLResponse:
    user_or_redirect = _user_or_redirect(who)
    if isinstance(user_or_redirect, RedirectResponse):
        raise HTTPException(status_code=401, detail="No user")
    user = user_or_redirect
    list_slug = _resolve_list(list)
    name = next_name(user, list_slug)
    template = "_card.html" if name else "_empty.html"
    return templates.TemplateResponse(
        request,
        template,
        {"name": name, "active_list": list_slug, "user": user},
    )


@app.post("/swipe", response_class=HTMLResponse)
def post_swipe(
    request: Request,
    name: str = Form(...),
    direction: int = Form(...),
    list: str = Form(...),  # noqa: A002
    who: str | None = Cookie(default=None),  # noqa: B008
) -> HTMLResponse:
    user_or_redirect = _user_or_redirect(who)
    if isinstance(user_or_redirect, RedirectResponse):
        raise HTTPException(status_code=401, detail="No user")
    user = user_or_redirect
    if direction not in (LIKE, DISLIKE):
        raise HTTPException(status_code=400, detail="bad direction")
    list_slug = _resolve_list(list)
    record(user, list_slug, name.strip(), direction)
    next_one = next_name(user, list_slug)
    template = "_card.html" if next_one else "_empty.html"
    return templates.TemplateResponse(
        request,
        template,
        {"name": next_one, "active_list": list_slug, "user": user},
    )


@app.post("/swipe/undo", response_class=HTMLResponse)
def post_undo(
    request: Request,
    list: str = Form(...),  # noqa: A002
    who: str | None = Cookie(default=None),  # noqa: B008
) -> HTMLResponse:
    user_or_redirect = _user_or_redirect(who)
    if isinstance(user_or_redirect, RedirectResponse):
        raise HTTPException(status_code=401, detail="No user")
    user = user_or_redirect
    list_slug = _resolve_list(list)
    restored = undo_last(user, list_slug)
    name = restored if restored else next_name(user, list_slug)
    template = "_card.html" if name else "_empty.html"
    return templates.TemplateResponse(
        request,
        template,
        {"name": name, "active_list": list_slug, "user": user},
    )


@app.get("/overview", response_class=HTMLResponse)
def overview_page(
    request: Request,
    list: str | None = None,  # noqa: A002
    who: str | None = Cookie(default=None),  # noqa: B008
) -> Response:
    user_or_redirect = _user_or_redirect(who)
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    user = user_or_redirect
    list_slug = _resolve_list(list)
    data = overview(user, list_slug)
    return templates.TemplateResponse(
        request,
        "overview.html",
        {
            "user": user,
            "lists": list_available_lists(),
            "active_list": list_slug,
            "ov": data,
        },
    )


@app.get("/upload", response_class=HTMLResponse)
def upload_page(
    request: Request,
    who: str | None = Cookie(default=None),  # noqa: B008
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
    who: str | None = Cookie(default=None),  # noqa: B008
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
