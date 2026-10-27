"""Back up the database apps/api/.env points at to a dated file. Read-only: it never changes or stops the database.

    apps/api/.venv/Scripts/python scripts/backup_db.py                 # -> var/backups/outreach-YYYYMMDD-HHMMSS.dump
    apps/api/.venv/Scripts/python scripts/backup_db.py --to D:/Backups # -> another folder (e.g. OneDrive, USB drive)

Uses pg_dump's custom format: a consistent snapshot taken while the app keeps running. Each backup is checked by
reading its table of contents back with pg_restore before it gets its final name, so a half-written or unreadable
file never looks like a good backup. Nothing is ever deleted.

Encryption: when BACKUP_ENCRYPTION_KEY (a Fernet key) is set in the environment or apps/api/.env, the backup is
written as <name>.dump.enc and the plain dump never touches the disk under its final name. Keep a copy of that key
somewhere other than this computer (a password manager): without it an encrypted backup can't be restored.
    apps/api/.venv/Scripts/python scripts/backup_db.py --decrypt var/backups/<file>.dump.enc   # -> <file>.dump

To restore (replaces the data in the target database, so stop the backend and engine first and ask before doing it):
    pg_restore --clean --if-exists --no-owner -d <database url> var/backups/<file>.dump
"""

import os
import pathlib
import re
import subprocess
import sys
from datetime import datetime
from urllib.parse import unquote, urlsplit

from pgserver._commands import POSTGRES_BIN_PATH

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV = ROOT / "apps" / "api" / ".env"
DEFAULT_DEST = ROOT / "var" / "backups"


def tool(name: str) -> str:
    return str(POSTGRES_BIN_PATH / (f"{name}.exe" if os.name == "nt" else name))


def env_value(key: str) -> str | None:
    m = re.search(rf"^{key}=([^\r\n]*)", ENV.read_text(encoding="utf-8-sig"), re.M)
    return os.environ.get(key) or (m.group(1).strip() if m else None) or None


def database_url() -> str:
    url = env_value("DATABASE_URL")
    if not url:
        sys.exit(f"No DATABASE_URL in {ENV}")
    return url


def fernet():
    key = env_value("BACKUP_ENCRYPTION_KEY")
    if not key:
        return None
    from cryptography.fernet import Fernet

    return Fernet(key.encode())


def decrypt_file(path: pathlib.Path) -> None:
    f = fernet()
    if f is None:
        sys.exit("BACKUP_ENCRYPTION_KEY is not set")
    out = path.with_suffix("")  # x.dump.enc -> x.dump
    out.write_bytes(f.decrypt(path.read_bytes()))
    print(f"Decrypted to {out}")


def main() -> None:
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "--decrypt":
        decrypt_file(pathlib.Path(args[1]))
        return
    if args and (args[0] != "--to" or len(args) != 2):
        sys.exit(__doc__)
    dest = pathlib.Path(args[1]) if args else DEFAULT_DEST
    dest.mkdir(parents=True, exist_ok=True)

    u = urlsplit(database_url())
    db = u.path.lstrip("/")
    # Credentials go through the environment, never the command line (visible to other processes) or the output
    env = {**os.environ, "PGHOST": u.hostname or "127.0.0.1", "PGPORT": str(u.port or 5432), "PGDATABASE": db}
    if u.username:
        env["PGUSER"] = unquote(u.username)
    if u.password:
        env["PGPASSWORD"] = unquote(u.password)

    target = dest / f"{db}-{datetime.now():%Y%m%d-%H%M%S}.dump"
    partial = target.with_name(target.name + ".partial")
    print(f"Backing up {u.hostname}:{u.port}/{db} ...")
    dump = subprocess.run([tool("pg_dump"), "--format=custom", "--no-owner", "--file", str(partial)],
                          env=env, capture_output=True, text=True)
    if dump.returncode != 0:
        partial.unlink(missing_ok=True)
        sys.exit(f"Backup FAILED (nothing was changed):\n{dump.stderr.strip()}")

    check = subprocess.run([tool("pg_restore"), "--list", str(partial)], capture_output=True, text=True)
    tables = sum(1 for line in check.stdout.splitlines() if " TABLE DATA " in line)
    if check.returncode != 0 or tables == 0:
        sys.exit(f"Backup FAILED verification; the unreadable file was left at {partial}\n{check.stderr.strip()}")
    f = fernet()
    if f is not None:
        sealed_target = target.with_name(target.name + ".enc")
        sealed = sealed_target.with_name(sealed_target.name + ".partial")
        sealed.write_bytes(f.encrypt(partial.read_bytes()))
        partial.unlink()
        if not f.decrypt(sealed.read_bytes()).startswith(b"PGDMP"):  # custom-format dumps start with this magic
            sys.exit(f"Backup FAILED: the encrypted file did not decrypt back; left at {sealed}")
        partial, target = sealed, sealed_target
    os.replace(partial, target)
    print(f"Backup OK: {target} ({target.stat().st_size / 1024:,.0f} KB, {tables} tables"
          + (", encrypted)" if f else ", not encrypted: set BACKUP_ENCRYPTION_KEY to encrypt)"))


if __name__ == "__main__":
    main()
