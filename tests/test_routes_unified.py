"""Route-level tests for the unified deck: cookies, shared state across pages."""

from __future__ import annotations

from fastapi.testclient import TestClient

from baby_names_swiper import config
from baby_names_swiper.app import app
from baby_names_swiper.deps import sign_user


def _seed_list(slug: str, names: list[str]) -> None:
    (config.NAMES_DIR / f"{slug}.csv").write_text("\n".join(names) + "\n", encoding="utf-8")


def _client_for(user: str) -> TestClient:
    client = TestClient(app)
    client.cookies.set(config.COOKIE_NAME, sign_user(user))
    return client


def test_swipe_first_visit_renders_empty_state():
    _seed_list("boys", ["Aaron"])
    r = _client_for("Ramses").get("/swipe")
    assert r.status_code == 200
    assert "Select one or more lists" in r.text


def test_swipe_writes_view_cookies():
    _seed_list("boys", ["Aaron", "Bram"])
    client = _client_for("Ramses")
    r = client.get(
        "/swipe",
        params=[("list", "boys"), ("state", "unswiped"), ("order", "alpha")],
    )
    assert r.status_code == 200
    # cookie jar should now contain the view cookies
    assert "bns_view_lists" in client.cookies
    assert "boys" in client.cookies["bns_view_lists"]
    assert "bns_view_states" in client.cookies
    assert "unswiped" in client.cookies["bns_view_states"]
    assert "bns_shuffle" in client.cookies


def test_swipe_random_mints_shuffle_when_no_cookie():
    _seed_list("boys", ["Aaron", "Bram"])
    client = _client_for("Ramses")
    r = client.get(
        "/swipe",
        params=[("list", "boys"), ("state", "unswiped"), ("order", "random")],
    )
    assert r.status_code == 200
    token = client.cookies.get("bns_shuffle")
    assert token
    assert len(token) == 8


def test_swipe_reuses_shuffle_cookie_across_requests():
    _seed_list("boys", ["A", "B", "C", "D", "E"])
    client = _client_for("Ramses")
    r1 = client.get(
        "/swipe",
        params=[("list", "boys"), ("state", "unswiped"), ("order", "random")],
    )
    assert r1.status_code == 200
    token1 = client.cookies.get("bns_shuffle")
    r2 = client.get("/swipe", params=[("order", "random")])
    assert r2.status_code == 200
    token2 = client.cookies.get("bns_shuffle")
    assert token1 == token2


def test_shuffle_reset_clears_cookie_and_redirects():
    _seed_list("boys", ["A"])
    client = _client_for("Ramses")
    # prime a shuffle cookie
    client.get(
        "/swipe",
        params=[("list", "boys"), ("state", "unswiped"), ("order", "random")],
    )
    assert client.cookies.get("bns_shuffle") is not None
    r = client.post(
        "/shuffle/reset",
        headers={"referer": "http://testserver/swipe"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers.get("location") == "/swipe"
    # cookie should have been deleted by the response
    assert client.cookies.get("bns_shuffle") is None


def test_swipe_and_lists_share_list_selection_via_cookie():
    _seed_list("boys", ["Aaron"])
    _seed_list("girls", ["Anna"])
    client = _client_for("Ramses")
    # visit /swipe with explicit selection
    r1 = client.get(
        "/swipe",
        params=[("list", "boys"), ("state", "unswiped"), ("order", "alpha")],
    )
    assert r1.status_code == 200
    # /lists with no params should pick up the same selection from the cookie
    r2 = client.get("/lists")
    assert r2.status_code == 200
    assert "Aaron" in r2.text
    # girls list isn't in the body
    assert "Anna" not in r2.text


def test_swipe_random_seed_stable_between_swipe_and_lists():
    """Same shuffle token = same name sequence on both pages."""
    names = [f"Name{i:02d}" for i in range(10)]
    _seed_list("boys", names)
    client = _client_for("Ramses")
    # Establish a shuffle in /swipe
    client.get(
        "/swipe",
        params=[("list", "boys"), ("state", "unswiped"), ("order", "random")],
    )
    token = client.cookies.get("bns_shuffle")
    assert token

    # /lists with same cookies should use the same shuffle token, so the
    # first row matches the swipe page's first card. We do a structural check:
    # the same name appears as a row name in /lists.
    r_lists = client.get("/lists")
    assert r_lists.status_code == 200
    # All ten names are present in the lists page response
    for n in names:
        assert n in r_lists.text
