"""EVE SSO OAuth2 with PKCE (native app, no client secret).

/auth/login builds an authorize URL and remembers the PKCE verifier by state;
the SSO redirects back to /callback, which trades the code for tokens and
stores the character.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time

import httpx

from .. import config, db

AUTHORIZE_URL = "https://login.eveonline.com/v2/oauth/authorize"
TOKEN_URL = "https://login.eveonline.com/v2/oauth/token"

#: Exactly what BEMT uses - nothing speculative. Every one is read-only on
#: Benji's own character, and none of them can act on another player.
SCOPES = [
    # his own open market orders: the whole point of the tool
    "esi-markets.read_character_orders.v1",
    # station hangar stock, so items he already holds aren't re-bought
    "esi-assets.read_assets.v1",
    # resolve a player structure's id to its name for the market picker. NPC
    # stations are public; citadels are not, and without this a Sotiyo market
    # would render as a bare number.
    "esi-universe.read_structures.v1",
]

_pending: dict[str, str] = {}  # state -> code_verifier


def scope_string() -> str:
    return " ".join(SCOPES)


def begin_login(client_id: str) -> str:
    """Return the SSO authorize URL to send the browser to."""
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    state = secrets.token_urlsafe(16)
    _pending[state] = verifier
    params = httpx.QueryParams(
        response_type="code",
        redirect_uri=config.CALLBACK_URL,
        client_id=client_id,
        scope=scope_string(),
        state=state,
        code_challenge=challenge,
        code_challenge_method="S256",
    )
    return f"{AUTHORIZE_URL}?{params}"


def finish_login(client_id: str, code: str, state: str) -> dict:
    """Exchange the auth code, decode the character, store the tokens."""
    verifier = _pending.pop(state, None)
    if verifier is None:
        raise ValueError("Unknown or expired login - start the login again")
    resp = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "code_verifier": verifier,
        },
        timeout=30,
    )
    resp.raise_for_status()
    tok = resp.json()
    char_id, name = decode_character(tok["access_token"])
    c = db.conn()
    with c:
        c.execute(
            """INSERT INTO characters
                   (character_id, name, refresh_token, access_token,
                    access_expires, scopes)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(character_id) DO UPDATE SET
                   name=excluded.name,
                   refresh_token=excluded.refresh_token,
                   access_token=excluded.access_token,
                   access_expires=excluded.access_expires,
                   scopes=excluded.scopes""",
            (char_id, name, tok["refresh_token"], tok["access_token"],
             time.time() + tok["expires_in"] - 60, scope_string()),
        )
        # A pre-0.1.1 database migrated while logged out leaves its items on
        # character 0; the first character to log in adopts them, so hand-tuned
        # targets survive the upgrade.
        only_one = c.execute("SELECT COUNT(*) AS n FROM characters"
                             ).fetchone()["n"] == 1
        if only_one:
            for table in ("items", "stock"):
                c.execute(f"UPDATE OR IGNORE {table} SET character_id=? "
                          "WHERE character_id=0", (char_id,))
                c.execute(f"DELETE FROM {table} WHERE character_id=0")
    return {"character_id": char_id, "name": name}


def access_token(character_id: int) -> str:
    """Current access token, refreshing it if it has expired."""
    row = db.conn().execute(
        "SELECT * FROM characters WHERE character_id=?", (character_id,)
    ).fetchone()
    if row is None:
        raise ValueError("Not logged in - use the Log in button first")
    if row["access_token"] and (row["access_expires"] or 0) > time.time():
        return row["access_token"]

    resp = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": row["refresh_token"],
            "client_id": config.load().esi_client_id,
        },
        timeout=30,
    )
    resp.raise_for_status()
    tok = resp.json()
    c = db.conn()
    with c:
        c.execute(
            "UPDATE characters SET refresh_token=?, access_token=?, "
            "access_expires=? WHERE character_id=?",
            (tok.get("refresh_token", row["refresh_token"]), tok["access_token"],
             time.time() + tok["expires_in"] - 60, character_id),
        )
    return tok["access_token"]


def missing_scopes(character_id: int) -> list[str]:
    """Scopes this tool now needs that the stored token doesn't carry.

    Adding a scope later means a one-time re-login; the UI surfaces that
    instead of letting an endpoint fail with a confusing 403.
    """
    row = db.conn().execute(
        "SELECT scopes FROM characters WHERE character_id=?", (character_id,)
    ).fetchone()
    if row is None:
        return list(SCOPES)
    have = set((row["scopes"] or "").split())
    return [s for s in SCOPES if s not in have]


def logout(character_id: int) -> None:
    c = db.conn()
    with c:
        c.execute("DELETE FROM characters WHERE character_id=?", (character_id,))


def decode_character(jwt: str) -> tuple[int, str]:
    """Character id + name from the SSO JWT payload. No signature check: the
    token came straight from login.eveonline.com over TLS."""
    payload = jwt.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    claims = json.loads(base64.urlsafe_b64decode(payload))
    char_id = int(claims["sub"].split(":")[-1])  # "CHARACTER:EVE:12345"
    return char_id, claims.get("name", str(char_id))
