from __future__ import annotations

from collections import Counter
import random

from baby_names_swiper import config, swipes as swipes_mod
from baby_names_swiper.swipes import (
    DISLIKE,
    LIKE,
    MODE_ALPHA,
    MODE_PARTNER_LIKES,
    MODE_RANDOM,
    next_name,
    next_two,
    overview,
    record,
    undo_last,
)


def _seed_list(slug: str, names: list[str]) -> None:
    (config.NAMES_DIR / f"{slug}.csv").write_text("\n".join(names) + "\n", encoding="utf-8")


def test_next_name_skips_seen():
    _seed_list("boys", ["Aaron", "Bram", "Cas"])
    record("Ramses", "boys", "Aaron", LIKE)
    record("Ramses", "boys", "Bram", DISLIKE)
    seen_again = next_name("Ramses", "boys")
    assert seen_again == "Cas"


def test_next_name_returns_none_when_all_swiped():
    _seed_list("boys", ["Aaron"])
    record("Ramses", "boys", "Aaron", LIKE)
    assert next_name("Ramses", "boys") is None


def test_undo_restores_and_makes_name_swipable_again():
    _seed_list("boys", ["Aaron"])
    record("Ramses", "boys", "Aaron", LIKE)
    restored = undo_last("Ramses", "boys")
    assert restored == "Aaron"
    assert next_name("Ramses", "boys") == "Aaron"


def test_undo_with_no_history_returns_none():
    _seed_list("boys", ["Aaron"])
    assert undo_last("Ramses", "boys") is None


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


def test_alpha_mode_returns_first_unseen_alphabetically():
    _seed_list("boys", ["Cas", "Aaron", "Bram"])
    assert next_name("Ramses", "boys", mode=MODE_ALPHA) == "Aaron"
    record("Ramses", "boys", "Aaron", LIKE)
    assert next_name("Ramses", "boys", mode=MODE_ALPHA) == "Bram"


def test_partner_likes_mode_only_returns_partner_likes():
    _seed_list("boys", ["Aaron", "Bram", "Cas", "Dex"])
    record("Chiara", "boys", "Bram", LIKE)
    record("Chiara", "boys", "Cas", LIKE)
    record("Chiara", "boys", "Aaron", DISLIKE)  # not eligible
    # Ramses has seen nothing yet
    seen: set[str] = set()
    for _ in range(5):
        n = next_name("Ramses", "boys", mode=MODE_PARTNER_LIKES)
        assert n in {"Bram", "Cas"}
        seen.add(n)
    # Alpha order in partner-likes mode
    assert next_name("Ramses", "boys", mode=MODE_PARTNER_LIKES) == "Bram"


def test_partner_likes_mode_returns_none_when_partner_has_no_likes():
    _seed_list("boys", ["Aaron", "Bram"])
    assert next_name("Ramses", "boys", mode=MODE_PARTNER_LIKES) is None


def test_reswipe_disliked_reincludes_own_dislikes():
    _seed_list("boys", ["Aaron"])
    record("Ramses", "boys", "Aaron", DISLIKE)
    assert next_name("Ramses", "boys") is None
    assert next_name("Ramses", "boys", reswipe_disliked=True) == "Aaron"


def test_reswipe_does_not_reinclude_own_likes():
    _seed_list("boys", ["Aaron"])
    record("Ramses", "boys", "Aaron", LIKE)
    assert next_name("Ramses", "boys", reswipe_disliked=True) is None


def test_next_name_exclude_drops_names_from_pool():
    _seed_list("boys", ["Aaron", "Bram", "Cas"])
    n = next_name("Ramses", "boys", mode=MODE_ALPHA, exclude={"Aaron", "Bram"})
    assert n == "Cas"


def test_next_two_returns_distinct_current_and_lookahead():
    _seed_list("boys", ["Aaron", "Bram", "Cas"])
    current, lookahead = next_two("Ramses", "boys", mode=MODE_ALPHA)
    assert current == "Aaron"
    assert lookahead == "Bram"
    assert current != lookahead


def test_next_two_lookahead_none_when_only_one_name_left():
    _seed_list("boys", ["Aaron"])
    current, lookahead = next_two("Ramses", "boys", mode=MODE_ALPHA)
    assert current == "Aaron"
    assert lookahead is None


def test_next_two_both_none_when_list_exhausted():
    _seed_list("boys", ["Aaron"])
    record("Ramses", "boys", "Aaron", LIKE)
    current, lookahead = next_two("Ramses", "boys")
    assert current is None
    assert lookahead is None


def test_random_mode_weights_partner_likes_higher(monkeypatch):
    # Names: "L" was liked by partner, "N" is neutral, "D" was disliked by partner.
    # With weights 5 : 1 : 0.2, the empirical distribution should be dominated by L.
    _seed_list("boys", ["L", "N", "D"])
    record("Chiara", "boys", "L", LIKE)
    record("Chiara", "boys", "D", DISLIKE)

    monkeypatch.setattr(swipes_mod, "random", random.Random(42))

    counts: Counter[str] = Counter()
    for _ in range(2000):
        n = next_name("Ramses", "boys", mode=MODE_RANDOM)
        assert n is not None
        counts[n] += 1

    # L: weight 5, N: 1, D: 0.2 => sum 6.2. Expected ratios ~ 0.806 / 0.161 / 0.032.
    # Generous bounds so the test isn't flaky.
    assert counts["L"] > counts["N"] * 3
    assert counts["N"] > counts["D"] * 3
