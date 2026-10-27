from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.models import EmailEvent
from app.models.base import utcnow

NY = ZoneInfo("America/New_York")


def test_dashboard_buckets_events_by_local_day(client, db):
    # 23:30 yesterday in New York is already "today" in UTC: it must land on yesterday's local bar.
    today = utcnow().astimezone(NY).date()
    yesterday = today - timedelta(days=1)
    late = datetime.combine(yesterday, time(23, 30), tzinfo=NY)
    db.add_all([
        EmailEvent(type="accepted", data={}, occurred_at=late),
        EmailEvent(type="accepted", data={}, occurred_at=late),
        EmailEvent(type="replied", data={}, occurred_at=late),
    ])
    db.commit()

    r = client.get("/api/dashboard")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["timezone"] == "America/New_York"
    volume = {row["date"]: row for row in d["daily_volume"]}
    assert len(d["daily_volume"]) == 30 and d["daily_volume"][-1]["date"] == today.isoformat()
    assert volume[yesterday.isoformat()]["accepted"] == 2
    assert volume[today.isoformat()]["accepted"] == 0
    replies = {row["date"]: row for row in d["reply_trend"]}
    assert replies[yesterday.isoformat()]["replied"] == 1
    assert d["kpis"]["reply_rate_30d"] == 50.0
