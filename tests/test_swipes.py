from __future__ import annotations

from collections import Counter

from baby_names_swiper import config
from baby_names_swiper.swipes import (
    DISLIKE,
    LIKE,
    MODE_ALPHA,
    MODE_PARTNER_LIKES,
    MODE_RANDOM,
    get_deck,
    overview,
    record,
    reset_decks,
    undo_last,
)


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
#                                 deck basics                                 #
# --------------------------------------------------------------------------- #


def test_alpha_deck_is_in_alphabetical_order():
    _seed_list("boys", ["Cas", "Aaron", "Bram"])
    deck = get_deck("Ramses", "boys", mode=MODE_ALPHA)
    assert deck.names == ["Aaron", "Bram", "Cas"]
    assert deck.current() == "Aaron"
    assert deck.lookahead() == "Bram"


def test_deck_advance_and_current_lookahead():
    _seed_list("boys", ["Aaron", "Bram", "Cas"])
    deck = get_deck("Ramses", "boys", mode=MODE_ALPHA)
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
    d1 = get_deck("Ramses", "boys", mode=MODE_ALPHA)
    d1.advance()
    d2 = get_deck("Ramses", "boys", mode=MODE_ALPHA)
    # same object, position preserved
    assert d2 is d1
    assert d2.position == 1


def test_different_mode_gets_a_different_deck():
    _seed_list("boys", ["Aaron", "Bram"])
    d_alpha = get_deck("Ramses", "boys", mode=MODE_ALPHA)
    d_random = get_deck("Ramses", "boys", mode=MODE_RANDOM)
    assert d_alpha is not d_random


def test_partner_likes_deck_only_has_partner_likes():
    _seed_list("boys", ["Aaron", "Bram", "Cas", "Dex"])
    record("Chiara", "boys", "Bram", LIKE)
    record("Chiara", "boys", "Cas", LIKE)
    record("Chiara", "boys", "Aaron", DISLIKE)  # not eligible
    deck = get_deck("Ramses", "boys", mode=MODE_PARTNER_LIKES)
    assert deck.names == ["Bram", "Cas"]


def test_partner_likes_deck_empty_when_partner_has_no_likes():
    _seed_list("boys", ["Aaron", "Bram"])
    deck = get_deck("Ramses", "boys", mode=MODE_PARTNER_LIKES)
    assert deck.names == []
    assert deck.current() is None


# --------------------------------------------------------------------------- #
#                            reswipe / reconcile                              #
# --------------------------------------------------------------------------- #


def test_deck_excludes_already_swiped_names():
    _seed_list("boys", ["Aaron", "Bram", "Cas"])
    record("Ramses", "boys", "Aaron", LIKE)
    record("Ramses", "boys", "Bram", DISLIKE)
    deck = get_deck("Ramses", "boys", mode=MODE_ALPHA)
    assert deck.names == ["Cas"]


def test_reswipe_deck_reincludes_own_dislikes_not_likes():
    _seed_list("boys", ["Aaron", "Bram", "Cas"])
    record("Ramses", "boys", "Aaron", LIKE)
    record("Ramses", "boys", "Bram", DISLIKE)
    deck = get_deck("Ramses", "boys", mode=MODE_ALPHA, reswipe_disliked=True)
    # Bram (disliked) comes back, Aaron (liked) stays gone
    assert deck.names == ["Bram", "Cas"]


def test_reconcile_skips_cursor_past_newly_swiped_name():
    _seed_list("boys", ["Aaron", "Bram", "Cas"])
    deck = get_deck("Ramses", "boys", mode=MODE_ALPHA)
    assert deck.current() == "Aaron"
    # a swipe happens through some other path (e.g. a different deck object)
    record("Ramses", "boys", "Aaron", LIKE)
    deck = get_deck("Ramses", "boys", mode=MODE_ALPHA)
    # cursor reconciled forward past the now-swiped Aaron
    assert deck.current() == "Bram"


