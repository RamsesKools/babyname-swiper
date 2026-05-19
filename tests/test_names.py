from __future__ import annotations

from pathlib import Path

import pytest

from baby_names_swiper import config
from baby_names_swiper.names import (
    list_available_lists,
    load_names,
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
