"""Public unsubscribe pages (/u/<token>). The token is an HMAC of contact id + campaign id; no session needed.

GET shows a confirmation page (link scanners must not unsubscribe people); POST unsubscribes. POST also
serves RFC 8058 one-click requests from mail clients (body ``List-Unsubscribe=One-Click``).
"""

import html
import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import unsign
from app.core.db import get_db
from app.models import Contact, EmailEvent, SuppressionEntry
from app.services import app_settings
from app.services import campaigns as campaign_service
from app.services.contacts import log_activity

router = APIRouter(prefix="/u", tags=["unsubscribe"], include_in_schema=False)

PAGE_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'",
    "Cache-Control": "no-store",
    "X-Robots-Tag": "noindex",
}


def _page(company: str, title: str, body: str, status: int = 200) -> HTMLResponse:
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
<style>body{{font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#f8f6f1;color:#0f0f0d;margin:0;
display:flex;min-height:100vh;align-items:center;justify-content:center;padding:16px}}main{{max-width:420px;
background:#fdfcf9;border:1px solid #e1ddd2;border-radius:6px;padding:28px}}h1{{font-size:20px;margin:0 0 8px}}
p{{color:#66625a;line-height:1.5}}button{{background:#ff5a33;color:#0f0f0d;border:0;border-radius:6px;padding:10px 16px;
font-size:15px;font-weight:600;cursor:pointer}}small{{color:#66625a}}</style></head>
<body><main><h1>{html.escape(title)}</h1>{body}<p><small>{html.escape(company)}</small></p></main></body></html>"""
    return HTMLResponse(doc, status_code=status, headers=PAGE_HEADERS)


def _resolve(db: Session, token: str) -> tuple[Contact | None, uuid.UUID | None]:
    payload = unsign(token, "unsubscribe")
    if not payload:
        return None, None
    contact_part, _, campaign_part = payload.partition(":")
    try:
        contact = db.get(Contact, uuid.UUID(contact_part))
        campaign_id = uuid.UUID(campaign_part) if campaign_part else None
    except ValueError:
        return None, None
    return contact, campaign_id


@router.get("/{token}", response_class=HTMLResponse)
def confirm_page(token: str, db: Session = Depends(get_db)):
    company = app_settings.get_section(db, "company").get("name") or ""
    contact, _ = _resolve(db, token)
    if contact is None:
        return _page(company, "Link not valid", "<p>This unsubscribe link is invalid or has expired. Reply to the "
                                                "email and ask to be removed and we will take care of it.</p>", 404)
    already = db.scalar(select(SuppressionEntry.id).where(SuppressionEntry.email == contact.email)) is not None
    if already:
        return _page(company, "You're unsubscribed", f"<p>{html.escape(contact.email)} will not receive further emails "
                                                     "from us.</p>")
    return _page(company, "Unsubscribe", f"<p>Stop all emails to <strong>{html.escape(contact.email)}</strong>?</p>"
                                         f'<form method="post"><button type="submit">Unsubscribe</button></form>')


@router.post("/{token}", response_class=HTMLResponse)
def unsubscribe(token: str, db: Session = Depends(get_db)):
    company = app_settings.get_section(db, "company").get("name") or ""
    contact, campaign_id = _resolve(db, token)
    if contact is None:
        return _page(company, "Link not valid", "<p>This unsubscribe link is invalid.</p>", 404)
    if not db.scalar(select(SuppressionEntry.id).where(SuppressionEntry.email == contact.email)):
        db.add(SuppressionEntry(email=contact.email, reason="unsubscribe", source="unsubscribe link"))
        campaign_service.stop_email_everywhere(db, contact.email, "unsubscribed", "unsubscribed")
        db.add(EmailEvent(type="unsubscribed", contact_id=contact.id, campaign_id=campaign_id, data={"via": "link"}))
        log_activity(db, contact.id, "unsubscribe", "Unsubscribed via link")
        db.commit()
    return _page(company, "You're unsubscribed", f"<p>{html.escape(contact.email)} will not receive further emails "
                                                 "from us. Sorry for the interruption.</p>")
