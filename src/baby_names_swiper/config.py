"""Runtime configuration sourced from environment variables."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

USERS: list[str] = ["Ramses", "Chiara"]

_REPO_ROOT = Path(__file__).resolve().parents[2]

DB_PATH: Path = Path(os.environ.get("DB_PATH", _REPO_ROOT / "data" / "swipes.db"))
NAMES_DIR: Path = Path(os.environ.get("NAMES_DIR", _REPO_ROOT / "data" / "names"))
UPLOAD_DIR: Path = Path(os.environ.get("UPLOAD_DIR", _REPO_ROOT / "data" / "uploads"))

COOKIE_SECRET: str = os.environ.get("COOKIE_SECRET", "dev-only-do-not-use-in-prod")
COOKIE_NAME: str = "who"

MAX_UPLOAD_BYTES: int = 1 * 1024 * 1024
MAX_NAMES_PER_LIST: int = 5000
MAX_NAME_LEN: int = 50
