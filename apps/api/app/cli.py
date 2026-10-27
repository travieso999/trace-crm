"""Operator CLI.

    python -m app.cli create-admin --email you@tracestudio.com --name "Your Name"
    python -m app.cli generate-keys
"""

import argparse
import getpass
import os
import secrets
import sys

from cryptography.fernet import Fernet
from sqlalchemy import select


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


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create-admin")
    p.add_argument("--email", required=True)
    p.add_argument("--name", default="")
    p.add_argument("--password", help="Prefer the ADMIN_PASSWORD env var or the interactive prompt")
    sub.add_parser("generate-keys")
    args = parser.parse_args()
    if args.cmd == "create-admin":
        create_admin(args.email, args.name, args.password)
    elif args.cmd == "generate-keys":
        print(f"SECRET_KEY={secrets.token_urlsafe(48)}")
        print(f"TOKEN_ENCRYPTION_KEYS={Fernet.generate_key().decode()}")


if __name__ == "__main__":
    main()
