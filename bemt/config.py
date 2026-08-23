"""Settings, persisted to data/config.json.

Written atomically: a plain truncating write means a crash mid-save leaves
unparseable JSON, and since load() runs at startup that bricks the app. The
previous contents are kept as config.json.bak and recovered from automatically.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading

from pydantic import BaseModel

from .paths import CONFIG_PATH, ensure_data_dir

log = logging.getLogger(__name__)

BACKUP_PATH = CONFIG_PATH.with_suffix(".json.bak")

#: Fixed: the EVE SSO application is registered with this exact callback URL,
#: so the port is not a setting. Changing it breaks the login.
PORT = 8425
CALLBACK_URL = f"http://localhost:{PORT}/callback"

#: Baked-in EVE SSO client id for the registered "BEMT" application. PKCE means
#: there is no client secret, so shipping this is safe - it identifies the
#: application and grants nothing on its own. Overridable in Settings if the
#: app ever has to be re-registered.
DEFAULT_CLIENT_ID = "d5d3e09c1d0e47649cccb6ea5d2cefc8"


class Config(BaseModel):
    esi_client_id: str = DEFAULT_CLIENT_ID

    # Whose orders we read. Set by the SSO login.
    character_id: int | None = None
    character_name: str = ""

    # The one market being seeded. Picked from the character's own orders on
    # first refresh (or in Settings); never hardcoded.
    location_id: int | None = None
    location_name: str = ""

    #: Subtract stock sitting in the station hangar from what needs buying.
    #: Needs the assets scope; turn it off to plan purely off market orders.
    count_hangar: bool = True

    #: Auto-add any sell-order item that isn't tracked yet, with its target
    #: seeded from the order's original size. This is the headline feature.
    auto_import: bool = True

    #: Round buy quantities up to a multiple of this (0 = off). Handy when a
    #: seeder likes to restock in fixed lot sizes.
    buy_lot_size: int = 0

    #: UI language: "en" or "hr".
    language: str = "en"


_lock = threading.Lock()
_cached: Config | None = None


def _read_or_recover() -> dict:
    """Parse config.json, falling back to the .bak if it is unreadable.

    A corrupt file is kept as .corrupt rather than discarded - the backup may
    predate edits worth rescuing by hand.
    """
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.error("config.json unreadable (%s); trying %s", e, BACKUP_PATH.name)
        try:
            CONFIG_PATH.replace(CONFIG_PATH.with_suffix(".json.corrupt"))
        except OSError:
            pass
        if BACKUP_PATH.exists():
            try:
                return json.loads(BACKUP_PATH.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                log.error("backup config also unreadable; starting from defaults")
    return {}


def load() -> Config:
    global _cached
    with _lock:
        if _cached is None:
            _cached = Config(**_read_or_recover())
        return _cached


def save(cfg: Config) -> Config:
    global _cached
    with _lock:
        ensure_data_dir()
        payload = cfg.model_dump_json(indent=2)
        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        if CONFIG_PATH.exists():
            try:
                shutil.copy2(CONFIG_PATH, BACKUP_PATH)
            except OSError as e:  # a missing backup never fails a save
                log.warning("could not refresh %s: %s", BACKUP_PATH.name, e)
        os.replace(tmp, CONFIG_PATH)
        _cached = cfg
        return cfg


def update(**fields) -> Config:
    """Patch and persist individual settings."""
    cfg = load()
    return save(cfg.model_copy(update=fields))


def reset_cache() -> None:
    """Forget the in-memory copy - used by tests that repoint CONFIG_PATH."""
    global _cached
    with _lock:
        _cached = None
