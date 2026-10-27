import json

CSV = (
    "Email,First Name,Last Name,Company,Website,State,City,Favorite Color\n"
    "  John@ABCBail.com ,John,Smith,ABC Bail Bonds,abcbail.com,Texas,Austin,blue\n"
    "jane@xyzbonds.com,Jane,Doe,XYZ Bonds,https://www.xyzbonds.com,FL,Miami,red\n"
    "john@abcbail.com,Johnny,Dup,ABC Bail Bonds,,TX,,\n"
    "not-an-email,Bad,Row,,,,,\n"
    "two@@ats.com,Double,At,,,,,\n"
)


def _import(client, csv_text=CSV, **opts):
    mapping = {"Email": "email", "First Name": "first_name", "Last Name": "last_name", "Company": "company_name",
               "Website": "website", "State": "state", "City": "city", "Favorite Color": "custom:favorite_color"}
    options = {"mapping": mapping, **opts}
    return client.post("/api/contacts/import", files={"file": ("leads.csv", csv_text.encode(), "text/csv")},
                       data={"options": json.dumps(options)})


def test_create_list_get_update_delete(client):
    r = client.post("/api/contacts", json={"email": " Owner@Example.COM ", "first_name": "Ann", "state": "texas",
                                           "company_name": "Ann Bail Bonds", "website": "annbail.com",
                                           "tags": ["hot", "tx"]})
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["email"] == "owner@example.com"
    assert c["state"] == "TX" and c["timezone"] == "America/Chicago"
    assert c["company_id"] and {t["name"] for t in c["tags"]} == {"hot", "tx"}
    assert client.post("/api/contacts", json={"email": "owner@example.com"}).status_code == 409

    lst = client.get("/api/contacts", params={"q": "ann"}).json()
    assert lst["total"] == 1
    assert client.get("/api/contacts", params={"tag": "hot"}).json()["total"] == 1
    assert client.get("/api/contacts", params={"state": "FL"}).json()["total"] == 0

    r = client.patch(f"/api/contacts/{c['id']}", json={"lead_status": "interested", "job_title": "Owner"})
    assert r.status_code == 200 and r.json()["lead_status"] == "interested"
    acts = client.get(f"/api/contacts/{c['id']}/activities").json()
    assert any(a["type"] == "stage_change" for a in acts)

    assert client.delete(f"/api/contacts/{c['id']}").status_code == 200
    assert client.get(f"/api/contacts/{c['id']}").status_code == 404
    # soft-deleted email can be re-created
    assert client.post("/api/contacts", json={"email": "owner@example.com"}).status_code == 201


def test_invalid_input_rejected(client):
    assert client.post("/api/contacts", json={"email": "nope"}).status_code == 422
    assert client.post("/api/contacts", json={"email": "a@b.com", "timezone": "Mars/Base"}).status_code == 422
    assert client.post("/api/contacts", json={"email": "a@b.com", "custom_fields": {"Bad Key": "x"}}).status_code == 422


def test_csv_preview_suggests_mapping(client):
    r = client.post("/api/contacts/import/preview", files={"file": ("leads.csv", CSV.encode(), "text/csv")})
    assert r.status_code == 200
    body = r.json()
    assert body["total_rows"] == 5
    assert body["suggested_mapping"]["Email"] == "email"
    assert body["suggested_mapping"]["Company"] == "company_name"
    assert body["suggested_mapping"]["Favorite Color"] == ""


def test_csv_import_dry_run_then_commit(client):
    r = _import(client, dry_run=True)
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["dry_run"] and res["created"] == 2 and res["invalid"] == 2 and res["skipped_duplicates"] == 1
    assert client.get("/api/contacts").json()["total"] == 0  # dry run rolled back

    res = _import(client, tags=["import-1"], source="test-list").json()
    assert res["created"] == 2
    errors = {e["row"]: e["error"] for e in res["errors"]}
    assert 4 in errors and "Duplicate" in errors[4]
    contacts = client.get("/api/contacts", params={"sort": "email", "order": "asc"}).json()["items"]
    jane, john = contacts
    assert john["email"] == "john@abcbail.com" and john["state"] == "TX"
    assert john["custom_fields"] == {"favorite_color": "blue"}
    assert john["source"] == "test-list" and [t["name"] for t in john["tags"]] == ["import-1"]
    assert jane["website"] == "https://www.xyzbonds.com"
    companies = client.get("/api/companies").json()
    assert companies["total"] == 2

    # re-import: duplicates skipped by default, updated when requested
    assert _import(client).json()["skipped_duplicates"] >= 2
    res = _import(client, csv_text="Email,First Name\njohn@abcbail.com,Jonathan\n", duplicate_strategy="update")
    # mapping includes columns not in this csv → rejected cleanly
    assert res.status_code == 422


