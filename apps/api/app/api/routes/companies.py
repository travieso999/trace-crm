import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import audit, reader, writer
from app.core.db import get_db
from app.models import Company, Contact, User, WebsiteAudit
from app.models.base import utcnow
from app.schemas.common import Message, Page
from app.schemas.contacts import CompanyBase, CompanyCreate, CompanyOut
from app.services.normalize import domain_of, guess_timezone, normalize_state, normalize_website

router = APIRouter(prefix="/api/companies", tags=["companies"])

SORTS = {"name": Company.name, "state": Company.state, "created_at": Company.created_at, "updated_at": Company.updated_at}


def _out(db: Session, companies: list[Company]) -> list[CompanyOut]:
    ids = [c.id for c in companies]
    counts = dict(db.execute(
        select(Contact.company_id, func.count()).where(Contact.company_id.in_(ids), Contact.deleted_at.is_(None))
        .group_by(Contact.company_id)
    ).all()) if ids else {}
    audits: dict = {}
    if ids:
        sub = (select(WebsiteAudit).where(WebsiteAudit.company_id.in_(ids))
               .order_by(WebsiteAudit.company_id, WebsiteAudit.created_at.desc())
               .distinct(WebsiteAudit.company_id))
        audits = {a.company_id: a for a in db.scalars(sub)}
    out = []
    for c in companies:
        o = CompanyOut.model_validate(c)
        o.contact_count = counts.get(c.id, 0)
        a = audits.get(c.id)
        if a:
            o.latest_audit = {"id": str(a.id), "status": a.status, "summary": a.summary,
                              "completed_at": a.completed_at.isoformat() if a.completed_at else None}
        out.append(o)
    return out


def _apply(company: Company, data: dict) -> None:
    for f in ("name", "industry", "city", "notes"):
        if f in data:
            setattr(company, f, (data[f] or "").strip() or None if f != "name" else data[f].strip())
    if "website" in data:
        company.website = normalize_website(data["website"])
        company.domain = domain_of(company.website)
    if "state" in data:
        company.state = normalize_state(data["state"])
    if "timezone" in data:
        company.timezone = data["timezone"] or None
    if not company.timezone and company.state:
        company.timezone = guess_timezone(company.state)


@router.get("", response_model=Page[CompanyOut])
def list_companies(
    q: str | None = None,
    state: str | None = None,
    industry: str | None = None,
    website_status: str | None = None,
    sort: str = "name",
    order: str = Query("asc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _: User = Depends(reader),
    db: Session = Depends(get_db),
):
    stmt = select(Company).where(Company.deleted_at.is_(None))
    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(Company.name).like(like), func.lower(Company.domain).like(like),
                              func.lower(Company.city).like(like)))
    if state:
        stmt = stmt.where(Company.state.in_([s.upper() for s in state.split(",")]))
    if industry:
        stmt = stmt.where(func.lower(Company.industry) == industry.lower())
    if website_status:
        stmt = stmt.where(Company.website_status == website_status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    col = SORTS.get(sort, Company.name)
    stmt = stmt.order_by(col.desc().nulls_last() if order == "desc" else col.asc().nulls_last(), Company.id)
    items = list(db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)))
    return Page(items=_out(db, items), total=total, page=page, page_size=page_size)


@router.post("", response_model=CompanyOut, status_code=201)
def create_company(body: CompanyCreate, request: Request, user: User = Depends(writer), db: Session = Depends(get_db)):
    company = Company(name=body.name.strip())
    _apply(company, body.model_dump(exclude_unset=True))
    if company.domain and db.scalar(select(Company).where(Company.domain == company.domain, Company.deleted_at.is_(None))):
        raise HTTPException(409, "A company with this website already exists")
    db.add(company)
    db.flush()
    audit(db, user, "company.created", "company", company.id, request)
    db.commit()
    return _out(db, [company])[0]


@router.get("/{company_id}", response_model=CompanyOut)
def get_company(company_id: uuid.UUID, _: User = Depends(reader), db: Session = Depends(get_db)):
    c = db.get(Company, company_id)
    if not c or c.deleted_at:
        raise HTTPException(404, "Company not found")
    return _out(db, [c])[0]


@router.patch("/{company_id}", response_model=CompanyOut)
def update_company(company_id: uuid.UUID, body: CompanyBase, request: Request, user: User = Depends(writer),
                   db: Session = Depends(get_db)):
    c = db.get(Company, company_id)
    if not c or c.deleted_at:
        raise HTTPException(404, "Company not found")
    data = body.model_dump(exclude_unset=True)
    if "name" in data and not (data["name"] or "").strip():
        raise HTTPException(422, "Name cannot be empty")
    _apply(c, data)
    audit(db, user, "company.updated", "company", c.id, request, fields=sorted(data))
    db.commit()
    return _out(db, [c])[0]


@router.delete("/{company_id}", response_model=Message)
def delete_company(company_id: uuid.UUID, request: Request, user: User = Depends(writer), db: Session = Depends(get_db)):
    c = db.get(Company, company_id)
    if not c or c.deleted_at:
        raise HTTPException(404, "Company not found")
    c.deleted_at = utcnow()
    # contacts remain; unlink them from the deleted company
    for contact in db.scalars(select(Contact).where(Contact.company_id == c.id)):
        contact.company_id = None
    audit(db, user, "company.deleted", "company", c.id, request)
    db.commit()
    return Message(message="Company deleted")


@router.get("/{company_id}/contacts")
def company_contacts(company_id: uuid.UUID, _: User = Depends(reader), db: Session = Depends(get_db)):
    rows = db.scalars(select(Contact).where(Contact.company_id == company_id, Contact.deleted_at.is_(None))
                      .order_by(Contact.last_name).limit(500))
    return [{"id": str(c.id), "email": c.email, "first_name": c.first_name, "last_name": c.last_name,
             "job_title": c.job_title, "lead_status": c.lead_status} for c in rows]
