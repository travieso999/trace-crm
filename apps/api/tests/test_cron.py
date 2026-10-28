from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.routes.cron import ENGINE_LOCK_ID
from app.core.config import get_settings
from app.core.db import get_engine

SECRET = "cron-secret-that-is-long-enough-0123456789"


def _client(monkeypatch, app, secret=SECRET):
    monkeypatch.setattr(get_settings(), "cron_secret", secret)
    return TestClient(app)


def test_cron_engine_off_without_secret(monkeypatch, app):
    c = _client(monkeypatch, app, secret=None)
    assert c.get("/api/cron/engine", headers={"Authorization": "Bearer x"}).status_code == 404


def test_cron_engine_requires_bearer(monkeypatch, app):
    c = _client(monkeypatch, app)
    assert c.get("/api/cron/engine").status_code == 401
    assert c.get("/api/cron/engine", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.get("/api/cron/engine", headers={"Authorization": SECRET}).status_code == 401


def test_cron_engine_runs_a_pass(monkeypatch, app):
    r = _client(monkeypatch, app).get("/api/cron/engine", headers={"Authorization": f"Bearer {SECRET}"})
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"activated", "replies", "sends", "reconciled", "reminders", "audience"}


def test_cron_engine_skips_while_another_pass_runs(monkeypatch, app):
    c = _client(monkeypatch, app)
    with get_engine().connect() as conn, conn.begin():
        conn.execute(text("select pg_advisory_xact_lock(:id)"), {"id": ENGINE_LOCK_ID})
        r = c.get("/api/cron/engine", headers={"Authorization": f"Bearer {SECRET}"})
    assert r.json() == {"skipped": "another engine pass is running"}


def test_database_url_accepts_hosted_postgres_scheme():
    from app.core.config import Settings

    s = Settings(database_url="postgres://u:p@ep-x-pooler.neon.tech/db?sslmode=require")
    assert s.database_url == "postgresql+psycopg://u:p@ep-x-pooler.neon.tech/db?sslmode=require"
