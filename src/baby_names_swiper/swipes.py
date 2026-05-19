"""Swipe state: record, query, undo."""

from __future__ import annotations

from dataclasses import dataclass
import random

from baby_names_swiper.config import USERS
from baby_names_swiper.db import cursor
from baby_names_swiper.names import load_names

LIKE = 1
DISLIKE = 0


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


def next_name(user: str, list_slug: str) -> str | None:
    """Random unseen name from the list, or None if everything has been swiped."""
    all_names = load_names(list_slug)
    if not all_names:
        return None
    seen = _seen_names(user, list_slug)
    remaining = [n for n in all_names if n not in seen]
    if not remaining:
        return None
    return random.choice(remaining)  # noqa: S311 -- not security sensitive


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
        name = row["name"]
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
