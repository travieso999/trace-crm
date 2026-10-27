"""Start an embedded PostgreSQL 16 for local development without Docker (uses the `pgserver` package).

    apps/api/.venv/Scripts/python scripts/dev_db.py           # start (idempotent) and print DATABASE_URL
    apps/api/.venv/Scripts/python scripts/dev_db.py --stop    # stop cleanly
    apps/api/.venv/Scripts/python scripts/dev_db.py --switch  # start and point apps/api/.env at this database

The data directory is ./var/pgdata (git-ignored). The server keeps running after this script exits.

apps/api/.env records which data directory its DATABASE_URL belongs to (DEV_DB_PGDATA). The port can change between
starts, so the URL is updated automatically when it belongs to this data directory. When .env points anywhere else
(another checkout's database, Docker, a server that is not running) nothing is started or written unless --switch
is given, so the API is never moved to a different database without you asking for it.
"""

import os
import pathlib
import re
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

import pgserver

ROOT = pathlib.Path(__file__).resolve().parent.parent
PGDATA = ROOT / "var" / "pgdata"
ENV = ROOT / "apps" / "api" / ".env"
OPTIONS = {"--stop", "--switch"}


def ensure_timezone_data() -> None:
    """pgserver's Windows build ships without share/postgresql/timezone, so `AT TIME ZONE 'America/New_York'` fails.
    The tzdata package carries the same compiled TZif files; copy them in once."""
    import tzdata

    target = pathlib.Path(pgserver.__file__).parent / "pginstall" / "share" / "postgresql" / "timezone"
    if (target / "America" / "New_York").exists():
        return
    shutil.copytree(pathlib.Path(tzdata.__file__).parent / "zoneinfo", target, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__init__.py", "__pycache__"))


def where(url: str) -> str:
    """host:port/database of a URL, without credentials (safe to print)."""
    u = urlsplit(url)
    return f"{u.hostname}:{u.port}{u.path}"


def same_dir(a: str | pathlib.Path, b: str | pathlib.Path) -> bool:
    return os.path.normcase(str(pathlib.Path(a).resolve())) == os.path.normcase(str(pathlib.Path(b).resolve()))


def read_env() -> tuple[str, bool, str]:
    """(text without BOM, had BOM, newline). Read as bytes so the BOM and CRLF line endings survive a rewrite."""
    raw = ENV.read_bytes().decode("utf-8")
    bom = raw.startswith("﻿")
    text = raw[1:] if bom else raw
    return text, bom, "\r\n" if "\r\n" in text else "\n"


def write_env(text: str, bom: bool) -> None:
    tmp = ENV.with_name(ENV.name + ".tmp")
    tmp.write_bytes((("﻿" if bom else "") + text).encode("utf-8"))
    os.replace(tmp, ENV)


def env_value(text: str, key: str) -> str | None:
    m = re.search(rf"^{key}=([^\r\n]*)", text, re.M)
    return m.group(1).strip() if m else None


def set_env_value(text: str, key: str, value: str, newline: str) -> str:
    line = f"{key}={value}"
    if re.search(rf"^{key}=", text, re.M):
        return re.sub(rf"^{key}=[^\r\n]*", lambda _: line, text, count=1, flags=re.M)
    if text and not text.endswith("\n"):
        text += newline
    return text + line + newline


def served_data_directory(url: str) -> str | None:
    """Data directory of the server `url` points at, or None when it is not running or cannot be asked."""
    import psycopg

    try:
        with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://", 1), connect_timeout=3) as conn:
            return conn.execute("SHOW data_directory").fetchone()[0]
    except psycopg.Error:
        return None


def foreign_database(text: str) -> str | None:
    """Why the DATABASE_URL in .env does not belong to PGDATA, or None when it does (or there is none yet)."""
    current = env_value(text, "DATABASE_URL")
    if not current:
        return None
    if urlsplit(current).hostname not in ("127.0.0.1", "localhost"):
        return f"it points at {where(current)}, which is not a local embedded database"
    served = served_data_directory(current)
    if served and not same_dir(served, PGDATA):
        return f"it points at {where(current)}, which is serving {served}"
    recorded = env_value(text, "DEV_DB_PGDATA")
    if recorded and not same_dir(recorded, PGDATA):
        return f"it belongs to the data directory {recorded}"
    if not recorded and not served:
        return f"it points at {where(current)}, which is not running, so it cannot be confirmed to be {PGDATA}"
    return None


def stop() -> None:
    pg_ctl = str(pgserver.POSTGRES_BIN_PATH / ("pg_ctl.exe" if os.name == "nt" else "pg_ctl"))
    if subprocess.run([pg_ctl, "status", "-D", str(PGDATA)], capture_output=True).returncode != 0:
        print("not running")
        return
    subprocess.run([pg_ctl, "stop", "-D", str(PGDATA), "-m", "fast", "-w", "-t", "120"], check=True)
    print("stopped")


def main() -> None:
    args = sys.argv[1:]
    unknown = [a for a in args if a not in OPTIONS]
    if unknown:
        sys.exit(f"unknown option {' '.join(unknown)}\n{__doc__}")
    if "--stop" in args:
        stop()
        return
    env = read_env() if ENV.exists() else None
    if env and "--switch" not in args:
        reason = foreign_database(env[0])
        if reason:
            sys.exit(
                f"Not starting: apps/api/.env DATABASE_URL does not belong to {PGDATA} ({reason}).\n"
                "Nothing was changed. To point the API at this database instead, run again with --switch."
            )
    ensure_timezone_data()
    PGDATA.parent.mkdir(parents=True, exist_ok=True)
    srv = pgserver.get_server(PGDATA, cleanup_mode=None)
    for name in ("outreach", "outreach_test"):
        exists = srv.psql(f"select 1 from pg_database where datname='{name}';")  # noqa: S608 - fixed names above
        if "1 row" not in exists:
            srv.psql(f"create database {name};")
    base = srv.get_uri().rsplit("/", 1)[0].replace("postgresql://", "postgresql+psycopg://")
    url = f"{base}/outreach"
    if env:
        text, bom, newline = env
        old = env_value(text, "DATABASE_URL")
        new = set_env_value(set_env_value(text, "DATABASE_URL", url, newline), "DEV_DB_PGDATA", str(PGDATA), newline)
        if new != text:
            write_env(new, bom)
        if old and old != url:
            print(f"apps/api/.env DATABASE_URL: {where(old)} -> {where(url)} (restart the API and engine)")
    print(f"DATABASE_URL={url}")
    print(f"TEST_DATABASE_URL={base}/outreach_test")


if __name__ == "__main__":
    main()
