"""CSV parsing, column mapping suggestions and transactional import."""

import csv
import io
import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Contact, User
from app.services.contacts import (
    IMPORT_FIELDS,
    apply_fields,
    get_or_create_tags,
    log_activity,
    suppressed_set,
)
from app.services.normalize import InvalidEmail, normalize_email

_ALIASES = {
    "email": ["email", "email address", "e-mail", "work email", "emailaddress", "mail"],
    "first_name": ["first name", "firstname", "first", "given name", "fname"],
    "last_name": ["last name", "lastname", "last", "surname", "family name", "lname"],
    "company_name": ["company", "company name", "organization", "organisation", "business", "business name", "agency"],
    "website": ["website", "url", "company website", "site", "domain", "web"],
    "phone": ["phone", "phone number", "telephone", "mobile", "tel"],
    "city": ["city", "town"],
    "state": ["state", "region", "province", "st"],
    "timezone": ["timezone", "time zone", "tz"],
    "industry": ["industry", "sector", "vertical"],
    "job_title": ["title", "job title", "position", "role"],
    "linkedin_url": ["linkedin", "linkedin url", "linkedin profile"],
    "notes": ["notes", "note", "comments"],
    "source": ["source", "lead source"],
    "tags": ["tags", "tag", "labels"],
}


class CsvError(ValueError):
    pass


def _norm_header(h: str) -> str:
    return re.sub(r"[\s_\-]+", " ", h.strip().lower())


def parse_csv(raw: bytes, max_rows: int) -> tuple[list[str], list[list[str]]]:
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    if "\x00" in text:
        raise CsvError("File does not look like a text CSV")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        raise CsvError("CSV is empty")
    headers = [h.strip() for h in rows[0]]
    if len(set(headers)) != len(headers):
        raise CsvError("CSV has duplicate column headers")
    body = rows[1:]
    if len(body) > max_rows:
        raise CsvError(f"CSV has {len(body)} rows; the maximum per import is {max_rows}")
    return headers, body


def suggest_mapping(headers: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for h in headers:
        nh = _norm_header(h)
        for field, aliases in _ALIASES.items():
            if field not in used and nh in aliases:
                mapping[h] = field
                used.add(field)
                break
        else:
            mapping[h] = ""
    return mapping


def _row_to_data(headers: list[str], row: list[str], mapping: dict[str, str]) -> dict:
    data: dict = {"custom_fields": {}}
    for idx, header in enumerate(headers):
        target = mapping.get(header) or ""
        if not target:
            continue
        value = row[idx].strip() if idx < len(row) else ""
        if target.startswith("custom:"):
            key = target[7:]
            if value:
                data["custom_fields"][key] = value[:2000]
        elif target == "tags":
            data["tags"] = [t.strip() for t in re.split(r"[;,|]", value) if t.strip()]
        else:
            data[target] = value
    if not data["custom_fields"]:
        data.pop("custom_fields")
    return data


def validate_mapping(headers: list[str], mapping: dict[str, str]) -> None:
    targets = [t for h, t in mapping.items() if t]
    unknown_headers = set(mapping) - set(headers)
    if unknown_headers:
        raise CsvError(f"Mapping references unknown columns: {', '.join(sorted(unknown_headers))}")
    if "email" not in targets:
        raise CsvError("A column must be mapped to email")
    for t in targets:
        if t.startswith("custom:"):
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,49}", t[7:]):
                raise CsvError(f"Invalid custom field name '{t[7:]}'")
        elif t not in IMPORT_FIELDS:
            raise CsvError(f"Unknown target field '{t}'")
    dupes = {t for t in targets if targets.count(t) > 1}
    if dupes:
        raise CsvError(f"Fields mapped more than once: {', '.join(sorted(dupes))}")


