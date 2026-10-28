"""Imported lists: every upload is a named group of prospects that can be reused for campaigns, and smart filters.
Prospects are never duplicated, and deleting a list or leaving a campaign never deletes a prospect."""

import io
import json

from openpyxl import Workbook
from sqlalchemy import func, select

from app.models import CampaignContact, Contact, ImportBatch, SuppressionEntry, import_batch_members
from tests.conftest import login, make_user

HEADERS = ["First Name", "Last Name", "Business Name", "Email Address", "Phone", "City", "State", "Industry"]


def texas_rows(n=300, start=0):
    cities = ["Austin", "Houston", "Dallas"]
    return [[f"Owner{i}", f"Last{i}", f"Lone Star Bail {i}", f"owner{i}@txbail{i}.com", f"(512) 555-{i:04d}",
             cities[i % 3], "TX", "Bail Bonds"] for i in range(start, start + n)]


def xlsx(rows) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(HEADERS)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def upload(client, data: bytes, name="texas_bail_bonds.xlsx", **opts):
    mapping = client.post("/api/contacts/import/preview",
                          files={"file": (name, data, "application/octet-stream")}).json()["suggested_mapping"]
    return client.post("/api/contacts/import", files={"file": (name, data, "application/octet-stream")},
                       data={"options": json.dumps({"mapping": mapping, **opts})})


def campaign(client, name="Texas Bail Bonds"):
    r = client.post("/api/campaigns", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_upload_becomes_a_named_list_and_prospects(client, db):
    r = upload(client, xlsx(texas_rows()), list_name="Texas Bail Bonds")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] == 300 and body["list_name"] == "Texas Bail Bonds" and body["list_contacts"] == 300

    lists = client.get("/api/imports").json()
    assert len(lists) == 1
    lst = lists[0]
    assert lst["name"] == "Texas Bail Bonds" and lst["original_filename"] == "texas_bail_bonds.xlsx"
    assert lst["contacts"] == 300 and lst["valid_contacts"] == 300 and lst["duplicates"] == 0
    # the same prospects are in the main Prospects database, filterable by list
    assert client.get("/api/contacts").json()["total"] == 300
    assert client.get(f"/api/contacts?batch_id={lst['id']}&page_size=1").json()["total"] == 300


def test_default_list_name_comes_from_the_file(client):
    upload(client, xlsx(texas_rows(3)), name="florida_bail-bonds.xlsx")
    assert client.get("/api/imports").json()[0]["name"] == "Florida Bail Bonds"


def test_overlapping_upload_reuses_prospects_without_duplicates(client, db):
    upload(client, xlsx(texas_rows(10)), list_name="First")
    # 5 already known + 5 new, plus one repeated row inside the file
    rows = texas_rows(10, start=5) + [texas_rows(1, start=5)[0]]
    rows[0][2] = ""  # a blank cell must not wipe what we already know
    r = upload(client, xlsx(rows), name="second.xlsx", list_name="Second", duplicate_strategy="update")
    assert r.json()["created"] == 5 and r.json()["updated"] == 5
    assert db.scalar(select(func.count()).select_from(Contact)) == 15
    assert db.scalar(select(Contact.company_name).where(Contact.email == "owner5@txbail5.com")) == "Lone Star Bail 5"
    second = next(x for x in client.get("/api/imports").json() if x["name"] == "Second")
    assert second["contacts"] == 10 and second["duplicates"] == 6  # 5 already in the CRM + 1 repeated row
    # with "skip", known prospects are left untouched but still belong to the list
    r = upload(client, xlsx(texas_rows(3)), name="third.xlsx", list_name="Third")
    assert r.json()["created"] == 0 and r.json()["list_contacts"] == 3
    third = db.scalar(select(ImportBatch).where(ImportBatch.name == "Third"))
    outcomes = set(db.scalars(select(import_batch_members.c.outcome).where(import_batch_members.c.batch_id == third.id)))
    assert outcomes == {"existing"}


def test_campaign_from_a_whole_list_without_selecting_anyone(client, db):
    upload(client, xlsx(texas_rows()), list_name="Texas Bail Bonds")
    batch_id = client.get("/api/imports").json()[0]["id"]
    cid = campaign(client)
    dry = client.post(f"/api/campaigns/{cid}/contacts", json={"batch_id": batch_id, "dry_run": True}).json()
    assert dry["summary"]["ready"] == 300
    r = client.post(f"/api/campaigns/{cid}/contacts", json={"batch_id": batch_id})
    assert r.status_code == 200, r.text
    assert r.json()["enrolled"] == 300
    assert db.scalar(select(func.count()).select_from(CampaignContact)) == 300
    lst = client.get(f"/api/imports/{batch_id}").json()
    assert [c["name"] for c in lst["campaigns"]] == ["Texas Bail Bonds"]
    # adding the list again changes nothing
    assert client.post(f"/api/campaigns/{cid}/contacts", json={"batch_id": batch_id}).json()["enrolled"] == 0


