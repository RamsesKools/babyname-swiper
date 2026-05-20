from __future__ import annotations

from pathlib import Path

import pytest

from baby_names_swiper import config
from baby_names_swiper.names import (
    add_manual_name,
    is_manual_name,
    list_available_lists,
    load_manual_names,
    load_names,
    remove_manual_name,
    sanitize_names,
    sanitize_slug,
    save_upload,
)


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_sanitize_names_strips_dedupes_sorts():
    raw = ["  Bram  ", "aaron", "Aaron", "", "x" * 200, "Cas"]
    out = sanitize_names(raw)
    assert out == ["aaron", "Bram", "Cas"]


def test_sanitize_slug_strict():
    assert sanitize_slug("My Cool Names!!") == "my-cool-names"
    assert sanitize_slug("___") == "list"
    assert sanitize_slug("kept_underscores-and-dashes") == "kept_underscores-and-dashes"


def test_load_names_from_builtin():
    _write(config.NAMES_DIR / "boys.csv", "Bram\nAaron\nAaron\n\n")
    assert load_names("boys") == ["Aaron", "Bram"]


def test_list_available_lists_orders_builtin_then_uploads():
    _write(config.NAMES_DIR / "boys.csv", "Bram\n")
    _write(config.NAMES_DIR / "girls.csv", "Anna\n")
    _write(config.UPLOAD_DIR / "custom.csv", "Atlas\n")
    lists = list_available_lists()
    slugs = [nl.slug for nl in lists]
    assert slugs == ["boys", "girls", "upload_custom"]
    assert lists[0].source == "builtin"
    assert lists[-1].source == "upload"


def test_save_upload_happy_path():
    nl = save_upload("Mijn Lijst.csv", b"Atlas\nJuno\nAtlas\n")
    assert nl.slug == "upload_mijn-lijst"
    assert nl.path.exists()
    assert load_names(nl.slug) == ["Atlas", "Juno"]


def test_save_upload_rejects_non_utf8():
    with pytest.raises(ValueError, match="UTF-8"):
        save_upload("bad.csv", b"\xff\xfe\x00")


def test_save_upload_rejects_too_large():
    too_big = b"x\n" * (config.MAX_UPLOAD_BYTES + 1)
    with pytest.raises(ValueError, match="too large"):
        save_upload("big.csv", too_big)


def test_save_upload_rejects_empty_after_sanitize():
    with pytest.raises(ValueError, match="No valid names"):
        save_upload("empty.csv", b"\n\n   \n")


# --------------------------------------------------------------------------- #
#                              manual additions                               #
# --------------------------------------------------------------------------- #


def test_add_manual_name_appends_and_merges_into_list():
    _write(config.NAMES_DIR / "boys.csv", "Bram\nAaron\n")
    added = add_manual_name("boys", "  Atlas ")
    assert added == "Atlas"
    # the manual name shows up in the merged list
    assert load_names("boys") == ["Aaron", "Atlas", "Bram"]
    assert load_manual_names("boys") == ["Atlas"]


def test_add_manual_name_rejects_duplicate_of_base_name():
    _write(config.NAMES_DIR / "boys.csv", "Bram\n")
    with pytest.raises(ValueError, match="already in this list"):
        add_manual_name("boys", "bram")  # case-insensitive clash


def test_add_manual_name_rejects_duplicate_of_manual_name():
    _write(config.NAMES_DIR / "boys.csv", "Bram\n")
    add_manual_name("boys", "Atlas")
    with pytest.raises(ValueError, match="already in this list"):
        add_manual_name("boys", "ATLAS")


def test_add_manual_name_rejects_empty():
    _write(config.NAMES_DIR / "boys.csv", "Bram\n")
    with pytest.raises(ValueError, match="Enter a name"):
        add_manual_name("boys", "   ")


def test_is_manual_name_distinguishes_manual_from_base():
    _write(config.NAMES_DIR / "boys.csv", "Bram\n")
    add_manual_name("boys", "Atlas")
    assert is_manual_name("boys", "Atlas") is True
    assert is_manual_name("boys", "atlas") is True  # case-insensitive
    assert is_manual_name("boys", "Bram") is False


def test_remove_manual_name_drops_it_from_the_list():
    _write(config.NAMES_DIR / "boys.csv", "Bram\n")
    add_manual_name("boys", "Atlas")
    add_manual_name("boys", "Juno")
    assert remove_manual_name("boys", "Atlas") is True
    assert load_manual_names("boys") == ["Juno"]
    assert load_names("boys") == ["Bram", "Juno"]


def test_remove_manual_name_returns_false_for_unknown():
    _write(config.NAMES_DIR / "boys.csv", "Bram\n")
    assert remove_manual_name("boys", "Atlas") is False


def test_remove_last_manual_name_deletes_the_file():
    _write(config.NAMES_DIR / "boys.csv", "Bram\n")
    add_manual_name("boys", "Atlas")
    remove_manual_name("boys", "Atlas")
    assert load_manual_names("boys") == []
    assert load_names("boys") == ["Bram"]
