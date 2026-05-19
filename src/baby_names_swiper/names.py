"""Name-list discovery, loading, and custom-CSV ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from baby_names_swiper.config import (
    MAX_NAME_LEN,
    MAX_NAMES_PER_LIST,
    MAX_UPLOAD_BYTES,
    NAMES_DIR,
    UPLOAD_DIR,
)

_SLUG_RE = re.compile(r"[^a-z0-9_-]+")
_BUILTIN_LABELS = {
    "boys": "Boys (jongensnamen)",
    "girls": "Girls (meisjesnamen)",
}


@dataclass(frozen=True)
class NameList:
    slug: str
    label: str
    path: Path
    source: str  # "builtin" | "upload"


def _label_for(slug: str, source: str) -> str:
    if source == "builtin" and slug in _BUILTIN_LABELS:
        return _BUILTIN_LABELS[slug]
    return slug.replace("_", " ").replace("-", " ").title()


def list_available_lists() -> list[NameList]:
    """Return all CSV-backed name lists, builtins first then uploads."""
    lists: list[NameList] = []
    for path in sorted(NAMES_DIR.glob("*.csv")):
        slug = path.stem
        lists.append(
            NameList(slug=slug, label=_label_for(slug, "builtin"), path=path, source="builtin")
        )
    if UPLOAD_DIR.exists():
        for path in sorted(UPLOAD_DIR.glob("*.csv")):
            slug = f"upload_{path.stem}"
            lists.append(
                NameList(
                    slug=slug,
                    label=_label_for(path.stem, "upload"),
                    path=path,
                    source="upload",
                )
            )
    return lists


def get_list(slug: str) -> NameList | None:
    for nl in list_available_lists():
        if nl.slug == slug:
            return nl
    return None


def load_names(slug: str) -> list[str]:
    """Load all names from a list as an alphabetically sorted, deduped list."""
    nl = get_list(slug)
    if nl is None:
        return []
    raw = nl.path.read_text(encoding="utf-8")
    return sanitize_names(raw.splitlines())


def sanitize_names(raw: list[str]) -> list[str]:
    """Strip, drop empties, enforce length cap, dedupe case-insensitively, sort."""
    seen_lower: set[str] = set()
    out: list[str] = []
    for line in raw:
        name = line.strip()
        if not name or len(name) > MAX_NAME_LEN:
            continue
        key = name.casefold()
        if key in seen_lower:
            continue
        seen_lower.add(key)
        out.append(name)
        if len(out) >= MAX_NAMES_PER_LIST:
            break
    out.sort(key=str.casefold)
    return out


def sanitize_slug(value: str) -> str:
    """Force a slug into [a-z0-9_-]+, collapsing other chars to dashes."""
    lowered = value.strip().lower().replace(" ", "-")
    cleaned = _SLUG_RE.sub("-", lowered).strip("-_")
    return cleaned or "list"


def save_upload(filename: str, raw_bytes: bytes) -> NameList:
    """Validate and store an uploaded CSV. Returns the resulting NameList."""
    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        msg = f"File too large (max {MAX_UPLOAD_BYTES} bytes)"
        raise ValueError(msg)
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        msg = "File must be UTF-8 text"
        raise ValueError(msg) from exc

    names = sanitize_names(text.splitlines())
    if not names:
        msg = "No valid names found in upload"
        raise ValueError(msg)

    stem = sanitize_slug(Path(filename).stem)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    out_path = UPLOAD_DIR / f"{stem}.csv"
    out_path.write_text("\n".join(names) + "\n", encoding="utf-8")

    return NameList(
        slug=f"upload_{stem}",
        label=_label_for(stem, "upload"),
        path=out_path,
        source="upload",
    )