def import_rows(
    db: Session,
    headers: list[str],
    rows: list[list[str]],
    mapping: dict[str, str],
    *,
    duplicate_strategy: str,
    tags: list[str],
    source: str | None,
    user: User | None,
    dry_run: bool,
) -> tuple[dict, list[uuid.UUID]]:
    """Import inside the caller's transaction. Caller commits (or rolls back for dry runs)."""
    validate_mapping(headers, mapping)
    result = {"created": 0, "updated": 0, "skipped_duplicates": 0, "skipped_suppressed": 0, "invalid": 0, "errors": []}
    prepared: list[tuple[int, str, dict]] = []
    seen: set[str] = set()
    for i, row in enumerate(rows, start=2):  # row 1 is the header
        data = _row_to_data(headers, row, mapping)
        try:
            email = normalize_email(data.get("email"))
        except InvalidEmail as exc:
            result["invalid"] += 1
            result["errors"].append({"row": i, "email": data.get("email") or None, "error": str(exc)})
            continue
        if email in seen:
            result["skipped_duplicates"] += 1
            result["errors"].append({"row": i, "email": email, "error": "Duplicate email within file"})
            continue
        seen.add(email)
        data["email"] = email
        prepared.append((i, email, data))

    emails = [e for _, e, _ in prepared]
    suppressed = suppressed_set(db, emails)
    existing = {
        c.email: c
        for c in db.scalars(select(Contact).where(Contact.email.in_(emails), Contact.deleted_at.is_(None)))
    } if emails else {}
    extra_tags = get_or_create_tags(db, tags) if tags else []
    touched: list[uuid.UUID] = []

    for row_no, email, data in prepared:
        if email in suppressed:
            result["skipped_suppressed"] += 1
            continue
        if source and not data.get("source"):
            data["source"] = source
        row_tags = data.pop("tags", None) or []
        contact = existing.get(email)
        try:
            with db.begin_nested():
                if contact is not None:
                    if duplicate_strategy == "skip":
                        result["skipped_duplicates"] += 1
                        continue
                    # update only non-empty values so blanks never wipe existing data
                    apply_fields(db, contact, {k: v for k, v in data.items() if v not in ("", None, {})})
                    result["updated"] += 1
                else:
                    contact = Contact(email=email, custom_fields={})
                    db.add(contact)
                    apply_fields(db, contact, data)
                    db.flush()
                    log_activity(db, contact.id, "import", "Imported from CSV", user, source=data.get("source"))
                    result["created"] += 1
                all_tags = {t.id: t for t in [*contact.tags, *extra_tags, *get_or_create_tags(db, row_tags)]}
                contact.tags = list(all_tags.values())
                db.flush()
                touched.append(contact.id)
        except Exception as exc:  # row-level DB error (e.g. constraint) – report, keep going
            result["errors"].append({"row": row_no, "email": email, "error": f"Database error: {type(exc).__name__}"})
            result["invalid"] += 1
    result["errors"] = result["errors"][:500]
    result["dry_run"] = dry_run
    return result, touched


def export_contacts_csv(contacts: list[Contact]) -> str:
    out = io.StringIO()
    custom_keys = sorted({k for c in contacts for k in (c.custom_fields or {})})
    cols = ["email", "first_name", "last_name", "company_name", "website", "phone", "city", "state", "timezone",
            "industry", "job_title", "linkedin_url", "source", "verification_status", "lead_status", "tags",
            "last_contacted_at", "last_replied_at", "created_at"]
    writer = csv.writer(out)
    writer.writerow(cols + [f"custom:{k}" for k in custom_keys])
    for c in contacts:
        row = []
        for col in cols:
            if col == "tags":
                row.append(";".join(t.name for t in c.tags))
            else:
                v = getattr(c, col)
                row.append("" if v is None else (v.isoformat() if hasattr(v, "isoformat") else str(v)))
        row += [str((c.custom_fields or {}).get(k, "")) for k in custom_keys]
        writer.writerow([_csv_safe(v) for v in row])
    return out.getvalue()


def _csv_safe(value: str) -> str:
    """Neutralise spreadsheet formula injection."""
    if value and value[0] in "=+-@\t\r":
        return "'" + value
    return value
