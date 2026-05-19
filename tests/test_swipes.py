from __future__ import annotations

from baby_names_swiper import config
from baby_names_swiper.swipes import DISLIKE, LIKE, next_name, overview, record, undo_last


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
    record("Chiara", "boys", "Aaron", LIKE)   # match
    record("Chiara", "boys", "Dex", LIKE)     # partner only

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
