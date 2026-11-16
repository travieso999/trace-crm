"""Engagement tracking endpoints.

Public (no session):
* ``GET|HEAD /t/<token>``      tracked link: record, then 302 to the stored destination (never to a URL from the request)
* ``GET /t/o/<token>.gif``     open pixel (only present in emails while open tracking is on)
* ``POST /api/track/demo``     demo step from an allowed demo origin; always 204 so it reveals nothing

Signed in:
* ``GET /api/tracking/emails``                      Email Tracking table (Outreach permission)
* ``GET /api/tracking/engaged``                     Engaged Prospects (opportunities permission)
* ``GET /api/tracking/contacts/<id>/timeline``      a prospect's engagement history; ``raw=true`` adds every event
"""

import json
import logging
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, reader, require_perm
from app.core import permissions, ratelimit
from app.core.config import get_settings
from app.core.db import SessionLocal, get_db
from app.core.security import load_session
from app.models import Contact, TrackedLink, User
from app.services import engagement, tracking

logger = logging.getLogger("app.tracking")

public_router = APIRouter(tags=["tracking"], include_in_schema=False)
demo_router = APIRouter(prefix="/api/track", tags=["tracking"], include_in_schema=False)
router = APIRouter(prefix="/api/tracking", tags=["tracking"])

REDIRECT_HEADERS = {"Cache-Control": "no-store, private", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow"}
PER_TOKEN_PER_MINUTE = 30
DEMO_EVENTS_PER_IP_PER_MINUTE = 120
MAX_DEMO_BODY = 2048


def _hit(request: Request, db: Session) -> tracking.Hit:
    cookie = request.cookies.get(get_settings().session_cookie_name)
    return tracking.Hit(user_agent=request.headers.get("user-agent"), ip=client_ip(request), method=request.method,
                        crm_user=bool(cookie and load_session(db, cookie)))


def _not_found() -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>Link not found</title><body style='font-family:system-ui,sans-serif;max-width:420px;margin:15vh auto;"
        "padding:0 16px;color:#0f0f0d'><h1 style='font-size:20px'>This link isn't available</h1><p style='color:#66625a'>"
        "It may have been mistyped or removed. If it came from an email, reply to that email and we'll send it again.</p>",
        status_code=404, headers={**REDIRECT_HEADERS, "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})


@public_router.get("/t/o/{name}")
def open_pixel(name: str, request: Request, db: Session = Depends(get_db)):
    token = name.removesuffix(".gif")
    if ratelimit.hit(f"t:{token}", PER_TOKEN_PER_MINUTE):
        try:
            tracking.record_open(db, token, _hit(request, db))
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("open pixel not recorded")
    return Response(tracking.PIXEL_GIF, media_type="image/gif",
                    headers={"Cache-Control": "no-store, no-cache, must-revalidate, private", "X-Robots-Tag": "noindex"})


@public_router.api_route("/t/{token}", methods=["GET", "HEAD"])
def click(token: str, request: Request, db: Session = Depends(get_db)):
    destination = None
    if ratelimit.hit(f"t:{token}", PER_TOKEN_PER_MINUTE):
        try:
            destination = tracking.record_click(db, token, _hit(request, db))
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("click not recorded")
    if destination is None and tracking.TOKEN_RE.match(token):
        # over the limit or the ledger failed: the person still gets where they were going
        destination = db.scalar(select(TrackedLink.destination_url).where(
            TrackedLink.token == token, TrackedLink.kind != "open_pixel"))
    if not destination:
        return _not_found()
    return RedirectResponse(destination, status_code=302, headers=REDIRECT_HEADERS)


def _cors(origin: str | None, allowed: bool) -> dict:
    if not allowed or not origin:
        return {"Vary": "Origin"}
    return {"Access-Control-Allow-Origin": origin, "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Max-Age": "86400", "Vary": "Origin"}


def _origin_allowed(origin: str | None) -> bool:
    with SessionLocal() as db:
        return tracking.allowed_origin(db, origin)


@demo_router.options("/demo")
async def demo_preflight(request: Request):
    origin = request.headers.get("origin")
    return Response(status_code=204, headers=_cors(origin, await run_in_threadpool(_origin_allowed, origin)))


def _store_demo_event(payload: dict, user_agent: str | None) -> None:
    with SessionLocal() as db:
        try:
            if tracking.record_demo_event(db, payload, user_agent):
                db.commit()
        except Exception:
            db.rollback()
            logger.exception("demo event not recorded")


@demo_router.post("/demo")
async def demo_event(request: Request):
    """Accepts text/plain or application/json (sendBeacon sends text/plain, which needs no preflight)."""
    origin = request.headers.get("origin")
    allowed = await run_in_threadpool(_origin_allowed, origin)
    headers = _cors(origin, allowed)
    if not allowed or not ratelimit.hit(f"demo-ip:{client_ip(request)}", DEMO_EVENTS_PER_IP_PER_MINUTE):
        return Response(status_code=204, headers=headers)
    body = await request.body()
    if len(body) > MAX_DEMO_BODY:
        return Response(status_code=204, headers=headers)
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        return Response(status_code=204, headers=headers)
    if isinstance(payload, dict):
        await run_in_threadpool(_store_demo_event, payload, request.headers.get("user-agent"))
    return Response(status_code=204, headers=headers)


# --- signed-in views ------------------------------------------------------------------------------

@router.get("/emails")
def email_tracking(campaign_id: uuid.UUID | None = None, q: str | None = Query(None, max_length=200),
                   date_from: date | None = None, date_to: date | None = None,
                   clicked: str | None = Query(None, pattern="^(yes|no|untracked)$"),
                   demo: str | None = Query(None, pattern="^(clicked|visited|interacted|completed)$"),
                   sort: str = Query("sent", pattern="^(sent|last_click|clicks)$"),
                   page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                   _: User = Depends(require_perm("outreach.access")), db: Session = Depends(get_db)):
    out = engagement.email_rows(db, campaign_id=campaign_id, q=q, date_from=date_from, date_to=date_to,
                                clicked=clicked, demo=demo, sort=sort, page=page, page_size=page_size)
    out["settings"] = _public_settings(db)
    return out


@router.get("/engaged")
def engaged_prospects(campaign_id: uuid.UUID | None = None, level: str | None = Query(None, pattern="^(high|medium|low)$"),
                      owner: str | None = Query(None, max_length=40), q: str | None = Query(None, max_length=200),
                      sort: str = Query("last_engagement", pattern=f"^({'|'.join(engagement.SORTS)})$"),
                      page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                      user: User = Depends(require_perm("opportunities.view")), db: Session = Depends(get_db)):
    out = engagement.engaged(db, campaign_id=campaign_id, level=level, owner=owner, me=user, q=q, sort=sort,
                             page=page, page_size=page_size)
    out["settings"] = _public_settings(db)
    return out


@router.get("/contacts/{contact_id}/timeline")
def contact_timeline(contact_id: uuid.UUID, raw: bool = False, user: User = Depends(reader),
                     db: Session = Depends(get_db)):
    c = db.get(Contact, contact_id)
    if c is None or c.deleted_at:
        raise HTTPException(404, "Prospect not found")
    # raw events carry user agents: troubleshooting detail for people who run outreach
    return engagement.timeline(db, contact_id, raw=raw and permissions.has(user, "outreach.access"))


def _public_settings(db: Session) -> dict:
    cfg = tracking.settings_for(db)
    return {"click_tracking": cfg["click_tracking"], "open_tracking": cfg["open_tracking"],
            "demo_urls": cfg["demo_urls"], "retention_days": cfg["retention_days"]}
