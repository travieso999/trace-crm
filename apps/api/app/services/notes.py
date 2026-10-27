"""Opportunity notes. Editing stores the previous text as a revision (original timestamps stay); archiving hides a
note but keeps it and its history. Notes written before this existed live in the lead timeline and are shown
read-only."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CrmActivity, CrmNote, CrmNoteRevision, User
from app.models.base import utcnow
from app.services.contacts import log_activity

MAX_NOTE = 20_000


class NoteError(ValueError):
    pass


def _clean(body: str | None) -> str:
    text = (body or "").strip()
    if not text:
        raise NoteError("A note can't be empty")
    if len(text) > MAX_NOTE:
        raise NoteError(f"Notes are limited to {MAX_NOTE:,} characters")
    return text


def create(db: Session, contact_id: uuid.UUID, body: str, user: User | None) -> CrmNote:
    note = CrmNote(contact_id=contact_id, author_id=user.id if user else None, body=_clean(body))
    db.add(note)
    db.flush()
    log_activity(db, contact_id, "note", note.body[:500], user, note_id=str(note.id))
    return note


def edit(db: Session, note: CrmNote, body: str, user: User | None) -> CrmNote:
    text = _clean(body)
    if note.archived_at:
        raise NoteError("Restore the note before editing it")
    if text == note.body:
        return note
    db.add(CrmNoteRevision(note_id=note.id, body=note.body, replaced_by_id=user.id if user else None))
    note.body, note.edited_at, note.edited_by_id = text, utcnow(), user.id if user else None
    db.flush()
    log_activity(db, note.contact_id, "note_edited", f"Note edited: {text[:300]}", user, note_id=str(note.id))
    return note


def set_archived(db: Session, note: CrmNote, archived: bool, user: User | None) -> CrmNote:
    if archived and note.archived_at is None:
        note.archived_at, note.archived_by_id = utcnow(), user.id if user else None
        log_activity(db, note.contact_id, "note_archived", f"Note archived: {note.body[:300]}", user, note_id=str(note.id))
    elif not archived and note.archived_at is not None:
        note.archived_at = note.archived_by_id = None
        log_activity(db, note.contact_id, "note_restored", f"Note restored: {note.body[:300]}", user, note_id=str(note.id))
    db.flush()
    return note


def listing(db: Session, contact_id: uuid.UUID, include_archived: bool) -> list[dict]:
    stmt = select(CrmNote).where(CrmNote.contact_id == contact_id)
    if not include_archived:
        stmt = stmt.where(CrmNote.archived_at.is_(None))
    notes = list(db.scalars(stmt.order_by(CrmNote.created_at.desc())))
    revs = dict(db.execute(select(CrmNoteRevision.note_id, func.count()).where(
        CrmNoteRevision.note_id.in_([n.id for n in notes])).group_by(CrmNoteRevision.note_id)).all()) if notes else {}
    # notes added from the lead page before notes were their own records (activity rows without a note_id)
    legacy = list(db.scalars(select(CrmActivity).where(
        CrmActivity.contact_id == contact_id, CrmActivity.type == "note",
        CrmActivity.data["note_id"].astext.is_(None)).order_by(CrmActivity.created_at.desc())))
    names = _names(db, {n.author_id for n in notes} | {n.edited_by_id for n in notes} | {a.user_id for a in legacy})
    out = [note_out(n, names, revs.get(n.id, 0)) for n in notes]
    out += [{"id": f"legacy-{a.id}", "body": a.summary, "author": names.get(a.user_id), "created_at": a.created_at.isoformat(),
             "edited_at": None, "edited_by": None, "archived_at": None, "revisions": 0, "legacy": True} for a in legacy]
    return sorted(out, key=lambda n: n["created_at"], reverse=True)


def revisions(db: Session, note: CrmNote) -> list[dict]:
    revs = list(db.scalars(select(CrmNoteRevision).where(CrmNoteRevision.note_id == note.id)
                           .order_by(CrmNoteRevision.replaced_at)))
    names = _names(db, {r.replaced_by_id for r in revs})
    return [{"body": r.body, "replaced_at": r.replaced_at.isoformat(), "replaced_by": names.get(r.replaced_by_id)}
            for r in revs]


def note_out(n: CrmNote, names: dict, revision_count: int = 0) -> dict:
    return {"id": str(n.id), "body": n.body, "author": names.get(n.author_id), "created_at": n.created_at.isoformat(),
            "edited_at": n.edited_at.isoformat() if n.edited_at else None, "edited_by": names.get(n.edited_by_id),
            "archived_at": n.archived_at.isoformat() if n.archived_at else None, "revisions": revision_count,
            "legacy": False}


def _names(db: Session, ids: set) -> dict:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {u.id: (u.full_name or u.email) for u in db.scalars(select(User).where(User.id.in_(ids)))}
