"""Swipe state: record, query, undo, and the in-memory swipe deck.

The deck is shared between /swipe and /lists. For a given combination of
(user, list_slugs, state_filters, order, shuffle) it returns a fixed-order
sequence of names plus the per-name source list slug. The deck lives in
memory only; swipe history itself is persisted in SQLite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import math
import random
import secrets

from baby_names_swiper.config import USERS
from baby_names_swiper.db import cursor
from baby_names_swiper.names import load_manual_names, load_names

LIKE = 1
DISLIKE = 0

# next-name orders
ORDER_RANDOM = "random"
ORDER_ALPHA = "alpha"
ORDER_PARTNER_LIKES = "partner_likes"
VALID_ORDERS = (ORDER_RANDOM, ORDER_ALPHA, ORDER_PARTNER_LIKES)

# weighting for ORDER_RANDOM: relative selection weights
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


def undo_last_across(user: str, list_slugs: list[str]) -> tuple[str, str] | None:
    """Delete the latest swipe across the given lists. Returns (name, slug)."""
    if not list_slugs:
        return None
    placeholders = ",".join("?" * len(list_slugs))
    with cursor() as cur:
        row = cur.execute(
            f"""
            SELECT name, list_slug FROM swipes
            WHERE user = ? AND list_slug IN ({placeholders})
            ORDER BY created_at DESC, rowid DESC
            LIMIT 1
            """,  # noqa: S608 - placeholders are app-controlled slug count
            (user, *list_slugs),
        ).fetchone()
        if row is None:
            return None
        name: str = row["name"]
        slug: str = row["list_slug"]
        cur.execute(
            "DELETE FROM swipes WHERE user = ? AND list_slug = ? AND name = ?",
            (user, slug, name),
        )
    return name, slug


# --------------------------------------------------------------------------- #
#                                  the deck                                   #
# --------------------------------------------------------------------------- #

DeckKey = tuple[str, tuple[str, ...], tuple[str, ...], str, str]
"""Cache key shape: (user, list_slugs, state_filters, order, shuffle)."""

# Alphabet for shuffle tokens: digits + lowercase, unambiguous in URLs.
_SHUFFLE_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
_SHUFFLE_LEN = 8


def new_shuffle_token() -> str:
    """Generate a fresh short alphanumeric token for the random shuffle seed."""
    return "".join(secrets.choice(_SHUFFLE_ALPHABET) for _ in range(_SHUFFLE_LEN))


@dataclass
class Deck:
    """A fixed-order sequence of names plus a cursor into it.

    `names` is frozen for the lifetime of the deck. `position` is where the
    user currently is: names[position] is the active card. `sources` maps each
    name to the list slug it was sourced from (so swipe POSTs can target the
    right list when multiple lists are unioned into one deck).
    """

    names: list[str]
    sources: dict[str, str] = field(default_factory=dict)
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

    def source_of(self, name: str) -> str | None:
        return self.sources.get(name)

    def reconcile(self, eligible: set[str]) -> None:
        """Skip the cursor forward past any name no longer in the eligible pool.

        Handles two cases: a process restart (deck rebuilt, cursor at 0) and
        the same name swiped via a different deck (page switch). A name is
        eligible only if it still matches the deck's state filter for its
        source list.
        """
        while self.position < len(self.names) and self.names[self.position] not in eligible:
            self.position += 1


_decks: dict[DeckKey, Deck] = {}


def _seed_for(key: DeckKey) -> int:
    user, slugs, states, order, shuffle = key
    parts = [user, "|".join(slugs), "|".join(states), order, shuffle]
    raw = "\x1f".join(parts)
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


def _union_pool(list_slugs: list[str]) -> tuple[list[str], dict[str, str]]:
    """Merge the names of every selected list, deduped case-insensitively.

    Returns (deduped canonical names case-folded sorted, {name: source_slug}).
    First-source-wins for case-insensitive duplicates across lists.
    """
    seen_lower: dict[str, str] = {}
    source: dict[str, str] = {}
    for slug in list_slugs:
        for n in load_names(slug):
            key = n.casefold()
            if key in seen_lower:
                continue
            seen_lower[key] = n
            source[n] = slug
    return sorted(seen_lower.values(), key=str.casefold), source


def _build_pool(
    user: str,
    list_slugs: list[str],
    state_filters: frozenset[str],
) -> tuple[list[str], dict[str, str]]:
    """Build the eligible name pool given the lists and state filters.

    Each name is included only when its current swipe state (looked up in its
    source list) matches one of the requested filters. Returns the pool plus
    the source mapping.
    """
    if not list_slugs or not state_filters:
        return [], {}
    pool, source = _union_pool(list_slugs)
    if not pool:
        return [], {}

    # Per-list swipe lookups, so the same name in different lists is judged
    # against the swipes recorded for *its* source list.
    likes_by_slug: dict[str, set[str]] = {}
    dislikes_by_slug: dict[str, set[str]] = {}
    for slug in set(list_slugs):
        likes_by_slug[slug] = _liked_by(user, slug)
        dislikes_by_slug[slug] = _disliked_by(user, slug)

    out: list[str] = []
    out_source: dict[str, str] = {}
    for name in pool:
        slug = source[name]
        if name in likes_by_slug[slug]:
            st = STATE_LIKE
        elif name in dislikes_by_slug[slug]:
            st = STATE_DISLIKE
        else:
            st = STATE_UNSWIPED
        if st in state_filters:
            out.append(name)
            out_source[name] = slug
    return out, out_source


def order_names(
    pool: list[str],
    order: str,
    *,
    user: str,
    list_slugs: list[str],
    state_filters: frozenset[str],
    shuffle: str | None = None,
) -> list[str]:
    """Sort/filter an arbitrary pool of names using a deck order.

    The seed for ORDER_RANDOM is derived from the full deck key so the random
    order is stable across `/swipe` and `/lists` for the same inputs.
    """
    if order not in VALID_ORDERS:
        order = ORDER_RANDOM

    # Partner state is looked up in the first selected list (the "seed slug").
    # Multi-list selection is rare; this matches today's lists_view behaviour.
    seed_slug = list_slugs[0] if list_slugs else ""
    partner = _partner(user)
    partner_likes: set[str] = _liked_by(partner, seed_slug) if partner and seed_slug else set()
    partner_dislikes: set[str] = (
        _disliked_by(partner, seed_slug) if partner and seed_slug else set()
    )

    if order == ORDER_PARTNER_LIKES:
        pool = [n for n in pool if n in partner_likes]
        return sorted(pool, key=str.casefold)

    if order == ORDER_ALPHA:
        return sorted(pool, key=str.casefold)

    key = _deck_key(user, list_slugs, state_filters, order, shuffle)
    seed = _seed_for(key)
    return _seeded_weighted_order(pool, seed, partner_likes, partner_dislikes)


def _deck_key(
    user: str,
    list_slugs: list[str],
    state_filters: frozenset[str],
    order: str,
    shuffle: str | None,
) -> DeckKey:
    slugs_key = tuple(sorted(list_slugs))
    states_key = tuple(sorted(state_filters))
    shuffle_key = (shuffle or "") if order == ORDER_RANDOM else ""
    return (user, slugs_key, states_key, order, shuffle_key)


def _build_order(
    user: str,
    list_slugs: list[str],
    *,
    order: str,
    state_filters: frozenset[str],
    shuffle: str | None,
) -> tuple[list[str], dict[str, str]]:
    """Compute the fixed name order for a fresh deck plus source mapping."""
    pool, source = _build_pool(user, list_slugs, state_filters)
    if not pool:
        return [], {}

    # Manual-added names go to the end of the random order (alphabetical among
    # themselves) so adding a name doesn't reshuffle the visible deck.
    if order == ORDER_RANDOM:
        manual_keys_by_slug: dict[str, set[str]] = {
            slug: {n.casefold() for n in load_manual_names(slug)} for slug in set(list_slugs)
        }
        base_pool: list[str] = []
        manual_pool: list[str] = []
        for n in pool:
            slug = source[n]
            if n.casefold() in manual_keys_by_slug.get(slug, set()):
                manual_pool.append(n)
            else:
                base_pool.append(n)
        ordered = order_names(
            base_pool,
            order,
            user=user,
            list_slugs=list_slugs,
            state_filters=state_filters,
            shuffle=shuffle,
        )
        ordered.extend(sorted(manual_pool, key=str.casefold))
    else:
        ordered = order_names(
            pool,
            order,
            user=user,
            list_slugs=list_slugs,
            state_filters=state_filters,
            shuffle=shuffle,
        )

    # source mapping is keyed by canonical name; the ordering step doesn't
    # change the names themselves, so we can carry it through unchanged.
    return ordered, {n: source[n] for n in ordered}


def get_deck(
    user: str,
    list_slugs: list[str],
    *,
    order: str = ORDER_RANDOM,
    state_filters: frozenset[str],
    shuffle: str | None = None,
) -> Deck:
    """Return the cached deck for this combination, building it if needed.

    The deck's order is fixed once built. The cursor is reconciled against the
    current eligible pool so it always points at a name that still belongs in
    the deck under its state filter.
    """
    if order not in VALID_ORDERS:
        order = ORDER_RANDOM
    key = _deck_key(user, list_slugs, state_filters, order, shuffle)

    deck = _decks.get(key)
    if deck is None:
        names, sources = _build_order(
            user,
            list_slugs,
            order=order,
            state_filters=state_filters,
            shuffle=key[4] or None,
        )
        deck = Deck(names=names, sources=sources)
        _decks[key] = deck

    # Reconcile: drop the cursor onto the next still-eligible name. A name is
    # eligible when its current state (per source list) is still in
    # state_filters.
    eligible = _eligible_set(user, deck.sources, state_filters)
    deck.reconcile(eligible)
    return deck


def _eligible_set(
    user: str,
    sources: dict[str, str],
    state_filters: frozenset[str],
) -> set[str]:
    """Names from the deck whose current state still matches the filter."""
    if not sources or not state_filters:
        return set()
    likes_by_slug: dict[str, set[str]] = {}
    dislikes_by_slug: dict[str, set[str]] = {}
    for slug in set(sources.values()):
        likes_by_slug[slug] = _liked_by(user, slug)
        dislikes_by_slug[slug] = _disliked_by(user, slug)
    eligible: set[str] = set()
    for name, slug in sources.items():
        if name in likes_by_slug[slug]:
            st = STATE_LIKE
        elif name in dislikes_by_slug[slug]:
            st = STATE_DISLIKE
        else:
            st = STATE_UNSWIPED
        if st in state_filters:
            eligible.add(name)
    return eligible


def reset_decks() -> None:
    """Drop all cached decks (used by tests and on list/data changes)."""
    _decks.clear()


def invalidate_decks(user: str, list_slug: str) -> None:
    """Drop cached decks for one user whose deck includes the given list.

    Called when a swipe is removed/reset: the freed-up names must be able to
    re-enter the deck's pool, which only happens on a fresh build.
    """
    stale = [key for key in _decks if key[0] == user and list_slug in key[1]]
    for key in stale:
        del _decks[key]


def invalidate_list_decks(list_slug: str) -> None:
    """Drop every cached deck that includes a list, across all users.

    Called when the list's name pool itself changes (a name added to or
    removed from the manual-additions CSV) so both users rebuild fresh.
    """
    stale = [key for key in _decks if list_slug in key[1]]
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
    random deck for the list (across both users) -- but only when the deck's
    state filter still includes the unswiped state, since freshly added names
    are unswiped.
    """
    for key, deck in list(_decks.items()):
        if list_slug not in key[1]:
            continue
        order = key[2]  # state_filters tuple
        deck_order = key[3]
        if deck_order == ORDER_RANDOM:
            if STATE_UNSWIPED in order and name not in deck.names:
                deck.names.append(name)
                deck.sources[name] = list_slug
            else:
                # filter excludes the new name -- drop the deck so subsequent
                # builds reflect the current state cleanly
                del _decks[key]
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