def test_upload_straight_into_a_campaign_includes_known_prospects(client, db):
    upload(client, xlsx(texas_rows(4)), list_name="Earlier")
    cid = campaign(client)
    r = upload(client, xlsx(texas_rows(6)), name="new.xlsx", list_name="New", campaign_id=cid)
    assert r.json()["created"] == 2 and r.json()["enrollment"]["enrolled"] == 6
    assert db.scalar(select(func.count()).select_from(CampaignContact)) == 6


def test_rename_delete_and_export_keep_prospects(client, db, app):
    upload(client, xlsx(texas_rows(4)))
    bid = client.get("/api/imports").json()[0]["id"]
    # e.g. edited later in the CRM: a value a spreadsheet would run as a formula
    db.execute(Contact.__table__.update().where(Contact.email == "owner3@txbail3.com")
               .values(company_name='=HYPERLINK("http://evil")'))
    db.commit()
    assert client.patch(f"/api/imports/{bid}", json={"name": "Renamed"}).json()["name"] == "Renamed"

    csv = client.get(f"/api/imports/{bid}/export")
    assert csv.status_code == 200 and "attachment" in csv.headers["content-disposition"]
    assert "'=HYPERLINK" in csv.text  # spreadsheet formula injection neutralised

    make_user(email="viewer@trace.studio", role="viewer")
    viewer = login(app, "viewer@trace.studio")
    assert viewer.get(f"/api/imports/{bid}/export").status_code == 403
    assert viewer.get("/api/contacts/export").status_code == 403
    assert viewer.get("/api/imports").status_code == 200  # viewing is fine

    assert client.delete(f"/api/imports/{bid}").status_code == 200
    assert client.get("/api/imports").json() == []
    assert client.get(f"/api/imports/{bid}").status_code == 404
    assert db.scalar(select(func.count()).select_from(Contact).where(Contact.deleted_at.is_(None))) == 4


def test_list_counts_exclude_suppressed_from_valid(client, db):
    upload(client, xlsx(texas_rows(5)))
    db.add(SuppressionEntry(email="owner1@txbail1.com", reason="unsubscribe", source="test"))
    db.commit()
    assert client.get("/api/imports").json()[0]["valid_contacts"] == 4


def test_smart_filters_count_and_enroll_exactly_the_match(client, db):
    upload(client, xlsx(texas_rows(9)), list_name="TX")
    fl = [["Fay", "Lee", "Sunshine Bail", "fay@sunbail.com", "", "Miami", "FL", "Bail Bonds"],
          ["Gus", "Roe", "Gator Plumbing", "gus@gator.com", "", "Miami", "FL", "Plumbing"]]
    upload(client, xlsx(fl), name="fl.xlsx")
    assert client.get("/api/contacts/count?state=TX&industry=bail").json()["total"] == 9
    assert client.get("/api/contacts/count?state=TX&city=austin").json()["total"] == 3
    assert client.get("/api/contacts/count?industry=Bail Bonds").json()["total"] == 10
    assert client.get("/api/contacts/count?state=fl&industry=plumbing").json()["total"] == 1

    cid = campaign(client)
    r = client.post(f"/api/campaigns/{cid}/contacts",
                    json={"filter": {"state": "TX", "city": "Houston", "industry": "Bail Bonds"}})
    assert r.json()["enrolled"] == 3
    # previous participation
    assert client.get("/api/contacts/count?participation=previous").json()["total"] == 3
    assert client.get("/api/contacts/count?participation=never").json()["total"] == 8
    assert client.get(f"/api/contacts/count?state=TX&not_in_campaign_id={cid}").json()["total"] == 6
    # an empty filter never means "everyone"
    assert client.post(f"/api/campaigns/{cid}/contacts", json={"filter": {}}).status_code == 422


def test_custom_field_filter(client, db):
    upload(client, xlsx(texas_rows(2)))
    db.execute(Contact.__table__.update().where(Contact.email == "owner0@txbail0.com")
               .values(custom_fields={"county": "Travis"}))
    db.commit()
    assert client.post(f"/api/campaigns/{campaign(client)}/contacts",
                       json={"filter": {"custom": {"county": "travis"}}}).json()["enrolled"] == 1
