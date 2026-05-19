"""Swipe state: record, query, undo."""

from __future__ import annotations

from dataclasses import dataclass
import random

from baby_names_swiper.config import USERS
from baby_names_swiper.db import cursor
from baby_names_swiper.names import load_names

LIKE = 1
DISLIKE = 0

# next-name modes
MODE_RANDOM = "random"
MODE_ALPHA = "alpha"
MODE_PARTNER_LIKES = "partner_likes"
VALID_MODES = (MODE_RANDOM, MODE_ALPHA, MODE_PARTNER_LIKES)

# weighting for MODE_RANDOM: relative selection weights
WEIGHT_PARTNER_LIKE = 5.0     # 5x more likely than a neutral name
WEIGHT_NEUTRAL = 1.0
WEIGHT_PARTNER_DISLIKE = 0.2  # 5x less likely than a neutral name


@dataclass(frozen=True)
class Overview:
    my_likes: list[str]
    my_dislikes: list[str]
    matches: list[str]
    partner_likes_only: list[str]
    total: int
    swiped: int
    remaining: int


def record(user: str, list_slug: str, name: str, direction: int) -> None:
    if direction not in (LIKE, DISLIKE):
        msg = "direction must be 0 or 1"
        raise ValueError(msg)
    with cursor() as cur:
        cur.execute(
            """
            INSERT INTO swipes (user, list_slug, name, direction, created_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(user, list_slug, name) DO UPDATE SET
                direction = excluded.direction,
                created_at = excluded.created_at
            """,
            (user, list_slug, name, direction),
        )


def _seen_names(user: str, list_slug: str) -> set[str]:
    with cursor() as cur:
        rows = cur.execute(
            "SELECT name FROM swipes WHERE user = ? AND list_slug = ?",
            (user, list_slug),
        ).fetchall()
    return {row["name"] for row in rows}


def next_name(
    user: str,
    list_slug: str,
    *,
    mode: str = MODE_RANDOM,
    reswipe_disliked: bool = False,
) -> str | None:
    """Pick the next name to show, given the active mode and reswipe flag.

    Modes:
      - random: weighted by partner's previous swipes
      - alpha: next alphabetically
      - partner_likes: only names the partner liked

    reswipe_disliked: include names the *current user* previously disliked.
    """
    if mode not in VALID_MODES:
        mode = MODE_RANDOM

    all_names = load_names(list_slug)
    if not all_names:
        return None

    my_likes = _liked_by(user, list_slug)
    my_dislikes = _disliked_by(user, list_slug)

    # "seen" = anything we should skip showing again on the swipe screen
    seen = set(my_likes)
    if not reswipe_disliked:
        seen |= my_dislikes

    partner = _partner(user)
    partner_likes = _liked_by(partner, list_slug) if partner else set()
    partner_dislikes = _disliked_by(partner, list_slug) if partner else set()

    if mode == MODE_PARTNER_LIKES:
        pool = [n for n in sorted(partner_likes, key=str.casefold) if n not in seen]
        return pool[0] if pool else None

    pool = [n for n in all_names if n not in seen]
    if not pool:
        return None

    if mode == MODE_ALPHA:
        return sorted(pool, key=str.casefold)[0]

    # weighted random
    weights = [
        WEIGHT_PARTNER_LIKE
        if n in partner_likes
        else WEIGHT_PARTNER_DISLIKE
        if n in partner_dislikes
        else WEIGHT_NEUTRAL
        for n in pool
    ]
    return random.choices(pool, weights=weights, k=1)[0]  # noqa: S311 -- not security sensitive


def undo_last(user: str, list_slug: str) -> str | None:
    """Delete the latest swipe and return the name so the UI can re-show it."""
    with cursor() as cur:
        row = cur.execute(
            """
            SELECT name FROM swipes
            WHERE user = ? AND list_slug = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (user, list_slug),
        ).fetchone()
        if row is None:
            return None
        name: str = row["name"]
        cur.execute(
            "DELETE FROM swipes WHERE user = ? AND list_slug = ? AND name = ?",
            (user, list_slug, name),
        )
    return name


def _liked_by(user: str, list_slug: str) -> set[str]:
    with cursor() as cur:
        rows = cur.execute(
            "SELECT name FROM swipes WHERE user = ? AND list_slug = ? AND direction = 1",
            (user, list_slug),
        ).fetchall()
    return {row["name"] for row in rows}


def _disliked_by(user: str, list_slug: str) -> set[str]:
    with cursor() as cur:
        rows = cur.execute(
            "SELECT name FROM swipes WHERE user = ? AND list_slug = ? AND direction = 0",
            (user, list_slug),
        ).fetchall()
    return {row["name"] for row in rows}


def _partner(user: str) -> str | None:
    others = [u for u in USERS if u != user]
    return others[0] if others else None


def overview(user: str, list_slug: str) -> Overview:
    all_names = load_names(list_slug)
    my_likes = _liked_by(user, list_slug)
    my_dislikes = _disliked_by(user, list_slug)
    swiped = my_likes | my_dislikes

    partner = _partner(user)
    partner_likes = _liked_by(partner, list_slug) if partner else set()

    matches = my_likes & partner_likes
    partner_likes_only = partner_likes - my_likes

    return Overview(
        my_likes=sorted(my_likes, key=str.casefold),
        my_dislikes=sorted(my_dislikes, key=str.casefold),
        matches=sorted(matches, key=str.casefold),
        partner_likes_only=sorted(partner_likes_only, key=str.casefold),
        total=len(all_names),
        swiped=len(swiped),
        remaining=max(len(all_names) - len(swiped), 0),
    )
