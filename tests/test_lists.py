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


ALL_STATES = ["like", "dislike", "unswiped"]


# --------------------------------------------------------------------------- #
#                              build_rows helper                              #
# --------------------------------------------------------------------------- #


def test_build_rows_empty_when_no_lists_selected():
    _seed_list("boys", ["Aaron"])
    rows = build_rows(
        user="Ramses",
        list_slugs=[],
        order="alpha",
        states=ALL_STATES,
    )
    assert rows == []


def test_build_rows_empty_when_no_states_selected():
    _seed_list("boys", ["Aaron"])
    rows = build_rows(
        user="Ramses",
        list_slugs=["boys"],
        order="alpha",
        states=[],
    )
    assert rows == []


def test_build_rows_decorates_states():
    _seed_list("boys", ["Aaron", "Bram", "Cas"])
    record("Ramses", "boys", "Aaron", LIKE)
    record("Ramses", "boys", "Bram", DISLIKE)
    rows = build_rows(
        user="Ramses",
        list_slugs=["boys"],
        order="alpha",
        states=ALL_STATES,
    )
    states = {r.name: r.state for r in rows}
    assert states == {"Aaron": "like", "Bram": "dislike", "Cas": "unswiped"}


def test_build_rows_dedupes_union_across_lists():
    _seed_list("boys", ["Aaron", "Robin"])
    _seed_list("unisex", ["Robin", "Sam"])
    rows = build_rows(
        user="Ramses",
        list_slugs=["boys", "unisex"],
        order="alpha",
        states=ALL_STATES,
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
        order="alpha",
        states=["like"],
    )
    assert [r.name for r in likes_only] == ["Aaron"]


def test_build_rows_random_order_pins_manual_names_to_end():
    _seed_list("boys", ["Aaron", "Bram", "Cas"])
    add_manual_name("boys", "Zenith")
    rows = build_rows(
        user="Ramses",
        list_slugs=["boys"],
        order="random",
        states=ALL_STATES,
        shuffle="abcd1234",
    )
    names = [r.name for r in rows]
    assert names[-1] == "Zenith"
    assert set(names[:-1]) == {"Aaron", "Bram", "Cas"}


def test_build_rows_marks_manual_names():
    _seed_list("boys", ["Aaron"])
    add_manual_name("boys", "Atlas")
    rows = build_rows(
        user="Ramses",
        list_slugs=["boys"],
        order="alpha",
        states=ALL_STATES,
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
    # Need an explicit state filter (default is unswiped, which fits here).
    r = _client_for("Ramses").get(
        "/lists",
        params=[("list", "boys"), ("order", "alpha"), ("state", "unswiped")],
    )
    assert r.status_code == 200
    assert "Aaron" in r.text
    assert "Bram" in r.text


def test_lists_page_filter_only_likes():
    _seed_list("boys", ["Aaron", "Bram"])
    record("Ramses", "boys", "Aaron", LIKE)
    record("Ramses", "boys", "Bram", DISLIKE)
    r = _client_for("Ramses").get(
        "/lists",
        params=[("list", "boys"), ("order", "alpha"), ("state", "like")],
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
    from baby_names_swiper.swipes import overview  # noqa: PLC0415

    assert overview("Ramses", "boys").my_likes == ["Aaron"]


def test_lists_swipe_emits_match_trigger_when_match_created():
    _seed_list("boys", ["Aaron"])
    record("Chiara", "boys", "Aaron", LIKE)  # partner already liked
    client = _client_for("Ramses")
    r = client.post(
        "/lists/swipe",
        data={"name": "Aaron", "list": "boys", "direction": str(LIKE)},
    )
    assert r.status_code == 200
    trigger = r.headers.get("HX-Trigger")
    assert trigger is not None
    assert "matchCreated" in trigger
    assert "Aaron" in trigger


def test_lists_swipe_no_match_trigger_when_not_a_match():
    _seed_list("boys", ["Aaron"])
    client = _client_for("Ramses")
    r = client.post(
        "/lists/swipe",
        data={"name": "Aaron", "list": "boys", "direction": str(LIKE)},
    )
    assert r.status_code == 200
    assert "HX-Trigger" not in r.headers


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
    big = [f"Name{i:03d}" for i in range(120)]
    _seed_list("boys", big)
    client = _client_for("Ramses")
    r = client.get(
        "/lists",
        params=[("list", "boys"), ("order", "alpha"), ("state", "unswiped")],
    )
    assert r.status_code == 200
    assert "/lists/rows" in r.text
    assert "offset=50" in r.text

    r2 = client.get(
        "/lists/rows",
        params=[("list", "boys"), ("order", "alpha"), ("state", "unswiped"), ("offset", "50")],
    )
    assert r2.status_code == 200
    assert "Name050" in r2.text
    assert "offset=100" in r2.text
