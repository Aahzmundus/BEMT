# BEMT — Benji Eve Market Tool

*[Hrvatska verzija ovog dokumenta →](README.hr.md)*

You keep a market seeded. Things sell. BEMT tells you exactly what to re-buy, as
a list you paste straight into the game's multibuy window.

It reads your own sell orders through EVE's official API, compares them against
the stock levels you want, and hands you the difference. Nothing else. It never
places an order, never spends ISK, and never touches your account in any way — it
only reads.

---

## Install

1. **Install Python** (only if you don't have it): <https://www.python.org/downloads/>
   During the install, tick **"Add python.exe to PATH"**. This matters.
2. **Unzip the BEMT folder** anywhere you like.
3. **Double-click `run.bat`.**

The first run takes a minute while it sets itself up. After that it starts in a
couple of seconds. Your browser opens automatically at
<http://localhost:8425>.

Leave the black console window open while you use BEMT — closing it stops the
tool. Everything runs on your own PC; nothing is uploaded anywhere.

---

## First time

1. Click **Log in with EVE** in the top right and authorise your market character.
   You'll land on CCP's own login page — BEMT never sees your password.
2. If you have sell orders in more than one place, it asks **which market you're
   seeding**. Pick it.
3. Click **Refresh**.

That import is the point of the tool: every item you currently have a sell order
for is picked up automatically, and its **target** is set to the size of the
order you placed. If you listed 100 Damage Control II, the target becomes 100.
You don't type anything.

---

## Every time after that

**Refresh** → **Copy multibuy** → in EVE, open the multibuy window
(Market → Multibuy) and press **Ctrl+V** → buy.

That's the whole loop.

---

## Reading the list

| Column | What it means |
|---|---|
| **Target** | How many you want on the market when the shelf is full. Click it to change it. |
| **Listed** | How many are still up for sale right now. |
| **Hangar** | How many are already sitting in your hangar at that station. |
| **Buy** | Target − Listed − Hangar. This is what goes on the multibuy list. |

An item only appears in the buy list once its stock falls below the **restock
threshold** — a quarter of the target by default. So an order that's still 80%
full is left alone, and once it drops under 25% you buy back up to the full
target in one go. Set the threshold to 100 in Settings if you'd rather top up
every missing unit every time.

The coloured dot on the left tells you the state at a glance:

- 🔴 **red** — sold out, nothing of it left on the market
- 🟡 **amber** — running low, partly sold
- 🟢 **green** — fully stocked, nothing to do
- ⚪ **grey** — paused

Sold-out items sort to the top, because those are the ones costing you sales.

**Why targets and not "what sold since last time":** a target is a statement
about how you want the shelf to look, so it can't drift. Skip a week, use a
different PC, forget to check — the answer is still right, because it's worked
out from what's on the market *now*.

---

## Adding and removing items

- **Add:** type the name in the box at the top and press Enter. It suggests
  names it already knows; for something new, copy the exact name from the game
  (right-click the item → Copy). You can set a target at the same time.
- **Pause** an item to keep it in the list but leave it out of the buy list —
  useful for a seasonal item you're not restocking right now.
- **✕** removes it entirely. If you still have a sell order for it, the next
  refresh will import it again, which is usually what you want.

Your targets are yours. An import only ever *adds* new items — it never
overwrites a number you typed in.

---

## Several characters

You can log in more than one character: open **Settings → Add character** and
authorise the next one. Each character keeps their own item list, targets and
market.

By default everything is **merged into one big buy list** — the same item
needed by two characters becomes one line with the quantities added up, so one
shopping trip covers everyone. Untick *Merge all characters* in Settings to see
each character's list separately instead, each with its own copy button (and
that's also where you edit each character's targets).

Each character row in Settings has its own **Change market** and **Log out**.
Logging a character out removes their items with them.

---

## Settings

- **Subtract stock already in the station hangar** — on by default. Items you
  already own don't need buying again. Turn it off to plan purely off market
  orders.
- **Automatically track new items from my sell orders** — on by default. This
  is the auto-import.
- **Restock when stock drops below (% of target)** — the restock threshold
  described above. Default 25.
- **Round buy quantities up to multiples of** — set it to e.g. 100 if you like
  restocking in round lots. 0 means no rounding.
- **Merge all characters into one buy list** — see above.
- **Characters** — add another character, change a character's market, or log
  one out.
- **EN / HR / NL / FR** — the selector in the top right switches the whole
  interface between English, Croatian, Dutch and French.

When a newer BEMT is released on GitHub, a banner appears at the top of the
page with a download link. BEMT never updates itself — you download the new
zip and replace the folder when it suits you.

---

## If something goes wrong

**"Log in with your EVE character"** — the login expired or was never done.
Click the link and log in again.

**"This version needs extra EVE permissions"** — a newer BEMT asks for one more
permission. Log in once more and it clears.

**The browser doesn't open, or the page won't load** — open
<http://localhost:8425> yourself. If it says the port is in use, BEMT is
probably already running in another window.

**"Python was not found"** — install Python and make sure you ticked *Add
python.exe to PATH*, then run `run.bat` again.

**Numbers look one refresh out of date** — press Refresh. BEMT only ever looks
at EVE when you ask it to.

**Starting over** — delete the `data` folder inside the BEMT folder. That wipes
your targets and history, and BEMT starts fresh at the next launch.

---

## Is this safe?

Yes, and here's exactly why:

- You log in on **CCP's own website**. BEMT never sees your password.
- It asks for three read-only permissions: your market orders, your assets, and
  the name of the station you trade at. **There is no permission here that can
  buy, sell, move, or spend anything.**
- The login token is stored only on your own PC, in `data/bemt.db`. Nothing
  leaves your machine except the requests to EVE's own API.
- You can revoke access at any time at
  <https://community.eveonline.com/support/third-party-applications/>.

---

## For whoever set this up

The EVE application is registered with callback `http://localhost:8425/callback`
and these three scopes:

```
esi-markets.read_character_orders.v1
esi-assets.read_assets.v1
esi-universe.read_structures.v1
```

The client id is baked into `bemt/config.py`. That's safe: the login uses PKCE,
so there is no client secret to leak, and the id identifies the app without
granting anything on its own. The port is fixed at 8425 because the callback URL
is registered against it — changing the port breaks the login.

Developer notes:

```bash
.venv\Scripts\python -m bemt                    # run it
.venv\Scripts\pip install -e ".[dev]"           # dev dependencies
.venv\Scripts\python -m pytest tests/ -q        # tests
```

The arithmetic lives in `bemt/model.py` and is pure — no I/O, fully tested.
`bemt/service.py` is the I/O shell around it. Every refresh writes a snapshot
(totals and per-item lines) to `snapshots`/`snapshot_lines`, because history
can't be backfilled later.
