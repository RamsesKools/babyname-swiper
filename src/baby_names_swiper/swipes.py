"""Swipe state: record, query, undo, and the in-memory swipe deck.

The deck gives each (user, list, mode, reswipe) combination a *fixed* order of
names. It lives in memory only: on a process restart the deck is rebuilt from
the unswiped pool (swipe history itself is persisted in SQLite, so nothing is
lost — only the exact ordering of yet-unseen names is regenerated).
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
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
WEIGHT_PARTNER_LIKE = 5.0  # 5x more likely than a neutral name
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


# state values returned by states_for(): the active user's swipe on a name.
STATE_LIKE = "like"
STATE_DISLIKE = "dislike"
STATE_UNSWIPED = "unswiped"
ALL_STATES = (STATE_LIKE, STATE_DISLIKE, STATE_UNSWIPED)


def states_for(user: str, list_slug: str, names: list[str]) -> dict[str, str]:
    """Return {name: state} for each input name. State is one of ALL_STATES."""
    likes = _liked_by(user, list_slug)
    dislikes = _disliked_by(user, list_slug)
    out: dict[str, str] = {}
    for n in names:
        if n in likes:
            out[n] = STATE_LIKE
        elif n in dislikes:
            out[n] = STATE_DISLIKE
        else:
            out[n] = STATE_UNSWIPED
    return out


# --------------------------------------------------------------------------- #
#                              low-level queries                              #
# --------------------------------------------------------------------------- #


def record(user: str, list_slug: str, name: str, direction: int) -> None:
    if direction not in (LIKE, DISLIKE):
        msg = "direction must be 0 or 1"
        raise ValueError(msg)
    with cursor() as cur:
        cur.execute(
            """
            INSERT INTO swipes (user, list_slug, name, direction, created_at)
            VALUES (?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%f', 'now'))
            ON CONFLICT(user, list_slug, name) DO UPDATE SET
                direction = excluded.direction,
                created_at = excluded.created_at
            """,
            (user, list_slug, name, direction),
        )


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


def is_match(user: str, list_slug: str, name: str) -> bool:
    """True when both this user and their partner have liked `name`."""
    partner = _partner(user)
    if partner is None:
        return False
    return name in _liked_by(user, list_slug) and name in _liked_by(partner, list_slug)


def undo_last(user: str, list_slug: str) -> str | None:
    """Delete the latest swipe and return the name so the UI can re-show it."""
    with cursor() as cur:
        row = cur.execute(
            """
            SELECT name FROM swipes
            WHERE user = ? AND list_slug = ?
            ORDER BY created_at DESC, rowid DESC
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


# --------------------------------------------------------------------------- #
#                                  the deck                                   #
# --------------------------------------------------------------------------- #

DeckKey = tuple[str, str, str, bool]  # (user, list_slug, mode, reswipe)


@dataclass
class Deck:
    """A fixed-order sequence of names plus a cursor into it.

    `names` is frozen for the lifetime of the deck. `position` is where the
    user currently is: names[position] is the active card.
    """

    names: list[str]
    position: int = 0

    def current(self) -> str | None:
        if 0 <= self.position < len(self.names):
            return self.names[self.position]
        return None

    def lookahead(self) -> str | None:
        nxt = self.position + 1
        if 0 <= nxt < len(self.names):
            return self.names[nxt]
        return None

    def advance(self) -> None:
        if self.position < len(self.names):
            self.position += 1

    def rewind(self) -> None:
        if self.position > 0:
            self.position -= 1

    def reconcile(self, swiped: set[str]) -> None:
        """Skip the cursor forward past any name that has already been swiped.

        Handles two cases: a process restart (deck rebuilt, cursor at 0) and
        the same name swiped via a different deck (mode/list switch).
        """
        while self.position < len(self.names) and self.names[self.position] in swiped:
            self.position += 1


_decks: dict[DeckKey, Deck] = {}


def _seed_for(key: DeckKey) -> int:
    raw = "\x1f".join([key[0], key[1], key[2], "1" if key[3] else "0"])
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def _weight_for(name: str, partner_likes: set[str], partner_dislikes: set[str]) -> float:
    if name in partner_likes:
        return WEIGHT_PARTNER_LIKE
    if name in partner_dislikes:
        return WEIGHT_PARTNER_DISLIKE
    return WEIGHT_NEUTRAL


def _seeded_weighted_order(
    pool: list[str],
    seed: int,
    partner_likes: set[str],
    partner_dislikes: set[str],
) -> list[str]:
    """Deterministic weighted shuffle (Efraimidis-Spirakis keys).

    Each name gets a sort key -ln(u)/weight with u ~ U(0,1) from a seeded RNG;
    sorting ascending yields a weighted-random order that is stable for a
    given seed. Higher weight => smaller key => earlier in the order.
    """
    rng = random.Random(seed)  # noqa: S311 -- ordering only, not security sensitive
    keyed: list[tuple[float, str]] = []
    for name in sorted(pool, key=str.casefold):  # stable base order before keying
        weight = _weight_for(name, partner_likes, partner_dislikes)
        u = rng.random()
        # u is in [0,1); guard against log(0)
        sort_key = -math.log(u if u > 0.0 else 1e-12) / weight
        keyed.append((sort_key, name))
    keyed.sort(key=lambda pair: pair[0])
    return [name for _, name in keyed]


def order_names(
    pool: list[str],
    mode: str,
    *,
    user: str,
    list_slug: str,
    reswipe_disliked: bool = False,
) -> list[str]:
    """Sort/filter an arbitrary pool of names using a deck mode.

    Used by both the swipe deck builder and the lists review page. For
    `MODE_PARTNER_LIKES` the pool is filtered down to names the partner liked;
    for `MODE_RANDOM` the order is the seeded weighted shuffle (same seed key
    the swipe deck uses, so a (user, list, random) view shares the swipe
    deck's ordering bias toward partner-liked names).
    """
    if mode not in VALID_MODES:
        mode = MODE_RANDOM

    partner = _partner(user)
    partner_likes: set[str] = _liked_by(partner, list_slug) if partner else set()
    partner_dislikes: set[str] = _disliked_by(partner, list_slug) if partner else set()

    if mode == MODE_PARTNER_LIKES:
        pool = [n for n in pool if n in partner_likes]
        return sorted(pool, key=str.casefold)

    if mode == MODE_ALPHA:
        return sorted(pool, key=str.casefold)

    seed = _seed_for((user, list_slug, mode, reswipe_disliked))
    return _seeded_weighted_order(pool, seed, partner_likes, partner_dislikes)


def _build_order(
    user: str,
    list_slug: str,
    mode: str,
    *,
    reswipe_disliked: bool,
) -> list[str]:
    """Compute the fixed name order for a fresh deck."""
    all_names = load_names(list_slug)
    if not all_names:
        return []

    my_likes = _liked_by(user, list_slug)
    my_dislikes = _disliked_by(user, list_slug)

    # exclude what counts as already-decided
    excluded = set(my_likes)
    if not reswipe_disliked:
        excluded |= my_dislikes

    pool = [n for n in all_names if n not in excluded]
    return order_names(
        pool,
        mode,
        user=user,
        list_slug=list_slug,
        reswipe_disliked=reswipe_disliked,
    )


def get_deck(
    user: str,
    list_slug: str,
    *,
    mode: str = MODE_RANDOM,
    reswipe_disliked: bool = False,
) -> Deck:
    """Return the cached deck for this combination, building it if needed.

    The deck's order is fixed once built. The cursor is reconciled against the
    current swipe history so it always points at a genuinely unseen name.
    """
    if mode not in VALID_MODES:
        mode = MODE_RANDOM
    key: DeckKey = (user, list_slug, mode, reswipe_disliked)

    deck = _decks.get(key)
    if deck is None:
        order = _build_order(
            user,
            list_slug,
            mode,
            reswipe_disliked=reswipe_disliked,
        )
        deck = Deck(names=order)
        _decks[key] = deck

    swiped = _liked_by(user, list_slug) | _disliked_by(user, list_slug)
    deck.reconcile(swiped)
    return deck


def reset_decks() -> None:
    """Drop all cached decks (used by tests and on list/data changes)."""
    _decks.clear()


def invalidate_decks(user: str, list_slug: str) -> None:
    """Drop cached decks for one user+list so a rebuilt deck picks up changes.

    Called when a swipe is removed/reset: the freed-up names must be able to
    re-enter the deck's pool, which only happens on a fresh build.
    """
    stale = [key for key in _decks if key[0] == user and key[1] == list_slug]
    for key in stale:
        del _decks[key]


def invalidate_list_decks(list_slug: str) -> None:
    """Drop every cached deck for a list, across all users.

    Called when the list's name pool itself changes (a name added to or
    removed from the manual-additions CSV) so both users rebuild fresh.
    """
    stale = [key for key in _decks if key[1] == list_slug]
    for key in stale:
        del _decks[key]


def absorb_added_name(list_slug: str, name: str) -> None:
    """Make cached decks aware of a newly added name without reshuffling.

    For deterministic-order decks (alpha, partner_likes) the order depends on
    the name itself, so they are dropped and the next get_deck() call rebuilds
    them with the new name in its natural place.

    For random decks the order is a one-shot shuffle; rebuilding would yield a
    *different* order and lose the user's scroll position / next-card
    expectation. Instead, the new name is appended to the end of every cached
    random deck for the list (across both users).
    """
    for key, deck in list(_decks.items()):
        if key[1] != list_slug:
            continue
        mode = key[2]
        if mode == MODE_RANDOM:
            if name not in deck.names:
                deck.names.append(name)
        else:
            del _decks[key]


def remove_swipe(user: str, list_slug: str, name: str) -> bool:
    """Delete a single swipe for this user. Returns True if a row was removed."""
    with cursor() as cur:
        cur.execute(
            "DELETE FROM swipes WHERE user = ? AND list_slug = ? AND name = ?",
            (user, list_slug, name),
        )
        removed = cur.rowcount > 0
    if removed:
        invalidate_decks(user, list_slug)
    return removed


def reset_list(user: str, list_slug: str) -> int:
    """Delete all of this user's swipes for a list. Returns the count removed."""
    with cursor() as cur:
        cur.execute(
            "DELETE FROM swipes WHERE user = ? AND list_slug = ?",
            (user, list_slug),
        )
        count = cur.rowcount
    invalidate_decks(user, list_slug)
    return count


# --------------------------------------------------------------------------- #
#                                  overview                                   #
# --------------------------------------------------------------------------- #


def overview(user: str, list_slug: str) -> Overview:
    all_names = load_names(list_slug)
    my_likes = _liked_by(user, list_slug)
    my_dislikes = _disliked_by(user, list_slug)
    swiped = my_likes | my_dislikes

    partner = _partner(user)
    partner_likes: set[str] = _liked_by(partner, list_slug) if partner else set()

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
