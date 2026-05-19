"""One-shot scraper for studiopoppy.nl name lists.

Run with:  uv run task scrape

Pulls the boys (jongensnamen) and girls (meisjesnamen) pages, extracts every
name from the single-column table on each page, and writes one CSV per list
into data/names/.
"""

from __future__ import annotations

from pathlib import Path
import sys

from bs4 import BeautifulSoup
import httpx

URLS = {
    "boys": "https://studiopoppy.nl/jongensnamen",
    "girls": "https://studiopoppy.nl/meisjesnamen",
}

OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "names"
USER_AGENT = "baby-names-swiper/0.1 (+personal use)"
SANITY_MIN = 500
MAX_NAME_LEN = 50


def parse_names(html: str) -> list[str]:
    """Extract names from the page's content table.

    The studiopoppy page renders names as one-column rows: each <tr> contains
    a single <td> with the name as its text. We grab every <td> with that
    centered-baseline style and filter out anything that doesn't look like a
    single name token.
    """
    soup = BeautifulSoup(html, "html.parser")
    cells = soup.select('td[style*="text-align:center"][style*="vertical-align:baseline"]')
    names: set[str] = set()
    for cell in cells:
        text = cell.get_text(strip=True)
        if not text:
            continue
        if len(text) > MAX_NAME_LEN:
            continue
        # Reject obvious non-names: anything with digits or HTML escapes left.
        if any(ch.isdigit() for ch in text):
            continue
        names.add(text)
    return sorted(names)


def fetch(url: str) -> str:
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0) as client:
        resp = client.get(url, follow_redirects=True)
        resp.raise_for_status()
        return resp.text


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    exit_code = 0
    for slug, url in URLS.items():
        print(f"[{slug}] fetching {url}")
        html = fetch(url)
        names = parse_names(html)
        print(f"[{slug}] parsed {len(names)} names")
        if len(names) < SANITY_MIN:
            print(
                f"[{slug}] ERROR: only {len(names)} names found, expected >= {SANITY_MIN}. "
                f"Page structure may have changed.",
                file=sys.stderr,
            )
            exit_code = 1
            continue
        out_path = OUT_DIR / f"{slug}.csv"
        out_path.write_text("\n".join(names) + "\n", encoding="utf-8")
        print(f"[{slug}] wrote {out_path}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
