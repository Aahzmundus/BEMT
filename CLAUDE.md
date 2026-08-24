# CLAUDE.md

Guidance for Claude Code (claude.ai/code) working in this repository.

## What this is

**BEMT (Benji Eve Market Tool)** — a small, single-purpose EVE Online helper
built for one player. It reads his own sell orders and turns them into an
in-game multibuy list of what to re-buy. Local FastAPI web app on **port 8425**.

**Scope is deliberately tiny and should stay that way.** A handful of
characters (each with their own market and list), one question: *what do I
re-buy?* It never writes to EVE — no orders, no ISK, no assets moved. Resist
scope creep; the tool's value is that a non-technical user can double-click
`run.bat` and be done in ten seconds.

BEMT was built inside a larger private tool (AEMT) to inherit its hard-won EVE
and architecture learnings, then split out to its own public repo. Everything
worth carrying over is recorded below.

## Commands

```
run.bat                                       # what the end user double-clicks
.venv\Scripts\python -m bemt                  # run it (also opens the browser)
.venv\Scripts\pip install -e ".[dev]"         # dev dependencies
.venv\Scripts\python -m pytest tests/ -q      # all tests
```

Frontend files (`bemt/web/static/`) are served from disk per request with
`Cache-Control: no-store` — edit, reload, done. Python changes need a restart.

## Architecture

- **`bemt/model.py` is pure** — par math, the hangar walk, import seeding,
  multibuy formatting. All the reasoning lives here and it is fully tested
  (`tests/test_model.py`). **Keep new arithmetic here** or it becomes
  untestable.
- **`bemt/service.py`** is the I/O shell: fetch from ESI, persist, answer the
  page's questions. **`bemt/app.py`** is FastAPI wiring only.
- **`bemt/esi/`** — SSO (OAuth2 PKCE, no secret), a polite rate-limit-aware
  client, and the three reads the tool needs. No response cache: a handful of
  user-triggered calls per refresh buys nothing from one.
- **`bemt/db.py`** — SQLite. **`bemt/config.py`** — settings to `data/config.json`.
- **`bemt/tsutil.py`** — the ONE timestamp parser, always aware UTC. Don't
  hand-roll another `strptime`-with-fallback; in the parent project ~20 copies
  had already drifted into naive/aware mismatches.

## The restock model (settled — don't re-litigate)

`buy = max(0, target − listed − hangar)`, gated by a **restock threshold**:
nothing is bought until stock falls *below* `restock_threshold_pct` of the
target (default 25, strict less-than, integer math in `model.buy_qty`), and
then the buy tops back up to the FULL target. 100 = buy any deficit (the
pre-0.1.1 behaviour, and what most legacy tests pin). **Par level, not
sold-since-last-check.** A par level states the desired end state, so a skipped
week, a different PC or a restored backup can't corrupt it — it is recomputed
from the live book every time. A "what sold since last time" model must
accumulate history correctly forever to stay right, and ESI's ~30-day wallet
window would cap it anyway.

- **The target seed is `volume_total`, not `volume_remain`** — the size the
  order was *placed* at. Seeding from what's left of a half-sold order would
  silently ratchet stock levels down on every import.
- **An import only ever ADDS.** A target may have been typed by hand; an
  automatic process must never overwrite a human decision. Tested.
- **`stock` is rebuilt wholesale on every refresh** (`DELETE` then re-insert),
  so an item whose orders are all gone reads as 0 listed rather than keeping
  yesterday's number. An emptied state is real data — and "listed 0" is the
  most important row on the page.
- **Hangar counting excludes anything fitted or loaded.** `hangar_stacks()`
  walks into station containers (their contents are also flagged `Hangar`) but
  stops at anything whose children carry slot/bay flags — a rigged, loaded hull
  is in use, and counting it as shelf stock would suppress a real re-buy. A
  *packaged* hull has no children and does count, which is correct. No SDE is
  needed; the location flags carry it. Don't add an SDE download to "improve"
  this.
- **A failed hangar read degrades, it does not fail the refresh** — it reports
  `hangar_error` and the page says the list may be slightly high. Over-buying a
  little beats no list at all.

## EVE constraints (verified — don't re-derive)

- **Port 8425 is fixed.** The EVE SSO application is registered with callback
  `http://localhost:8425/callback`. Changing the port breaks the login.
