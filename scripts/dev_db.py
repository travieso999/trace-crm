"""Start an embedded PostgreSQL 16 for local development without Docker (uses the `pgserver` package).

    apps/api/.venv/Scripts/python scripts/dev_db.py          # start (idempotent) and print DATABASE_URL
    apps/api/.venv/Scripts/python scripts/dev_db.py --stop   # stop

The data directory is ./var/pgdata (git-ignored). The server keeps running after this script exits.
The DATABASE_URL line in apps/api/.env is updated automatically because the port can change between starts.
"""

import pathlib
import re
import shutil
import sys

import pgserver

ROOT = pathlib.Path(__file__).resolve().parent.parent
PGDATA = ROOT / "var" / "pgdata"
ENV = ROOT / "apps" / "api" / ".env"


def ensure_timezone_data() -> None:
    """pgserver's Windows build ships without share/postgresql/timezone, so `AT TIME ZONE 'America/New_York'` fails.
    The tzdata package carries the same compiled TZif files; copy them in once."""
    import tzdata

    target = pathlib.Path(pgserver.__file__).parent / "pginstall" / "share" / "postgresql" / "timezone"
    if (target / "America" / "New_York").exists():
        return
    shutil.copytree(pathlib.Path(tzdata.__file__).parent / "zoneinfo", target, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__init__.py", "__pycache__"))


def main() -> None:
    ensure_timezone_data()
    PGDATA.parent.mkdir(parents=True, exist_ok=True)
    srv = pgserver.get_server(PGDATA, cleanup_mode=None)
    if "--stop" in sys.argv:
        srv.cleanup()
        print("stopped")
        return
    for name in ("outreach", "outreach_test"):
        exists = srv.psql(f"select 1 from pg_database where datname='{name}';")  # noqa: S608 - fixed names above
        if "1 row" not in exists:
            srv.psql(f"create database {name};")
    base = srv.get_uri().rsplit("/", 1)[0].replace("postgresql://", "postgresql+psycopg://")
    url = f"{base}/outreach"
    if ENV.exists():
        text = ENV.read_text()
        if re.search(r"^DATABASE_URL=.*$", text, re.M):
            text = re.sub(r"^DATABASE_URL=.*$", f"DATABASE_URL={url}", text, flags=re.M)
        else:
            text += f"\nDATABASE_URL={url}\n"
        ENV.write_text(text)
    print(f"DATABASE_URL={url}")
    print(f"TEST_DATABASE_URL={base}/outreach_test")


if __name__ == "__main__":
    main()
