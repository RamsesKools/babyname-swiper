"""Per-row decoration for the /lists review page.

The deck (pool, ordering, source mapping) is built by `swipes.get_deck`, so
`/swipe` and `/lists` always agree on the sequence of names. This module
turns that sequence into `NameRow` objects with state and is_manual decoration.
"""

from __future__ import annotations

from dataclasses import dataclass

from baby_names_swiper.names import load_manual_names
from baby_names_swiper.swipes import ALL_STATES, get_deck, states_for

# query-param defaults / valid sets
VIEW_CARD = "card"
VIEW_TABLE = "table"
VALID_VIEWS = (VIEW_CARD, VIEW_TABLE)


@dataclass(frozen=True)
class NameRow:
    """One row/card on the lists page."""

    name: str
    state: str  # "like" | "dislike" | "unswiped"
    is_manual: bool
    # The list this name was sourced from. Used as the target slug for the
    # per-row swipe/unswipe/delete actions so the action hits the correct
    # list when multiple are selected.
    source_list: str


def normalise_states(raw: list[str] | None) -> list[str]:
    """Filter `raw` to the valid state values; empty input means empty selection."""
    if not raw:
        return []
    return [s for s in raw if s in ALL_STATES]


def normalise_view(value: str | None) -> str:
    if value in VALID_VIEWS:
        return value
    return VIEW_CARD


def build_rows(
    *,
    user: str,
    list_slugs: list[str],
    order: str,
    states: list[str],
    shuffle: str | None = None,
) -> list[NameRow]:
    """Decorate the shared deck's name sequence with per-row state + is_manual."""
    if not list_slugs or not states:
        return []
    state_filters = frozenset(states)
    deck = get_deck(
        user,
        list_slugs,
        order=order,
        state_filters=state_filters,
        shuffle=shuffle,
    )
    if not deck.names:
        return []

    manual_by_slug: dict[str, set[str]] = {
        slug: {n.casefold() for n in load_manual_names(slug)} for slug in set(list_slugs)
    }

    # Look up per-name state in its source list (multi-list selection means
    # the same name in different lists can have different states).
    rows: list[NameRow] = []
    state_cache: dict[str, dict[str, str]] = {}
    for name in deck.names:
        slug = deck.source_of(name) or (list_slugs[0] if list_slugs else "")
        if slug not in state_cache:
            state_cache[slug] = {}
        # batched lookup per slug is cheaper, but the deck pool is already
        # filtered to a small set so the per-name call is fine.
        if name not in state_cache[slug]:
            state_cache[slug].update(states_for(user, slug, [name]))
        st = state_cache[slug].get(name, "unswiped")
        is_manual = name.casefold() in manual_by_slug.get(slug, set())
        rows.append(
            NameRow(
                name=name,
                state=st,
                is_manual=is_manual,
                source_list=slug,
            )
        )
    return rows
