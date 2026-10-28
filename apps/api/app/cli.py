"""Operator CLI.

    python -m app.cli create-admin --email you@tracestudio.com --name "Your Name"
    python -m app.cli generate-keys
    python -m app.cli inbox-cleanup [--apply]   # hide non-campaign conversations from the Unified Inbox
"""

import argparse
import getpass
import os
import secrets
import sys

from cryptography.fernet import Fernet
from sqlalchemy import func, select


def create_admin(email: str, name: str, password: str | None) -> None:
    from app.core.db import session_scope
    from app.core.security import hash_password, validate_password_strength
    from app.models import User

    password = password or os.environ.get("ADMIN_PASSWORD") or getpass.getpass("Password (min 12 chars): ")
    try:
        validate_password_strength(password)
    except ValueError as exc:
        sys.exit(f"Error: {exc}")
    with session_scope() as db:
        existing = db.scalar(select(User).where(User.email == email.lower()))
        if existing:
            existing.password_hash = hash_password(password)
            existing.role, existing.is_active = "admin", True
            print(f"Updated existing user {email} (now admin, password reset)")
        else:
            db.add(User(email=email.lower(), full_name=name, role="admin", password_hash=hash_password(password)))
            print(f"Created admin {email}")


def run_engine(interval: int, once: bool) -> None:
    import time

    from app.core.config import get_settings
    from app.core.logging import configure_logging
    from app.workers import engine

    s = get_settings()
    configure_logging(s.log_level)
    print(f"Engine running (every {interval}s), REAL_SENDING={s.real_sending}. Ctrl+C to stop.")
    while True:
        print(engine.tick())
        if once:
            return
        time.sleep(interval)


def inbox_cleanup(apply: bool) -> None:
    """Hide conversations synced before the inbox only kept campaign replies. Nothing is deleted (here or in Gmail)."""
    from app.core.db import session_scope
    from app.services import inbox

    with session_scope() as db:
        r = inbox.hide_unrelated(db, apply=apply)
    if apply:
        print(f"Hid {r['hidden']} non-campaign conversation(s) from the Unified Inbox. Their messages are kept.")
    else:
        print(f"Dry run: {r['would_hide']} non-campaign conversation(s) would be hidden. Run with --apply to hide them.")


def companies_cleanup(apply: bool) -> None:
    """Archive companies that were created automatically from imported business names. Kept: companies with a won
    prospect or a won opportunity, and companies someone added by hand. Prospects keep their business names."""
    from sqlalchemy import String, cast, exists, update

    from app.core.db import session_scope
    from app.models import AuditLog, Company, Contact, CrmOpportunity
    from app.models.base import utcnow

    with session_scope() as db:
        manual = select(AuditLog.entity_id).where(AuditLog.action == "company.created",
                                                  AuditLog.entity_type == "company")
        won_contact = exists().where(Contact.company_id == Company.id, Contact.deleted_at.is_(None),
                                     Contact.lead_status == "won")
        won_opp = exists().where(CrmOpportunity.contact_id == Contact.id, Contact.company_id == Company.id,
                                 CrmOpportunity.status == "won")
        ids = list(db.scalars(select(Company.id).where(
            Company.deleted_at.is_(None), ~won_contact, ~won_opp, cast(Company.id, String).not_in(manual))))
        kept = db.scalar(select(func.count()).select_from(Company).where(Company.deleted_at.is_(None))) - len(ids)
        if apply and ids:
            db.execute(update(Contact).where(Contact.company_id.in_(ids)).values(company_id=None))
            db.execute(update(Company).where(Company.id.in_(ids)).values(deleted_at=utcnow()))
    verb = "Archived" if apply else "Dry run: would archive"
    print(f"{verb} {len(ids)} auto-created compan{'y' if len(ids) == 1 else 'ies'}; {kept} client(s) kept."
          + ("" if apply else " Run with --apply to archive them."))


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create-admin")
    p.add_argument("--email", required=True)
    p.add_argument("--name", default="")
    p.add_argument("--password", help="Prefer the ADMIN_PASSWORD env var or the interactive prompt")
    sub.add_parser("generate-keys")
    e = sub.add_parser("engine", help="Run the send/sync engine in one process (local development)")
    e.add_argument("--interval", type=int, default=30, help="Seconds between passes")
    e.add_argument("--once", action="store_true")
    c = sub.add_parser("inbox-cleanup", help="Hide non-campaign conversations from the Unified Inbox (dry run by default)")
    c.add_argument("--apply", action="store_true", help="Actually hide them")
    cc = sub.add_parser("companies-cleanup", help="Archive companies auto-created from imports (dry run by default)")
    cc.add_argument("--apply", action="store_true", help="Actually archive them")
    args = parser.parse_args()
    if args.cmd == "create-admin":
        create_admin(args.email, args.name, args.password)
    elif args.cmd == "engine":
        run_engine(args.interval, args.once)
    elif args.cmd == "inbox-cleanup":
        inbox_cleanup(args.apply)
    elif args.cmd == "companies-cleanup":
        companies_cleanup(args.apply)
    elif args.cmd == "generate-keys":
        print(f"SECRET_KEY={secrets.token_urlsafe(48)}")
        print(f"TOKEN_ENCRYPTION_KEYS={Fernet.generate_key().decode()}")


if __name__ == "__main__":
    main()
