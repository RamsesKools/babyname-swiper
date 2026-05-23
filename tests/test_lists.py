from __future__ import annotations

from fastapi.testclient import TestClient

from baby_names_swiper import config
from baby_names_swiper.app import app
from baby_names_swiper.deps import sign_user
from baby_names_swiper.lists_view import build_rows
from baby_names_swiper.names import add_manual_name
from baby_names_swiper.swipes import DISLIKE, LIKE, record


def _seed_list(slug: str, names: list[str]) -> None:
    (config.NAMES_DIR / f"{slug}.csv").write_text("\n".join(names) + "\n", encoding="utf-8")


def _client_for(user: str) -> TestClient:
    client = TestClient(app)
    client.cookies.set(config.COOKIE_NAME, sign_user(user))
    return client


# --------------------------------------------------------------------------- #
#                              build_rows helper                              #
# --------------------------------------------------------------------------- #


def test_build_rows_empty_when_no_lists_selected():
    _seed_list("boys", ["Aaron"])
    rows = build_rows(
        user="Ramses",
        list_slugs=[],
        mode="alpha",
        states=["like", "dislike", "unswiped"],
    )
    assert rows == []


def test_build_rows_decorates_states():
    _seed_list("boys", ["Aaron", "Bram", "Cas"])
    record("Ramses", "boys", "Aaron", LIKE)
    record("Ramses", "boys", "Bram", DISLIKE)
    rows = build_rows(
        user="Ramses",
        list_slugs=["boys"],
        mode="alpha",
        states=["like", "dislike", "unswiped"],
    )
    states = {r.name: r.state for r in rows}
    assert states == {"Aaron": "like", "Bram": "dislike", "Cas": "unswiped"}


def test_build_rows_dedupes_union_across_lists():
    _seed_list("boys", ["Aaron", "Robin"])
    _seed_list("unisex", ["Robin", "Sam"])
    rows = build_rows(
        user="Ramses",
        list_slugs=["boys", "unisex"],
        mode="alpha",
        states=["like", "dislike", "unswiped"],
    )
    names = [r.name for r in rows]
    assert names == ["Aaron", "Robin", "Sam"]


def test_build_rows_filter_by_state_keeps_only_requested():
    _seed_list("boys", ["Aaron", "Bram", "Cas"])
    record("Ramses", "boys", "Aaron", LIKE)
    record("Ramses", "boys", "Bram", DISLIKE)
    likes_only = build_rows(
        user="Ramses",
        list_slugs=["boys"],
        mode="alpha",
        states=["like"],
    )
    assert [r.name for r in likes_only] == ["Aaron"]


def test_build_rows_random_mode_pins_manual_names_to_end():
    _seed_list("boys", ["Aaron", "Bram", "Cas"])
    add_manual_name("boys", "Zenith")
    rows = build_rows(
        user="Ramses",
        list_slugs=["boys"],
        mode="random",
        states=["like", "dislike", "unswiped"],
    )
    names = [r.name for r in rows]
    # Zenith (manual) lands at the very end of the random order
    assert names[-1] == "Zenith"
    # base names occupy the first three slots in some random order
    assert set(names[:-1]) == {"Aaron", "Bram", "Cas"}


def test_build_rows_marks_manual_names():
    _seed_list("boys", ["Aaron"])
    add_manual_name("boys", "Atlas")
    rows = build_rows(
        user="Ramses",
        list_slugs=["boys"],
        mode="alpha",
        states=["like", "dislike", "unswiped"],
    )
    by_name = {r.name: r for r in rows}
    assert by_name["Aaron"].is_manual is False
    assert by_name["Atlas"].is_manual is True
    assert by_name["Atlas"].source_list == "boys"


# --------------------------------------------------------------------------- #
#                                   routes                                    #
# --------------------------------------------------------------------------- #


def test_lists_page_renders_empty_when_nothing_selected():
    _seed_list("boys", ["Aaron"])
    r = _client_for("Ramses").get("/lists")
    assert r.status_code == 200
    assert "Select one or more lists" in r.text


def test_lists_page_lists_names_for_selected_list():
    _seed_list("boys", ["Aaron", "Bram"])
    r = _client_for("Ramses").get("/lists", params={"list": "boys", "mode": "alpha"})
    assert r.status_code == 200
    # both names appear in the rendered body
    assert "Aaron" in r.text
    assert "Bram" in r.text


def test_lists_page_filter_only_likes():
    _seed_list("boys", ["Aaron", "Bram"])
    record("Ramses", "boys", "Aaron", LIKE)
    record("Ramses", "boys", "Bram", DISLIKE)
    r = _client_for("Ramses").get(
        "/lists",
        params=[("list", "boys"), ("mode", "alpha"), ("state", "like")],
    )
    assert r.status_code == 200
    assert "Aaron" in r.text
    assert "Bram" not in r.text


def test_lists_swipe_records_like():
    _seed_list("boys", ["Aaron"])
    client = _client_for("Ramses")
    r = client.post(
        "/lists/swipe",
        data={"name": "Aaron", "list": "boys", "direction": str(LIKE)},
    )
    assert r.status_code == 200
    assert "state-like" in r.text
    # confirms the swipe was actually persisted
    from baby_names_swiper.swipes import overview  # noqa: PLC0415

    assert overview("Ramses", "boys").my_likes == ["Aaron"]


def test_lists_unswipe_clears_swipe():
    _seed_list("boys", ["Aaron"])
    record("Ramses", "boys", "Aaron", LIKE)
    client = _client_for("Ramses")
    r = client.post("/lists/unswipe", data={"name": "Aaron", "list": "boys"})
    assert r.status_code == 200
    assert "state-unswiped" in r.text
    from baby_names_swiper.swipes import overview  # noqa: PLC0415

    assert overview("Ramses", "boys").my_likes == []


def test_lists_delete_removes_manual_name_for_both_users():
    _seed_list("boys", ["Aaron"])
    add_manual_name("boys", "Atlas")
    record("Ramses", "boys", "Atlas", LIKE)
    record("Chiara", "boys", "Atlas", LIKE)

    client = _client_for("Ramses")
    r = client.post("/lists/delete", data={"name": "Atlas", "list": "boys"})
    assert r.status_code == 200
    # response is an empty body so HTMX swaps the row out
    assert r.text == ""

    from baby_names_swiper.names import load_manual_names  # noqa: PLC0415
    from baby_names_swiper.swipes import overview  # noqa: PLC0415

    assert load_manual_names("boys") == []
    assert overview("Ramses", "boys").my_likes == []
    assert overview("Chiara", "boys").my_likes == []


def test_lists_swipe_rejects_unknown_list():
    _seed_list("boys", ["Aaron"])
    r = _client_for("Ramses").post(
        "/lists/swipe",
        data={"name": "Aaron", "list": "nope", "direction": str(LIKE)},
    )
    assert r.status_code == 400


def test_lists_rows_paginates_with_next_offset():
    # Seed enough names that there's more than one page.
    big = [f"Name{i:03d}" for i in range(120)]
    _seed_list("boys", big)
    client = _client_for("Ramses")
    r = client.get("/lists", params={"list": "boys", "mode": "alpha"})
    assert r.status_code == 200
    # The infinite-scroll trigger references the next batch URL.
    assert "/lists/rows" in r.text
    assert "offset=50" in r.text

    r2 = client.get(
        "/lists/rows",
        params={"list": "boys", "mode": "alpha", "offset": "50"},
    )
    assert r2.status_code == 200
    # Page 2 starts with Name050 and references offset=100 for page 3
    assert "Name050" in r2.text
    assert "offset=100" in r2.text
