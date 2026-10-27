import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Company, Contact, CrmActivity, SuppressionEntry, Tag, User
from app.services.normalize import (
    clean,
    domain_of,
    guess_timezone,
    normalize_email,
    normalize_state,
    normalize_website,
)

TEXT_FIELDS = {
    "first_name": 120, "last_name": 120, "company_name": 300, "phone": 60, "city": 120, "industry": 120,
    "job_title": 200, "linkedin_url": 500, "notes": 20000, "source": 120,
}
# Fields a CSV column can be mapped to
IMPORT_FIELDS = ["email", *TEXT_FIELDS.keys(), "website", "state", "timezone", "tags"]


def log_activity(db: Session, contact_id: uuid.UUID, type_: str, summary: str, user: User | None = None, **data) -> None:
    db.add(CrmActivity(contact_id=contact_id, user_id=user.id if user else None, type=type_, summary=summary, data=data))


def is_suppressed(db: Session, email: str) -> bool:
    return db.scalar(select(SuppressionEntry.id).where(SuppressionEntry.email == email.lower())) is not None


def suppressed_set(db: Session, emails: list[str]) -> set[str]:
    if not emails:
        return set()
    return set(db.scalars(select(SuppressionEntry.email).where(SuppressionEntry.email.in_(emails))))


def find_active_contact(db: Session, email: str) -> Contact | None:
    return db.scalar(select(Contact).where(Contact.email == email, Contact.deleted_at.is_(None)))


def get_or_create_company(db: Session, name: str | None, website: str | None, *, state=None, city=None,
                          industry=None, timezone=None) -> Company | None:
    domain = domain_of(website)
    company = None
    if domain:
        company = db.scalar(select(Company).where(Company.domain == domain, Company.deleted_at.is_(None)))
    if company is None and name:
        company = db.scalar(
            select(Company).where(func.lower(Company.name) == name.strip().lower(), Company.deleted_at.is_(None))
        )
    if company is None and (name or domain):
        company = Company(
            name=(name or domain or "").strip()[:300],
            website=website,
            domain=domain,
            state=state,
            city=city,
            industry=industry,
            timezone=timezone,
        )
        db.add(company)
        db.flush()
    elif company is not None and website and not company.website:
        company.website, company.domain = website, domain
    return company


def get_or_create_tags(db: Session, names: list[str]) -> list[Tag]:
    clean_names = sorted({n.strip()[:80] for n in names if n and n.strip()})
    if not clean_names:
        return []
    existing = {t.name: t for t in db.scalars(select(Tag).where(Tag.name.in_(clean_names)))}
    for n in clean_names:
        if n not in existing:
            existing[n] = Tag(name=n)
            db.add(existing[n])
    db.flush()
    return [existing[n] for n in clean_names]


def apply_fields(db: Session, contact: Contact, data: dict, *, link_company: bool = True) -> None:
    """Apply already-validated user input to a contact, normalising as we go."""
    if "email" in data and data["email"] is not None:
        contact.email = normalize_email(data["email"])
    for f, max_len in TEXT_FIELDS.items():
        if f in data:
            setattr(contact, f, clean(data[f], max_len))
    if "website" in data:
        contact.website = normalize_website(data["website"])
    if "state" in data:
        contact.state = normalize_state(data["state"])
    if "timezone" in data:
        contact.timezone = data["timezone"] or None
    if not contact.timezone and contact.state:
        contact.timezone = guess_timezone(contact.state)
    for f in ("verification_status", "lead_status"):
        if data.get(f):
            setattr(contact, f, data[f])
    if data.get("custom_fields") is not None:
        contact.custom_fields = {**(contact.custom_fields or {}), **data["custom_fields"]}
    if data.get("company_id"):
        contact.company_id = data["company_id"]
    elif link_company and (contact.company_name or contact.website) and not contact.company_id:
        company = get_or_create_company(
            db, contact.company_name, contact.website, state=contact.state, city=contact.city,
            industry=contact.industry, timezone=contact.timezone,
        )
        if company:
            contact.company_id = company.id
    if data.get("tags") is not None:
        contact.tags = get_or_create_tags(db, data["tags"])


def set_lead_status(db: Session, contact: Contact, status: str, user: User | None, reason: str = "manual") -> bool:
    if contact.lead_status == status:
        return False
    old = contact.lead_status
    contact.lead_status = status
    log_activity(db, contact.id, "stage_change", f"Stage changed from {old} to {status}", user, old=old, new=status,
                 reason=reason)
    return True


def template_variables(contact: Contact) -> dict[str, str]:
    """Variables available to templates for this contact. Values may be empty strings."""
    company = contact.company
    vars_ = {
        "first_name": contact.first_name or "",
        "last_name": contact.last_name or "",
        "full_name": " ".join(p for p in (contact.first_name, contact.last_name) if p),
        "email": contact.email,
        "company_name": contact.company_name or (company.name if company else "") or "",
        "city": contact.city or (company.city if company else "") or "",
        "state": contact.state or (company.state if company else "") or "",
        "website": contact.website or (company.website if company else "") or "",
        "job_title": contact.job_title or "",
        "industry": contact.industry or "",
        "phone": contact.phone or "",
    }
    for k, v in (contact.custom_fields or {}).items():
        vars_.setdefault(k, str(v) if v is not None else "")
    vars_.setdefault("custom_intro", "")
    return vars_
