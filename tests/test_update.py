"""The GitHub update watcher: version arithmetic and the cached check."""

from __future__ import annotations

import json

from bemt import db, update


def test_version_parsing_tolerates_the_usual_tag_shapes():
    assert update.parse_version("v0.1.1") == (0, 1, 1)
    assert update.parse_version("0.1.1") == (0, 1, 1)
    assert update.parse_version("V1.2") == (1, 2)
    assert update.parse_version("garbage") == ()
    assert update.parse_version("") == ()


def test_newer_means_strictly_newer():
    assert update.is_newer("v0.1.2", "0.1.1")
    assert update.is_newer("v0.2.0", "0.1.9")
    assert not update.is_newer("v0.1.1", "0.1.1")
    assert not update.is_newer("v0.1.0", "0.1.1")
    assert not update.is_newer("nonsense", "0.1.1")  # unparseable: never nag


def test_check_asks_github_once_then_serves_the_cache(env, monkeypatch):
    calls = []

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"tag_name": "v99.0.0", "html_url": "https://x/rel"}

    def fake_get(*a, **k):
        calls.append(1)
        return Resp()

    monkeypatch.setattr(update.httpx, "get", fake_get)
    first = update.check()
    second = update.check()
    assert len(calls) == 1
    assert first["update_available"] and second["update_available"]
    assert first["url"] == "https://x/rel"


def test_a_failed_check_is_cached_as_no_update(env, monkeypatch):
    """A machine without internet must not retry GitHub on every page load."""
    def boom(*a, **k):
        raise RuntimeError("offline")

    monkeypatch.setattr(update.httpx, "get", boom)
    assert update.check()["update_available"] is False
    cached = json.loads(db.get_state("update_check"))
    assert cached["latest"] == ""
