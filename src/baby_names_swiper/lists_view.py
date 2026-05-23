"""Pool assembly + per-row decoration for the /lists review page.

Kept separate from `app.py` so the routing layer stays thin and so the
ordering / filtering / decoration logic is easy to test.
"""

from __future__ import annotations

from dataclasses import dataclass

from baby_names_swiper.names import load_manual_names, load_names
from baby_names_swiper.swipes import ALL_STATES, MODE_RANDOM, order_names, states_for

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
    """Filter `raw` to the valid state values, defaulting to all when empty."""
    if not raw:
        return list(ALL_STATES)
    cleaned = [s for s in raw if s in ALL_STATES]
    return cleaned or list(ALL_STATES)


def normalise_view(value: str | None) -> str:
    if value in VALID_VIEWS:
        return value
    return VIEW_CARD


def _union_pool(list_slugs: list[str]) -> tuple[list[str], dict[str, str]]:
    """Merge the names of every selected list, deduped case-insensitively.

    Returns the deduped list of canonical names (case-folded order, preserving
    whichever casing was seen first) and a map {name: source_list_slug} that
    identifies which list a name came from -- used for the per-list manual
    membership check.
    """
    seen_lower: dict[str, str] = {}  # lower -> canonical name
    source: dict[str, str] = {}  # canonical name -> list slug
    for slug in list_slugs:
        for n in load_names(slug):
            key = n.casefold()
            if key in seen_lower:
                continue
            seen_lower[key] = n
            source[n] = slug
    return sorted(seen_lower.values(), key=str.casefold), source


def build_rows(
    *,
    user: str,
    list_slugs: list[str],
    mode: str,
    states: list[str],
    reswipe_disliked: bool = False,
    shuffle: str | None = None,
) -> list[NameRow]:
    """Build the full ordered, filtered list of NameRow for the page.

    Sort first, then filter by state, so the user's chosen ordering is
    preserved within each state bucket. The order_names() helper is called
    with the first selected list's slug as the seed key for MODE_RANDOM --
    that's deterministic per (user, list_slugs[0], mode) so a reload returns
    the same order.
    """
    if not list_slugs:
        return []
    pool, source = _union_pool(list_slugs)
    if not pool:
        return []

    # Manual-name lookup is per list: a name only counts as manual if it
    # actually lives in the manual CSV of *its* source list.
    manual_by_slug: dict[str, set[str]] = {
        slug: {n.casefold() for n in load_manual_names(slug)} for slug in list_slugs
    }

    # Use the first slug as the seed key so the random order is stable for a
    # given selection. Multi-list selection is unioned, so this is a
    # documented choice rather than a bug.
    seed_slug = list_slugs[0]

    if mode == MODE_RANDOM:
        # Manual-added names go to the end of the random order (alphabetical
        # among themselves) so that adding a name doesn't reshuffle the user's
        # current view -- they'll just see the new entry appear at the bottom
        # of the list. The base-CSV pool keeps the weighted-random shuffle.
        manual_keys = {k for s in manual_by_slug.values() for k in s}
        base_pool = [n for n in pool if n.casefold() not in manual_keys]
        manual_pool = [n for n in pool if n.casefold() in manual_keys]
        ordered = order_names(
            base_pool,
            mode,
            user=user,
            list_slug=seed_slug,
            reswipe_disliked=reswipe_disliked,
            shuffle=shuffle,
        )
        ordered.extend(sorted(manual_pool, key=str.casefold))
    else:
        ordered = order_names(
            pool,
            mode,
            user=user,
            list_slug=seed_slug,
            reswipe_disliked=reswipe_disliked,
        )

    state_map = states_for(user, seed_slug, ordered)
    # state_map only checks the seed slug's swipes; for a true union view
    # across multiple lists each list has its own swipes, so re-check per
    # name against its source list.
    if len(list_slugs) > 1:
        state_map = {}
        for n in ordered:
            slug = source[n]
            state_map.update(states_for(user, slug, [n]))

    state_set = set(states)
    rows: list[NameRow] = []
    for n in ordered:
        st = state_map.get(n, "unswiped")
        if st not in state_set:
            continue
        slug = source[n]
        is_manual = n.casefold() in manual_by_slug.get(slug, set())
        rows.append(
            NameRow(
                name=n,
                state=st,
                is_manual=is_manual,
                source_list=slug,
            )
        )
    return rows
