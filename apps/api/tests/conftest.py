"""Test fixtures.

Tests run against a real PostgreSQL database. Set TEST_DATABASE_URL to use an existing server
(e.g. the Docker Compose one); otherwise an embedded server is started with `pgserver`.
The schema is created by running the real Alembic migrations (this doubles as a migration test).
No real email provider is ever contacted: provider HTTP is mocked per test.
"""

import os
import pathlib
import tempfile

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-1234567890")
os.environ.setdefault("TOKEN_ENCRYPTION_KEYS", "Vg0sdXKOS6k3k9cP7r4Q3x2Y8bqvbm4Y0aZb0n0mJ4E=")
os.environ.setdefault("LOGIN_RATE_LIMIT_PER_MINUTE", "1000")
os.environ.setdefault("API_RATE_LIMIT_PER_MINUTE", "100000")
os.environ.setdefault("SANDBOX_OUTBOX_DIR", str(pathlib.Path(tempfile.gettempdir()) / "ts_outreach_test_outbox"))

import pytest  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

API_DIR = pathlib.Path(__file__).resolve().parent.parent


def _database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        return url
    import importlib.util
    import logging

    import pgserver

    # Same helper the dev DB script uses: pgserver on Windows lacks the timezone database
    spec = importlib.util.spec_from_file_location("dev_db", API_DIR.parent.parent / "scripts" / "dev_db.py")
    dev_db = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dev_db)
    dev_db.ensure_timezone_data()

    logging.getLogger("pgserver").disabled = True
    pgdata = pathlib.Path(tempfile.gettempdir()) / "ts_outreach_pgtest"
    srv = pgserver.get_server(pgdata, cleanup_mode="stop")
    if "1 row" not in srv.psql("select 1 from pg_database where datname='outreach_test';"):
        srv.psql("create database outreach_test;")
    base = srv.get_uri().rsplit("/", 1)[0].replace("postgresql://", "postgresql+psycopg://")
    pytest._pgserver = srv  # keep a reference so it is not garbage collected
    return f"{base}/outreach_test"


DB_URL = _database_url()
os.environ["DATABASE_URL"] = DB_URL

from app.core import ratelimit  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.db import SessionLocal, reset_engine  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema():
    get_settings.cache_clear()
    reset_engine(DB_URL)
    engine = create_engine(DB_URL)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    engine.dispose()
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", DB_URL)
    cfg.attributes["skip_logging"] = True
    command.upgrade(cfg, "head")
    yield


TABLES_TO_KEEP = {"alembic_version"}


@pytest.fixture(autouse=True)
def _clean_db():
    ratelimit.reset()
    yield
    engine = create_engine(DB_URL)
    with engine.begin() as conn:
        tables = [r[0] for r in conn.execute(text(
            "select tablename from pg_tables where schemaname='public'")) if r[0] not in TABLES_TO_KEEP]
        if tables:
            conn.execute(text("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE"))
    engine.dispose()


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def app():
    from app.main import create_app

    return create_app()


def make_user(email="admin@trace.studio", password="Sup3rSecretPass!", role="admin"):
    from app.core.security import hash_password
    from app.models import User

    with SessionLocal() as s:
        u = User(email=email, full_name="Test User", role=role, password_hash=hash_password(password))
        s.add(u)
        s.commit()
        return u.id


class AuthedClient:
    """TestClient wrapper that logs in and sends the CSRF header automatically."""

    def __init__(self, client, csrf):
        self.c, self.csrf = client, csrf

    def _h(self, kw):
        kw.setdefault("headers", {})["X-CSRF-Token"] = self.csrf
        return kw

    def get(self, *a, **kw):
        return self.c.get(*a, **kw)

    def post(self, *a, **kw):
        return self.c.post(*a, **self._h(kw))

    def patch(self, *a, **kw):
        return self.c.patch(*a, **self._h(kw))

    def put(self, *a, **kw):
        return self.c.put(*a, **self._h(kw))

    def delete(self, *a, **kw):
        return self.c.delete(*a, **self._h(kw))


def login(app, email="admin@trace.studio", password="Sup3rSecretPass!") -> AuthedClient:
    from fastapi.testclient import TestClient

    client = TestClient(app)
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return AuthedClient(client, r.json()["csrf_token"])


@pytest.fixture
def client(app):
    make_user()
    return login(app)


@pytest.fixture
def compliance(db):
    from app.services import app_settings

    app_settings.update_section(db, "compliance", {"physical_address": "123 Main St, Suite 4, Austin, TX 78701"})
    db.commit()
