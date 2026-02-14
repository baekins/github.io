from __future__ import annotations

import re
from urllib.parse import urlparse

URL_RE = re.compile(r"^https?://", re.IGNORECASE)
ODDS_LINE_RE = re.compile(r"^\s*(?P<name>[^:]+):\s*(?P<odd>\d+(?:\.\d+)?)\s*$")


def is_url(text: str) -> bool:
        return bool(URL_RE.match(text.strip()))


def extract_slug(input_text: str) -> tuple[str | None, str | None]:
        """Return (kind, slug) where kind in {'event','market','sports'} for polymarket URLs."""
        text = input_text.strip()
        if not is_url(text):
                    return None, None

        parsed = urlparse(text)
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) < 2:
                    return None, None

        if parts[0] in {"event", "market"} and parts[1]:
                    return parts[0], parts[1]

        # Support sports URLs: /sports/{league}/{event-slug}
        if parts[0] == "sports" and len(parts) >= 3:
                    return "event", parts[-1]

        return None, None


def parse_reference_odds(text: str) -> dict[str, float]:
        """Parse decimal odds from multi-line chat text, e.g. 'Lakers: 1.85'."""
        odds: dict[str, float] = {}
        for line in text.splitlines():
                    m = ODDS_LINE_RE.match(line)
                    if not m:
                                    continue
                                odd = float(m.group("odd"))
                    if odd > 1.0:
                                    odds[m.group("name").strip()] = odd
                            return odds
            
