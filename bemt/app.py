"""FastAPI wiring. One page, a handful of JSON endpoints, and the SSO callback."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               PlainTextResponse, RedirectResponse)

from . import config, db, model, paths, service, update
from .esi import sso

log = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    config.load()
    yield


app = FastAPI(title="BEMT", docs_url=None, redoc_url=None, lifespan=lifespan)


def _setup_response(e: service.SetupNeeded) -> JSONResponse:
    """A setup gap is not a server error - it is the page's next question."""
    return JSONResponse(status_code=409,
                        content={"error": str(e), "reason": e.reason, **e.extra})


# ------------------------------------------------------------------ the page

def _no_store(resp: FileResponse) -> FileResponse:
    # Files are read from disk per request; tell the browser not to hold a copy
    # so an edit to app.js is one refresh away.
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/", response_class=HTMLResponse)
def index() -> FileResponse:
    return _no_store(FileResponse(paths.STATIC_DIR / "index.html"))


@app.get("/static/{name}")
def static_file(name: str) -> FileResponse:
    target = (paths.STATIC_DIR / name).resolve()
    if not target.is_file() or paths.STATIC_DIR.resolve() not in target.parents:
        raise HTTPException(status_code=404, detail="not found")
    return _no_store(FileResponse(target))


# ------------------------------------------------------------------- auth

@app.get("/auth/login")
def auth_login() -> RedirectResponse:
    cfg = config.load()
    if not cfg.esi_client_id:
        raise HTTPException(status_code=400,
                            detail="No EVE application id configured")
    return RedirectResponse(sso.begin_login(cfg.esi_client_id))


@app.get("/callback", response_class=HTMLResponse)
def auth_callback(code: str = "", state: str = "") -> HTMLResponse:
    cfg = config.load()
    try:
        who = sso.finish_login(cfg.esi_client_id, code, state)
    except Exception as e:
        log.exception("SSO callback failed")
        return HTMLResponse(
            f"<p>Login failed: {e}</p><p><a href='/'>Back to BEMT</a></p>",
            status_code=400)
    return HTMLResponse(
        "<!doctype html><meta charset='utf-8'>"
        "<meta http-equiv='refresh' content='0;url=/'>"
        f"<p>Logged in as {who['name']}. <a href='/'>Continue</a></p>")


@app.delete("/api/characters/{character_id}")
def api_remove_character(character_id: int) -> dict:
    service.remove_character(character_id)
    return service.page_state()


@app.post("/api/characters/{character_id}/location")
def api_character_location(character_id: int,
                           payload: dict = Body(...)) -> dict:
    loc = payload.get("location_id")
    if loc is None:
        raise HTTPException(status_code=400, detail="location_id required")
    service.set_character_location(character_id, int(loc),
                                   payload.get("location_name"))
    return service.page_state()


# ------------------------------------------------------------------ the data

@app.get("/api/state")
def api_state() -> dict:
    return service.page_state()


@app.post("/api/refresh")
def api_refresh():
    try:
        summary = service.refresh()
    except service.SetupNeeded as e:
        return _setup_response(e)
    except Exception as e:
        log.exception("refresh failed")
        db.set_state("last_error", str(e))
        raise HTTPException(status_code=502, detail=str(e))
    return {"summary": summary, **service.page_state()}


@app.get("/api/locations")
def api_locations(character_id: int):
    try:
        return {"locations": service.locations(character_id)}
    except service.SetupNeeded as e:
        return _setup_response(e)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/update")
def api_update() -> dict:
    return update.check()


@app.get("/api/suggest")
def api_suggest(q: str = "", limit: int = 25) -> dict:
    return {"matches": service.known_names(q, limit)}


@app.post("/api/items")
def api_add_item(payload: dict = Body(...)) -> dict:
    cid = payload.get("character_id")
    try:
        item = service.add_item(
            name=payload.get("name"),
            type_id=payload.get("type_id"),
            target_qty=int(payload.get("target_qty") or 0),
            character_id=None if cid is None else int(cid))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"item": item, **service.page_state()}


@app.patch("/api/items/{character_id}/{type_id}")
def api_update_item(character_id: int, type_id: int,
                    payload: dict = Body(...)) -> dict:
    target = payload.get("target_qty")
    service.update_item(
        character_id, type_id,
        target_qty=None if target is None else int(target),
        active=payload.get("active"))
    return service.page_state()


@app.delete("/api/items/{character_id}/{type_id}")
def api_delete_item(character_id: int, type_id: int) -> dict:
    service.remove_item(character_id, type_id)
    return service.page_state()


@app.get("/api/multibuy", response_class=PlainTextResponse)
def api_multibuy() -> str:
    return model.multibuy_text(service.current_rows())


@app.get("/api/history")
def api_history(limit: int = 30) -> dict:
    return {"snapshots": service.history(limit)}


@app.post("/api/settings")
def api_settings(payload: dict = Body(...)) -> dict:
    fields = {}
    for key in ("count_hangar", "auto_import", "merge_characters", "language",
                "esi_client_id"):
        if key in payload and payload[key] is not None:
            fields[key] = payload[key]
    if payload.get("buy_lot_size") is not None:
        fields["buy_lot_size"] = max(0, int(payload["buy_lot_size"]))
    if payload.get("restock_threshold_pct") is not None:
        # 1..100: 0 would mean "never buy anything", which is never intended.
        fields["restock_threshold_pct"] = min(
            100, max(1, int(payload["restock_threshold_pct"])))
    config.update(**fields)
    return service.page_state()
