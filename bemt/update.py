"""Watch GitHub for a newer release.

Notify-and-link only: the banner points at the release page and the user
replaces the folder himself. Auto-overwriting a running install on Windows is
exactly the kind of half-updated state that bricks a non-technical user's
tool, so BEMT deliberately does not try.

The check is cached in the state table for a few hours - the page asks on
every load, GitHub is only asked a handful of times a day.
"""

from __future__ import annotations

import json
import logging
import time

import httpx

from . import __version__, db
from .config import GITHUB_REPO

log = logging.getLogger(__name__)

RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"

_CACHE_KEY = "update_check"
_CACHE_TTL = 6 * 3600  # seconds between real GitHub calls


def parse_version(text: str) -> tuple[int, ...]:
    """'v0.1.1' / '0.1.1' -> (0, 1, 1). Unparseable -> () (never newer)."""
    text = (text or "").strip().lstrip("vV")
    parts = []
    for chunk in text.split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def is_newer(candidate: str, current: str) -> bool:
    cand = parse_version(candidate)
    return bool(cand) and cand > parse_version(current)


def check(force: bool = False) -> dict:
    """Latest-release info for the page. Degrades to "no update" on any error -
    an unreachable GitHub must never make the tool look broken."""
    cached = db.get_state(_CACHE_KEY)
    if cached and not force:
        try:
            data = json.loads(cached)
            if time.time() - float(data.get("checked_at") or 0) < _CACHE_TTL:
                return _shape(data)
        except (ValueError, TypeError):
            pass

    latest, url = "", RELEASES_PAGE
    try:
        resp = httpx.get(RELEASES_API, timeout=10,
                         headers={"Accept": "application/vnd.github+json",
                                  "User-Agent": f"BEMT/{__version__}"})
        resp.raise_for_status()
        body = resp.json()
        latest = body.get("tag_name") or body.get("name") or ""
        url = body.get("html_url") or RELEASES_PAGE
    except Exception as e:
        log.info("update check failed: %s", e)
        # Cache the failure too, so a machine without internet doesn't retry
        # on every page load.
    data = {"latest": latest, "url": url, "checked_at": time.time()}
    db.set_state(_CACHE_KEY, json.dumps(data))
    return _shape(data)


def _shape(data: dict) -> dict:
    latest = data.get("latest") or ""
    return {
        "current": __version__,
        "latest": latest,
        "url": data.get("url") or RELEASES_PAGE,
        "update_available": is_newer(latest, __version__),
    }
