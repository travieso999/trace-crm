"""Back up the database apps/api/.env points at to a dated file. Read-only: it never changes or stops the database.

    apps/api/.venv/Scripts/python scripts/backup_db.py                 # -> var/backups/outreach-YYYYMMDD-HHMMSS.dump
    apps/api/.venv/Scripts/python scripts/backup_db.py --to D:/Backups # -> another folder (e.g. OneDrive, USB drive)

Uses pg_dump's custom format: a consistent snapshot taken while the app keeps running. Each backup is checked by
reading its table of contents back with pg_restore before it gets its final name, so a half-written or unreadable
file never looks like a good backup. Nothing is ever deleted.

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


def database_url() -> str:
    m = re.search(r"^DATABASE_URL=([^\r\n]*)", ENV.read_text(encoding="utf-8-sig"), re.M)
    if not m or not m.group(1).strip():
        sys.exit(f"No DATABASE_URL in {ENV}")
    return m.group(1).strip()


def main() -> None:
    args = sys.argv[1:]
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
    os.replace(partial, target)
    print(f"Backup OK: {target} ({target.stat().st_size / 1024:,.0f} KB, {tables} tables)")


if __name__ == "__main__":
    main()