- **The client id is baked into `bemt/config.py` and that is safe.** The login
  uses PKCE, so there is no client secret; the id identifies the application and
  grants nothing on its own. This is how EVE third-party apps ship.
- **Three scopes, and only three**: `esi-markets.read_character_orders.v1` (the
  feature), `esi-assets.read_assets.v1` (hangar subtraction), and
  `esi-universe.read_structures.v1`. That last one is not optional — **a player
  structure's name is NOT public**; without it a citadel market renders in the
  picker as a bare `Structure <id>` instead of its name. NPC stations resolve
  publicly.
- **ESI has no public fuzzy type search.** `/universe/ids/` is an exact,
  case-insensitive match. That is why the add box autocompletes from names the
  install has already seen and only falls back to ESI for genuinely new items;
  the game itself supplies exact names via right-click → Copy.
- **`sso.missing_scopes()`** compares a stored token's scopes against the live
  `SCOPES` list. Adding a scope later means every existing login needs a
  one-time re-login — the page surfaces that rather than letting an endpoint
  fail with a confusing 403.

## Conventions

- **Atomic config write** with a `.bak` fallback (`config.py`). A plain
  truncating write means a crash mid-save leaves unparseable JSON, and since
  `load()` runs at startup that bricks the app. This has happened for real in
  the parent project.
- **A snapshot per refresh, from day one** — `snapshots` + `snapshot_lines`,
  with `count_hangar` frozen into the row so a later settings change can't
  rewrite what an old list meant. Nothing charts it yet; history cannot be
  backfilled, so record it now.
- **No data means idle, never OK.** Before the first refresh the hero reads "—"
  and "Never refreshed", not a zero that looks like a clean bill of health.
- **A setup gap returns 409 with a `reason` code** (no login, several candidate
  markets, no orders), which the page turns into a question. It is not a 500,
  and **the market is never guessed when there is more than one candidate** —
  picking one would silently produce a wrong list for the other.
- **Nothing is hardcoded about location or character.** The market comes from
  each character's own orders via the picker.
- **Multi-character (0.1.1, schema rev 2).** `items`/`stock` are keyed by
  `(character_id, type_id)`; each character row carries its own
  `location_id`/`location_name`. `db.init()` migrates a rev-1 database in
  place, handing existing rows to the character from legacy `config.json`
  fields (kept in `Config` for exactly that) — or to character 0, adopted by
  the first login (`sso.finish_login`). Refresh loops over all characters;
  `model.merge_rows` folds per-character rows into the merged shopping list
  (quantities summed — each character still needs their share). The merged
  view is read-only when several characters contribute; editing happens in
  the per-character (separate) view.
- **Update check (`bemt/update.py`) is notify-and-link only.** It watches
  GitHub releases (cached 6h in `state`, failures cached too) and the page
  shows a banner. Never auto-download/overwrite a running install — a
  half-replaced folder bricks it for a non-technical user.

## Frontend

Vanilla JS, no framework, no build step. `i18n.js` holds **EN + HR + NL + FR**
strings and the top-right selector switches live; the choice persists
server-side. **Any new UI string must be added to all four tables.**

Verify UI changes by DOM / computed-style query against a **throwaway instance
on a spare port** — a script that repoints `bemt.paths.DB_PATH` and
`bemt.config.CONFIG_PATH` at a temp dir *before* seeding synthetic data. Never
seed fake data into the shipped `data/` dir.

## Testing

`tests/conftest.py`'s `env` fixture points the DB and config at a temp dir. Note
which name gets patched: `config.py` does `from .paths import CONFIG_PATH`, so
the binding that matters is on the **consuming** module (`bemt.config.CONFIG_PATH`),
not `bemt.paths`. Patching only the source module leaves the real config in play
— this exact mistake caused a real incident in the parent project.

Warnings are errors (`filterwarnings = ["error"]` in `pyproject.toml`). That is
deliberate: it caught a FastAPI `on_event` deprecation during the initial build.

## Releasing

The user-facing artifact is a zip attached to a GitHub release, containing
everything needed to run: the `bemt/` package, `run.bat`, `pyproject.toml`, and
both READMEs. It must unpack to a single folder the user can double-click into.
Never ship `.venv`, `data/`, `__pycache__`, or `.pytest_cache`.
