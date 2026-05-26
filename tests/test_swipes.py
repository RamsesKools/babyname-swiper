from __future__ import annotations

from collections import Counter

from baby_names_swiper import config
from baby_names_swiper.swipes import (
    DISLIKE,
    LIKE,
    ORDER_ALPHA,
    ORDER_PARTNER_LIKES,
    ORDER_RANDOM,
    STATE_DISLIKE,
    STATE_LIKE,
    STATE_UNSWIPED,
    absorb_added_name,
    get_deck,
    overview,
    record,
    remove_swipe,
    reset_decks,
    reset_list,
    undo_last,
)

UNSWIPED_ONLY = frozenset({STATE_UNSWIPED})
ALL_FILTERS = frozenset({STATE_LIKE, STATE_DISLIKE, STATE_UNSWIPED})


def _seed_list(slug: str, names: list[str]) -> None:
    (config.NAMES_DIR / f"{slug}.csv").write_text("\n".join(names) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
#                              record / overview                              #
# --------------------------------------------------------------------------- #


def test_overview_matches_and_partner_likes():
    _seed_list("boys", ["Aaron", "Bram", "Cas", "Dex"])
    record("Ramses", "boys", "Aaron", LIKE)
    record("Ramses", "boys", "Bram", LIKE)
    record("Ramses", "boys", "Cas", DISLIKE)
    record("Chiara", "boys", "Aaron", LIKE)  # match
    record("Chiara", "boys", "Dex", LIKE)  # partner only

    ov = overview("Ramses", "boys")
    assert ov.matches == ["Aaron"]
    assert ov.partner_likes_only == ["Dex"]
    assert ov.my_likes == ["Aaron", "Bram"]
    assert ov.my_dislikes == ["Cas"]
    assert ov.total == 4
    assert ov.swiped == 3
    assert ov.remaining == 1


def test_record_upsert_overrides_direction():
    _seed_list("boys", ["Aaron"])
    record("Ramses", "boys", "Aaron", DISLIKE)
    record("Ramses", "boys", "Aaron", LIKE)
    ov = overview("Ramses", "boys")
    assert ov.my_likes == ["Aaron"]
    assert ov.my_dislikes == []


def test_undo_last_returns_none_with_no_history():
    _seed_list("boys", ["Aaron"])
    assert undo_last("Ramses", "boys") is None


# --------------------------------------------------------------------------- #
#                          remove single / reset list                         #
# --------------------------------------------------------------------------- #


def test_remove_swipe_deletes_one_and_reports_true():
    _seed_list("boys", ["Aaron", "Bram"])
    record("Ramses", "boys", "Aaron", LIKE)
    record("Ramses", "boys", "Bram", DISLIKE)
    assert remove_swipe("Ramses", "boys", "Aaron") is True
    ov = overview("Ramses", "boys")
    assert ov.my_likes == []
    assert ov.my_dislikes == ["Bram"]


def test_remove_swipe_missing_name_reports_false():
    _seed_list("boys", ["Aaron"])
    assert remove_swipe("Ramses", "boys", "Aaron") is False


def test_remove_swipe_only_touches_that_users_rows():
    _seed_list("boys", ["Aaron"])
    record("Ramses", "boys", "Aaron", LIKE)
    record("Chiara", "boys", "Aaron", LIKE)
    remove_swipe("Ramses", "boys", "Aaron")
    assert overview("Ramses", "boys").my_likes == []
    assert overview("Chiara", "boys").my_likes == ["Aaron"]


def test_removed_name_reenters_the_deck():
    _seed_list("boys", ["Aaron", "Bram", "Cas"])
    record("Ramses", "boys", "Aaron", LIKE)
    deck = get_deck("Ramses", ["boys"], order=ORDER_ALPHA, state_filters=UNSWIPED_ONLY)
    assert "Aaron" not in deck.names  # excluded while liked
    remove_swipe("Ramses", "boys", "Aaron")
    deck = get_deck("Ramses", ["boys"], order=ORDER_ALPHA, state_filters=UNSWIPED_ONLY)
    assert "Aaron" in deck.names


def test_reset_list_clears_all_of_users_swipes():
    _seed_list("boys", ["Aaron", "Bram", "Cas"])
    record("Ramses", "boys", "Aaron", LIKE)
    record("Ramses", "boys", "Bram", DISLIKE)
    record("Ramses", "boys", "Cas", LIKE)
    removed = reset_list("Ramses", "boys")
    assert removed == 3
    ov = overview("Ramses", "boys")
    assert ov.my_likes == []
    assert ov.my_dislikes == []
    assert ov.swiped == 0


def test_reset_list_only_touches_that_user_and_list():
    _seed_list("boys", ["Aaron"])
    _seed_list("girls", ["Anna"])
    record("Ramses", "boys", "Aaron", LIKE)
    record("Ramses", "girls", "Anna", LIKE)
    record("Chiara", "boys", "Aaron", LIKE)
    reset_list("Ramses", "boys")
    assert overview("Ramses", "boys").my_likes == []
    assert overview("Ramses", "girls").my_likes == ["Anna"]
    assert overview("Chiara", "boys").my_likes == ["Aaron"]


def test_reset_list_returns_zero_when_nothing_to_clear():
    _seed_list("boys", ["Aaron"])
    assert reset_list("Ramses", "boys") == 0


# --------------------------------------------------------------------------- #
#                                 deck basics                                 #
# --------------------------------------------------------------------------- #


def test_alpha_deck_is_in_alphabetical_order():
    _seed_list("boys", ["Cas", "Aaron", "Bram"])
    deck = get_deck("Ramses", ["boys"], order=ORDER_ALPHA, state_filters=UNSWIPED_ONLY)
    assert deck.names == ["Aaron", "Bram", "Cas"]
    assert deck.current() == "Aaron"
    assert deck.lookahead() == "Bram"


def test_deck_advance_and_current_lookahead():
    _seed_list("boys", ["Aaron", "Bram", "Cas"])
    deck = get_deck("Ramses", ["boys"], order=ORDER_ALPHA, state_filters=UNSWIPED_ONLY)
    assert deck.current() == "Aaron"
    deck.advance()
    assert deck.current() == "Bram"
    assert deck.lookahead() == "Cas"
    deck.advance()
    assert deck.current() == "Cas"
    assert deck.lookahead() is None
    deck.advance()
    assert deck.current() is None


def test_deck_is_cached_per_combination():
    _seed_list("boys", ["Aaron", "Bram"])
    d1 = get_deck("Ramses", ["boys"], order=ORDER_ALPHA, state_filters=UNSWIPED_ONLY)
    d1.advance()
    d2 = get_deck("Ramses", ["boys"], order=ORDER_ALPHA, state_filters=UNSWIPED_ONLY)
    assert d2 is d1
    assert d2.position == 1


def test_different_mode_gets_a_different_deck():
    _seed_list("boys", ["Aaron", "Bram"])
    d_alpha = get_deck("Ramses", ["boys"], order=ORDER_ALPHA, state_filters=UNSWIPED_ONLY)
    d_random = get_deck("Ramses", ["boys"], order=ORDER_RANDOM, state_filters=UNSWIPED_ONLY)
    assert d_alpha is not d_random


def test_partner_likes_deck_only_has_partner_likes():
    _seed_list("boys", ["Aaron", "Bram", "Cas", "Dex"])
    record("Chiara", "boys", "Bram", LIKE)
    record("Chiara", "boys", "Cas", LIKE)
    record("Chiara", "boys", "Aaron", DISLIKE)
    deck = get_deck(
        "Ramses",
        ["boys"],
        order=ORDER_PARTNER_LIKES,
        state_filters=UNSWIPED_ONLY,
    )
    assert deck.names == ["Bram", "Cas"]


def test_partner_likes_deck_empty_when_partner_has_no_likes():
    _seed_list("boys", ["Aaron", "Bram"])
    deck = get_deck(
        "Ramses",
        ["boys"],
        order=ORDER_PARTNER_LIKES,
        state_filters=UNSWIPED_ONLY,
    )
    assert deck.names == []
    assert deck.current() is None


# --------------------------------------------------------------------------- #
#                          state filters + reconcile                          #
# --------------------------------------------------------------------------- #


def test_deck_excludes_already_swiped_names_with_unswiped_filter():
    _seed_list("boys", ["Aaron", "Bram", "Cas"])
    record("Ramses", "boys", "Aaron", LIKE)
    record("Ramses", "boys", "Bram", DISLIKE)
    deck = get_deck("Ramses", ["boys"], order=ORDER_ALPHA, state_filters=UNSWIPED_ONLY)
    assert deck.names == ["Cas"]


def test_dislike_only_filter_returns_disliked_names():
    _seed_list("boys", ["Aaron", "Bram", "Cas"])
    record("Ramses", "boys", "Aaron", LIKE)
    record("Ramses", "boys", "Bram", DISLIKE)
    deck = get_deck(
        "Ramses",
        ["boys"],
        order=ORDER_ALPHA,
        state_filters=frozenset({STATE_DISLIKE}),
    )
    assert deck.names == ["Bram"]


def test_like_and_unswiped_filter_excludes_dislikes():
    _seed_list("boys", ["Aaron", "Bram", "Cas"])
    record("Ramses", "boys", "Aaron", LIKE)
    record("Ramses", "boys", "Bram", DISLIKE)
    deck = get_deck(
        "Ramses",
        ["boys"],
        order=ORDER_ALPHA,
        state_filters=frozenset({STATE_LIKE, STATE_UNSWIPED}),
    )
    assert deck.names == ["Aaron", "Cas"]


def test_empty_state_filter_yields_empty_deck():
    _seed_list("boys", ["Aaron"])
    deck = get_deck("Ramses", ["boys"], order=ORDER_ALPHA, state_filters=frozenset())
    assert deck.names == []


def test_reconcile_skips_cursor_past_newly_swiped_name():
    _seed_list("boys", ["Aaron", "Bram", "Cas"])
    deck = get_deck("Ramses", ["boys"], order=ORDER_ALPHA, state_filters=UNSWIPED_ONLY)
    assert deck.current() == "Aaron"
    record("Ramses", "boys", "Aaron", LIKE)
    deck = get_deck("Ramses", ["boys"], order=ORDER_ALPHA, state_filters=UNSWIPED_ONLY)
    assert deck.current() == "Bram"


# --------------------------------------------------------------------------- #
#                                   multi-list                                #
# --------------------------------------------------------------------------- #


def test_multi_list_deck_unions_pools_deduped_and_records_source():
    _seed_list("boys", ["Aaron", "Robin"])
    _seed_list("unisex", ["Robin", "Sam"])
    deck = get_deck(
        "Ramses",
        ["boys", "unisex"],
        order=ORDER_ALPHA,
        state_filters=UNSWIPED_ONLY,
    )
    assert deck.names == ["Aaron", "Robin", "Sam"]
    # boys wins the dedupe for "Robin" (it's first in the list_slugs order)
    assert deck.source_of("Aaron") == "boys"
    assert deck.source_of("Robin") == "boys"
    assert deck.source_of("Sam") == "unisex"


def test_multi_list_deck_state_filter_uses_per_list_state():
    _seed_list("boys", ["Aaron"])
    _seed_list("girls", ["Aaron"])  # same name in both lists, but separate slugs
    record("Ramses", "boys", "Aaron", LIKE)  # liked in boys, unswiped in girls
    # source attribution picks "boys" first, so this Aaron is "liked"; the
    # likes-only filter should keep it, the unswiped filter should drop it.
    deck_likes = get_deck(
        "Ramses",
        ["boys", "girls"],
        order=ORDER_ALPHA,
        state_filters=frozenset({STATE_LIKE}),
    )
    assert deck_likes.names == ["Aaron"]
    deck_unswiped = get_deck(
        "Ramses",
        ["boys", "girls"],
        order=ORDER_ALPHA,
        state_filters=UNSWIPED_ONLY,
    )
    assert deck_unswiped.names == []


# --------------------------------------------------------------------------- #
#                         fixed order survives undo                           #
# --------------------------------------------------------------------------- #


def test_order_is_stable_across_undo():
    _seed_list("boys", ["A", "B", "C", "D", "E"])
    deck = get_deck("Ramses", ["boys"], order=ORDER_RANDOM, state_filters=UNSWIPED_ONLY)
    original_order = list(deck.names)

    swiped_seq = []
    for _ in range(3):
        name = deck.current()
        assert name is not None
        swiped_seq.append(name)
        record("Ramses", "boys", name, LIKE)
        deck.advance()
    assert swiped_seq == original_order[:3]

    restored = undo_last("Ramses", "boys")
    assert restored == swiped_seq[-1]
    deck = get_deck("Ramses", ["boys"], order=ORDER_RANDOM, state_filters=UNSWIPED_ONLY)
    deck.rewind()

    assert deck.names == original_order
    assert deck.current() == swiped_seq[-1]
    deck.advance()
    assert deck.current() == original_order[3]


def test_random_order_is_deterministic_for_same_inputs():
    _seed_list("boys", ["A", "B", "C", "D", "E", "F", "G", "H"])
    first = get_deck(
        "Ramses",
        ["boys"],
        order=ORDER_RANDOM,
        state_filters=UNSWIPED_ONLY,
        shuffle="abcd1234",
    ).names
    reset_decks()
    second = get_deck(
        "Ramses",
        ["boys"],
        order=ORDER_RANDOM,
        state_filters=UNSWIPED_ONLY,
        shuffle="abcd1234",
    ).names
    assert first == second


def test_random_order_differs_between_users():
    _seed_list("boys", ["A", "B", "C", "D", "E", "F", "G", "H"])
    ramses = get_deck(
        "Ramses",
        ["boys"],
        order=ORDER_RANDOM,
        state_filters=UNSWIPED_ONLY,
        shuffle="abcd1234",
    ).names
    chiara = get_deck(
        "Chiara",
        ["boys"],
        order=ORDER_RANDOM,
        state_filters=UNSWIPED_ONLY,
        shuffle="abcd1234",
    ).names
    assert ramses != chiara


# --------------------------------------------------------------------------- #
#                       absorb_added_name (add-name flow)                     #
# --------------------------------------------------------------------------- #


def test_absorb_added_name_appends_to_random_deck():
    _seed_list("boys", ["A", "B", "C"])
    deck = get_deck("Ramses", ["boys"], order=ORDER_RANDOM, state_filters=UNSWIPED_ONLY)
    original_order = list(deck.names)

    absorb_added_name("boys", "Zenith")

    same = get_deck("Ramses", ["boys"], order=ORDER_RANDOM, state_filters=UNSWIPED_ONLY)
    assert same is deck
    assert same.names == [*original_order, "Zenith"]
    assert same.source_of("Zenith") == "boys"


def test_absorb_added_name_rebuilds_alpha_deck():
    _seed_list("boys", ["B", "D"])
    deck = get_deck("Ramses", ["boys"], order=ORDER_ALPHA, state_filters=UNSWIPED_ONLY)
    assert deck.names == ["B", "D"]

    (config.NAMES_DIR / "boys.csv").write_text("B\nC\nD\n", encoding="utf-8")
    absorb_added_name("boys", "C")

    rebuilt = get_deck("Ramses", ["boys"], order=ORDER_ALPHA, state_filters=UNSWIPED_ONLY)
    assert rebuilt is not deck
    assert rebuilt.names == ["B", "C", "D"]


# --------------------------------------------------------------------------- #
#                            weighted random order                            #
# --------------------------------------------------------------------------- #


def test_random_order_front_loads_partner_likes():
    names = [f"name{i:02d}" for i in range(30)]
    _seed_list("boys", names)
    likes = set(names[:5])
    dislikes = set(names[5:10])
    for n in likes:
        record("Chiara", "boys", n, LIKE)
    for n in dislikes:
        record("Chiara", "boys", n, DISLIKE)

    deck = get_deck("Ramses", ["boys"], order=ORDER_RANDOM, state_filters=ALL_FILTERS)
    order = deck.names
    positions = {n: order.index(n) for n in order}

    avg_like = sum(positions[n] for n in likes) / len(likes)
    avg_dislike = sum(positions[n] for n in dislikes) / len(dislikes)
    neutral = [n for n in names if n not in likes and n not in dislikes]
    avg_neutral = sum(positions[n] for n in neutral) / len(neutral)

    assert avg_like < avg_neutral < avg_dislike


def test_weighted_order_counts_dominated_by_partner_likes():
    counts: Counter[str] = Counter()
    for i in range(400):
        reset_decks()
        slug = f"list{i:03d}"
        _seed_list(slug, ["L", "N", "D"])
        record("Chiara", slug, "L", LIKE)
        record("Chiara", slug, "D", DISLIKE)
        deck = get_deck("Ramses", [slug], order=ORDER_RANDOM, state_filters=UNSWIPED_ONLY)
        first = deck.current()
        assert first is not None
        counts[first] += 1

    assert counts["L"] > counts["N"]
    assert counts["N"] > counts["D"]
