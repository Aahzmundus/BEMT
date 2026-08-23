"""Small, polite ESI HTTP client.

Honours both of ESI's rate-limit systems (the global error budget behind a 420
and the per-route 429 with its Retry-After), retries transient failures, and
pages endpoints that use X-Pages.

No response cache: BEMT makes a handful of calls per refresh, entirely
user-triggered, so a cache would be complexity with nothing to buy.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

import httpx

from . import sso

log = logging.getLogger(__name__)

BASE = "https://esi.evetech.net/latest"
# CCP asks for a contactable identity in the User-Agent. Set BEMT_CONTACT in the
# environment to a real address; a neutral placeholder is used otherwise so
# nothing personal lives in the source.
_CONTACT = os.environ.get("BEMT_CONTACT", "bemt-user@example.com")
_UA = f"BEMT/0.1 (personal market tool; {_CONTACT})"

_client = httpx.Client(
    base_url=BASE,
    headers={"User-Agent": _UA, "Accept": "application/json"},
    timeout=30,
)
_guard = threading.Lock()
_pause_until = 0.0


class EsiError(RuntimeError):
    """An ESI call that failed in a way worth showing the user."""


def _respect_error_budget(resp: httpx.Response) -> None:
    global _pause_until
    remain = resp.headers.get("X-ESI-Error-Limit-Remain")
    reset = resp.headers.get("X-ESI-Error-Limit-Reset")
    if remain is not None and int(remain) <= 5:
        with _guard:
            _pause_until = time.time() + float(reset or 30)
        log.warning("ESI error budget low (%s left), pausing %ss", remain, reset)


def _request(method: str, path: str, *, character_id: int | None = None,
             params: dict[str, Any] | None = None, json_body: Any = None,
             retries: int = 3) -> httpx.Response:
    wait = _pause_until - time.time()
    if wait > 0:
        time.sleep(wait)

    headers: dict[str, str] = {}
    if character_id is not None:
        headers["Authorization"] = f"Bearer {sso.access_token(character_id)}"

    last: Exception | None = None
    for attempt in range(retries):
        try:
            resp = _client.request(method, path, params=params, json=json_body,
                                   headers=headers)
            _respect_error_budget(resp)
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                time.sleep(float(retry_after) if retry_after else float(2 ** attempt))
                continue
            if resp.status_code in (420, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as e:
            raise EsiError(
                f"ESI {method} {path} returned {e.response.status_code}"
            ) from e
        except httpx.TransportError as e:
            last = e
            time.sleep(2 ** attempt)
    raise EsiError(f"Could not reach ESI ({method} {path}): {last}")


def get_json(path: str, **kw) -> Any:
    return _request("GET", path, **kw).json()


def post_json(path: str, **kw) -> Any:
    return _request("POST", path, **kw).json()


def get_paged(path: str, **kw) -> list:
    """Every page of an X-Pages endpoint, concatenated."""
    params = dict(kw.pop("params", None) or {})
    params["page"] = 1
    first = _request("GET", path, params=params, **kw)
    items: list = first.json()
    pages = int(first.headers.get("X-Pages", 1))
    for page in range(2, pages + 1):
        params["page"] = page
        items.extend(_request("GET", path, params=params, **kw).json())
    return items
