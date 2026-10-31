"""Enrollment dry runs and per-lead personalized previews. Read-only: nothing here sends or enrolls."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Campaign, CampaignContact, Contact
from app.services import sending, video_assets
from app.services.contacts import suppressed_set
from app.services.rendering import MissingVariables, render_email, render_string

CLOSED_STAGES = ("won", "lost", "not_interested")
# Mirrors campaigns.enroll_contacts, plus missing merge fields (which the sender would block)
STATUSES = ("ready", "missing_fields", "already_enrolled", "suppressed", "invalid_email", "closed_stage", "not_found")


def missing_variables(db: Session, campaign: Campaign, contact: Contact) -> list[str]:
    vars_ = sending.contact_variables(db, contact)
    missing: set[str] = set()
    for s in campaign.steps:
        templates = [s.body] + ([s.subject] if s.position == 1 or not s.reply_in_thread else [])
        for tpl in templates:
            try:
                render_string(tpl, vars_, policy=campaign.missing_variable_policy, fallbacks=campaign.variable_fallbacks)
            except MissingVariables as exc:
                missing.update(exc.names)
    return sorted(missing)


def report(db: Session, campaign: Campaign, contact_ids: list[uuid.UUID]) -> list[dict]:
    ids = list(dict.fromkeys(contact_ids))
    contacts = {c.id: c for c in db.scalars(select(Contact).where(Contact.id.in_(ids), Contact.deleted_at.is_(None)))}
    enrolled = set(db.scalars(select(CampaignContact.contact_id).where(
        CampaignContact.campaign_id == campaign.id, CampaignContact.contact_id.in_(ids))))
    suppressed = suppressed_set(db, [c.email for c in contacts.values()])
    out = []
    for cid in ids:
        c = contacts.get(cid)
        if c is None:
            out.append({"contact_id": str(cid), "email": None, "first_name": None, "last_name": None,
                        "company_name": None, "status": "not_found", "missing": []})
            continue
        missing: list[str] = []
        if cid in enrolled:
            status = "already_enrolled"
        elif c.email in suppressed:
            status = "suppressed"
        elif c.verification_status == "invalid":
            status = "invalid_email"
        elif c.lead_status in CLOSED_STAGES:
            status = "closed_stage"
        else:
            missing = missing_variables(db, campaign, c)
            status = "missing_fields" if missing else "ready"
        merge = sending.contact_variables(db, c)
        out.append({"contact_id": str(c.id), "email": c.email, "first_name": c.first_name, "last_name": c.last_name,
                    "company_name": merge.get("company_name") or None, "lead_status": c.lead_status,
                    "status": status, "missing": missing})
    return out


def summarize(rows: list[dict]) -> dict:
    counts = dict.fromkeys(STATUSES, 0)
    for r in rows:
        counts[r["status"]] += 1
    return counts


def preview_lead(db: Session, campaign: Campaign, contact: Contact) -> dict:
    """Every step rendered for one lead, exactly as the sender would (footer + unsubscribe link included)."""
    vars_ = sending.contact_variables(db, contact)
    try:
        footer = sending.footer_text(db, sending.unsubscribe_url(contact.id, campaign.id))
        footer_problem = None
    except MissingVariables:
        footer, footer_problem = None, "The compliance footer is incomplete: set a postal address in Settings"
    steps = []
    first_subject = None
    for s in campaign.steps:
        threaded = s.position > 1 and s.reply_in_thread
        attachment = video_assets.attachment_info(s.video_asset) if s.video_attach and s.video_asset else None
        video = video_assets.video_link(s.video_asset, on_screen=True) if s.video_preview else None
        try:
            r = render_email("" if threaded else s.subject, s.body, vars_, policy=campaign.missing_variable_policy,
                             fallbacks=campaign.variable_fallbacks, footer=footer, video=video)
            subject = r.subject
            if threaded:
                base = first_subject or "(step 1 subject)"
                subject = base if base.lower().startswith("re:") else f"Re: {base}"
            if s.position == 1:
                first_subject = subject
            steps.append({"position": s.position, "delay_days": s.delay_days, "business_days": s.business_days,
                          "threaded": threaded, "ok": True, "subject": subject, "text": r.text, "html": r.html,
                          "missing": [], "attachment": attachment})
        except MissingVariables as exc:
            steps.append({"position": s.position, "delay_days": s.delay_days, "business_days": s.business_days,
                          "threaded": threaded, "ok": False, "subject": None, "text": None, "html": None,
                          "missing": exc.names, "attachment": attachment})
    [row] = report(db, campaign, [contact.id])
    return {"contact": row, "steps": steps, "footer_problem": footer_problem}
