"""Imported lists (one per committed spreadsheet upload). Prospects themselves live in /api/contacts."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import audit, reader, writer
from app.core.db import get_db
from app.models import Contact, ImportBatch, User
from app.models.base import utcnow
from app.schemas.common import Message
from app.services import import_lists
from app.services.audience import AudienceFilter, contacts_query
from app.services.csv_import import export_contacts_csv

router = APIRouter(prefix="/api/imports", tags=["imported lists"])


class RenameIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


def _get(db: Session, batch_id: uuid.UUID) -> ImportBatch:
    b = db.get(ImportBatch, batch_id)
    if b is None or b.deleted_at:
        raise HTTPException(404, "Imported list not found")
    return b


@router.get("")
def list_imports(_: User = Depends(reader), db: Session = Depends(get_db)):
    batches = list(db.scalars(select(ImportBatch).where(ImportBatch.deleted_at.is_(None))
                              .order_by(ImportBatch.created_at.desc())))
    return import_lists.summaries(db, batches)


@router.get("/{batch_id}")
def get_import(batch_id: uuid.UUID, _: User = Depends(reader), db: Session = Depends(get_db)):
    return import_lists.summaries(db, [_get(db, batch_id)])[0]


@router.patch("/{batch_id}")
def rename_import(batch_id: uuid.UUID, body: RenameIn, request: Request, user: User = Depends(writer),
                  db: Session = Depends(get_db)):
    b = _get(db, batch_id)
    old, b.name = b.name, body.name.strip()
    audit(db, user, "import_list.renamed", "import_batch", b.id, request, old=old, new=b.name)
    db.commit()
    return import_lists.summaries(db, [b])[0]


@router.delete("/{batch_id}", response_model=Message)
def delete_import(batch_id: uuid.UUID, request: Request, user: User = Depends(writer), db: Session = Depends(get_db)):
    """Removes the list only. Its prospects, their campaigns and all email history are kept."""
    b = _get(db, batch_id)
    b.deleted_at = utcnow()
    audit(db, user, "import_list.deleted", "import_batch", b.id, request, name=b.name)
    db.commit()
    return Message(message=f"List “{b.name}” deleted. Its prospects are still in Prospects.")


@router.get("/{batch_id}/export")
def export_import(batch_id: uuid.UUID, request: Request, user: User = Depends(writer), db: Session = Depends(get_db)):
    b = _get(db, batch_id)
    contacts = list(db.scalars(contacts_query(AudienceFilter(batch_id=b.id)).order_by(Contact.email)))
    audit(db, user, "import_list.exported", "import_batch", b.id, request, count=len(contacts))
    db.commit()
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in b.name.lower()).strip("-")[:60] or "list"
    return Response(export_contacts_csv(contacts), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{safe}-{utcnow():%Y%m%d}.csv"'})
