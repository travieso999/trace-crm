"""Imported lists: every committed spreadsheet import is kept as a named, reusable group of prospects.

A list only references prospects. Renaming or deleting a list never changes or deletes a prospect, and a
prospect can be in many lists and many campaigns."""

import uuid
from pathlib import PurePath

from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from app.models import (
    Campaign,
    Contact,
    ImportBatch,
    SuppressionEntry,
    User,
    campaign_import_batches,
    import_batch_members,
)
from app.models.base import utcnow


def default_name(filename: str | None) -> str:
    stem = PurePath(filename or "").stem.replace("_", " ").replace("-", " ").strip()
    return (" ".join(w.capitalize() if w.islower() else w for w in stem.split()) or "Imported list")[:200]


def create(db: Session, *, name: str | None, filename: str | None, sha256: str, file_format: str | None,
           total_rows: int, result: dict, members: list[tuple[uuid.UUID, str, int]], user: User | None) -> ImportBatch:
    batch = ImportBatch(
        name=(name or "").strip()[:200] or default_name(filename), original_filename=(filename or "")[:300] or None,
        sha256=sha256, file_format=file_format, total_rows=total_rows, created_by=user.id if user else None,
        stats={
            "created": result["created"], "updated": result["updated"],
            "existing": sum(1 for _, outcome, _ in members if outcome == "existing"),
            "duplicate_in_file": sum(1 for r in result.get("rows", []) if r["status"] == "duplicate_in_file"),
            "invalid": result["invalid"], "suppressed": result["skipped_suppressed"],
        },
    )
    db.add(batch)
    db.flush()
    seen: set[uuid.UUID] = set()
    rows = []
    for contact_id, outcome, row_no in members:
        if contact_id not in seen:  # one membership per prospect even if two rows resolved to it
            seen.add(contact_id)
            rows.append({"batch_id": batch.id, "contact_id": contact_id, "outcome": outcome, "row_number": row_no})
    if rows:
        db.execute(insert(import_batch_members), rows)
    return batch


def member_ids(db: Session, batch_id: uuid.UUID) -> list[uuid.UUID]:
    return list(db.scalars(
        select(import_batch_members.c.contact_id)
        .join(Contact, Contact.id == import_batch_members.c.contact_id)
        .where(import_batch_members.c.batch_id == batch_id, Contact.deleted_at.is_(None))
        .order_by(import_batch_members.c.row_number)))


def link_campaign(db: Session, campaign_id: uuid.UUID, batch_id: uuid.UUID) -> None:
    exists = db.scalar(select(campaign_import_batches.c.batch_id).where(
        campaign_import_batches.c.campaign_id == campaign_id, campaign_import_batches.c.batch_id == batch_id))
    if not exists:
        db.execute(insert(campaign_import_batches).values(campaign_id=campaign_id, batch_id=batch_id, added_at=utcnow()))


def summaries(db: Session, batches: list[ImportBatch]) -> list[dict]:
    """List rows with live counts: prospects still in the CRM, how many can be emailed, linked campaigns."""
    ids = [b.id for b in batches]
    if not ids:
        return []
    m, c = import_batch_members, Contact
    live = dict(db.execute(
        select(m.c.batch_id, func.count()).join(c, c.id == m.c.contact_id)
        .where(m.c.batch_id.in_(ids), c.deleted_at.is_(None)).group_by(m.c.batch_id)).all())
    # valid: still in the CRM, not suppressed or unsubscribed, email not marked invalid
    valid = dict(db.execute(
        select(m.c.batch_id, func.count()).join(c, c.id == m.c.contact_id)
        .where(m.c.batch_id.in_(ids), c.deleted_at.is_(None), c.verification_status != "invalid",
               c.email.not_in(select(SuppressionEntry.email)))
        .group_by(m.c.batch_id)).all())
    campaigns: dict[uuid.UUID, list[dict]] = {}
    for bid, cid, name, status in db.execute(
        select(campaign_import_batches.c.batch_id, Campaign.id, Campaign.name, Campaign.status)
        .join(Campaign, Campaign.id == campaign_import_batches.c.campaign_id)
        .where(campaign_import_batches.c.batch_id.in_(ids), Campaign.deleted_at.is_(None))
        .order_by(Campaign.created_at)):
        campaigns.setdefault(bid, []).append({"id": str(cid), "name": name, "status": status})
    users = dict(db.execute(select(User.id, User.full_name).where(
        User.id.in_([b.created_by for b in batches if b.created_by]))).all()) if any(b.created_by for b in batches) else {}
    out = []
    for b in batches:
        st = b.stats or {}
        out.append({
            "id": str(b.id), "name": b.name, "original_filename": b.original_filename, "file_format": b.file_format,
            "created_at": b.created_at.isoformat(), "uploaded_by": users.get(b.created_by),
            "total_rows": b.total_rows, "contacts": live.get(b.id, 0), "valid_contacts": valid.get(b.id, 0),
            # rows that matched a prospect already in the CRM, plus repeated rows inside the file
            "duplicates": st.get("updated", 0) + st.get("existing", 0) + st.get("duplicate_in_file", 0),
            "stats": st, "campaigns": campaigns.get(b.id, []),
        })
    return out