# --------------------------------------------------------------------------- #
#                         fixed order survives undo                           #
# --------------------------------------------------------------------------- #


def test_order_is_stable_across_undo():
    """The core requirement: undo replays the same sequence."""
    _seed_list("boys", ["A", "B", "C", "D", "E"])
    deck = get_deck("Ramses", "boys", mode=MODE_RANDOM)
    original_order = list(deck.names)

    # swipe the first three in deck order
    swiped_seq = []
    for _ in range(3):
        name = deck.current()
        assert name is not None
        swiped_seq.append(name)
        record("Ramses", "boys", name, LIKE)
        deck.advance()
    assert swiped_seq == original_order[:3]

    # undo the last swipe
    restored = undo_last("Ramses", "boys")
    assert restored == swiped_seq[-1]
    deck = get_deck("Ramses", "boys", mode=MODE_RANDOM)
    deck.rewind()

    # the deck order is unchanged and the cursor points back at the undone name
    assert deck.names == original_order
    assert deck.current() == swiped_seq[-1]
    # continuing forward yields the same remaining sequence
    deck.advance()
    assert deck.current() == original_order[3]


def test_random_order_is_deterministic_for_same_inputs():
    _seed_list("boys", ["A", "B", "C", "D", "E", "F", "G", "H"])
    first = get_deck("Ramses", "boys", mode=MODE_RANDOM).names
    reset_decks()
    second = get_deck("Ramses", "boys", mode=MODE_RANDOM).names
    assert first == second


def test_random_order_differs_between_users():
    _seed_list("boys", ["A", "B", "C", "D", "E", "F", "G", "H"])
    ramses = get_deck("Ramses", "boys", mode=MODE_RANDOM).names
    chiara = get_deck("Chiara", "boys", mode=MODE_RANDOM).names
    # extremely unlikely to coincide for 8 names with distinct seeds
    assert ramses != chiara


# --------------------------------------------------------------------------- #
#                            weighted random order                            #
# --------------------------------------------------------------------------- #


def test_random_order_front_loads_partner_likes():
    # 30 names: 5 liked by partner, 5 disliked, 20 neutral.
    # With weights 5 : 1 : 0.2 the partner-likes should cluster near the front.
    names = [f"name{i:02d}" for i in range(30)]
    _seed_list("boys", names)
    likes = set(names[:5])
    dislikes = set(names[5:10])
    for n in likes:
        record("Chiara", "boys", n, LIKE)
    for n in dislikes:
        record("Chiara", "boys", n, DISLIKE)

    deck = get_deck("Ramses", "boys", mode=MODE_RANDOM)
    order = deck.names
    positions = {n: order.index(n) for n in order}

    avg_like = sum(positions[n] for n in likes) / len(likes)
    avg_dislike = sum(positions[n] for n in dislikes) / len(dislikes)
    neutral = [n for n in names if n not in likes and n not in dislikes]
    avg_neutral = sum(positions[n] for n in neutral) / len(neutral)

    # likes earlier than neutral earlier than dislikes
    assert avg_like < avg_neutral < avg_dislike


def test_weighted_order_counts_dominated_by_partner_likes():
    # Statistical check across many seeds: which name lands in slot 0 most.
    # The seed depends on the list slug, so a fresh slug each iteration
    # gives an independent draw while keeping a real (user, partner) pair.
    counts: Counter[str] = Counter()
    for i in range(400):
        reset_decks()
        slug = f"list{i:03d}"
        _seed_list(slug, ["L", "N", "D"])
        record("Chiara", slug, "L", LIKE)
        record("Chiara", slug, "D", DISLIKE)
        deck = get_deck("Ramses", slug, mode=MODE_RANDOM)
        first = deck.current()
        assert first is not None
        counts[first] += 1

    assert counts["L"] > counts["N"]
    assert counts["N"] > counts["D"]