def test_csv_requires_email_mapping_and_rejects_non_csv(client):
    r = client.post("/api/contacts/import", files={"file": ("x.csv", b"a,b\n1,2\n", "text/csv")},
                    data={"options": json.dumps({"mapping": {"a": "first_name"}})})
    assert r.status_code == 422 and "email" in r.json()["detail"]
    r = client.post("/api/contacts/import/preview", files={"file": ("x.exe", b"MZ\x00", "application/octet-stream")})
    assert r.status_code == 415


def test_csv_update_strategy(client):
    client.post("/api/contacts", json={"email": "john@abcbail.com", "first_name": "John", "city": "Austin"})
    r = client.post("/api/contacts/import", files={"file": ("x.csv", b"Email,First Name,City\njohn@abcbail.com,Jonathan,\n", "text/csv")},
                    data={"options": json.dumps({"mapping": {"Email": "email", "First Name": "first_name", "City": "city"},
                                                 "duplicate_strategy": "update"})})
    assert r.json()["updated"] == 1
    c = client.get("/api/contacts").json()["items"][0]
    assert c["first_name"] == "Jonathan" and c["city"] == "Austin"  # blank never overwrites


def test_export_neutralises_formulas(client):
    client.post("/api/contacts", json={"email": "a@b.com", "first_name": "=HYPERLINK(1)"})
    r = client.get("/api/contacts/export")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "'=HYPERLINK(1)" in r.text


def test_bulk_actions(client):
    ids = [client.post("/api/contacts", json={"email": f"u{i}@example.com"}).json()["id"] for i in range(3)]
    assert client.post("/api/contacts/bulk", json={"ids": ids, "action": "add_tag", "value": "vip"}).json()["updated"] == 3
    assert client.get("/api/contacts", params={"tag": "vip"}).json()["total"] == 3
    assert client.post("/api/contacts/bulk", json={"ids": ids[:2], "action": "set_status", "value": "contacted"}).json()["updated"] == 2
    assert client.post("/api/contacts/bulk", json={"ids": ids, "action": "delete"}).json()["updated"] == 3
    assert client.get("/api/contacts").json()["total"] == 0


def test_suppressed_rows_skipped_on_import(client, db):
    from app.models import SuppressionEntry

    db.add(SuppressionEntry(email="jane@xyzbonds.com", reason="unsubscribe"))
    db.commit()
    res = _import(client).json()
    assert res["skipped_suppressed"] == 1 and res["created"] == 1


def test_companies_crud(client):
    r = client.post("/api/companies", json={"name": "Liberty Bail", "website": "https://libertybail.com", "state": "ga"})
    assert r.status_code == 201
    cid = r.json()["id"]
    assert r.json()["domain"] == "libertybail.com" and r.json()["timezone"] == "America/New_York"
    assert client.post("/api/companies", json={"name": "Dup", "website": "libertybail.com"}).status_code == 409
    client.post("/api/contacts", json={"email": "x@libertybail.com", "company_id": cid})
    assert client.get(f"/api/companies/{cid}").json()["contact_count"] == 1
    assert client.patch(f"/api/companies/{cid}", json={"industry": "Bail Bonds"}).json()["industry"] == "Bail Bonds"
    assert client.get("/api/companies", params={"q": "liberty"}).json()["total"] == 1
    assert client.delete(f"/api/companies/{cid}").status_code == 200
    assert client.get("/api/contacts").json()["items"][0]["company_id"] is None


def test_dashboard_empty_state(client):
    r = client.get("/api/dashboard")
    assert r.status_code == 200
    d = r.json()
    assert d["kpis"]["total_contacts"] == 0 and d["kpis"]["sent_today"] == 0
    assert len(d["daily_volume"]) == 30
    assert d["upcoming"] == [] and d["recent_activity"] == []
