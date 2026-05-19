"""Test fixtures: isolated names + uploads dirs and a fresh SQLite per test."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from baby_names_swiper import config, db


@pytest.fixture(autouse=True)
def _isolate_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    names_dir = tmp_path / "names"
    uploads_dir = tmp_path / "uploads"
    names_dir.mkdir()
    uploads_dir.mkdir()
    monkeypatch.setattr(config, "NAMES_DIR", names_dir)
    monkeypatch.setattr(config, "UPLOAD_DIR", uploads_dir)
    db_path = tmp_path / "swipes.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    db.reset_for_tests(db_path)
    # also patch the names module references that were bound at import time
    from baby_names_swiper import names as names_mod  # noqa: PLC0415

    monkeypatch.setattr(names_mod, "NAMES_DIR", names_dir)
    monkeypatch.setattr(names_mod, "UPLOAD_DIR", uploads_dir)
    yield
