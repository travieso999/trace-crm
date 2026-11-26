"""Spreadsheet import (CSV/XLSX, header recognition, name splitting, dry-run report) and enrollment dry runs /
per-lead previews. Nothing here sends email or starts a campaign."""

import io
import json

import pytest
from openpyxl import Workbook
from sqlalchemy import func, select

from app.models import CampaignContact, Company, Contact, ScheduledMessage, SentMessage, SuppressionEntry
from app.services.contacts import template_variables
from app.services.csv_import import split_name, suggest_mapping


@pytest.mark.parametrize("headers,expected", [
    (["Client Name", "Business Name", "Email Address", "Website", "Phone #", "City", "State"],
     ["full_name", "company_name", "email", "website", "phone", "city", "state"]),
    (["Owner Name", "Agency", "E-mail", "Web Site", "Phone Number", "Town", "ST"],
     ["full_name", "company_name", "email", "website", "phone", "city", "state"]),
    (["First Name", "Last Name", "Contact Name", "Company", "Email"],
     ["first_name", "last_name", "full_name", "company_name", "email"]),
    (["Bail Bonds Company", "Primary Contact", "Owner Email", "County"],
     ["company_name", "full_name", "email", ""]),
    (["Agency Name", "Bondsman", "Contact E-mail Address", "Company Website URL"],
     ["company_name", "full_name", "email", "website"]),
])
def test_header_recognition(headers, expected):
    assert [suggest_mapping(headers)[h] for h in headers] == expected


@pytest.mark.parametrize("raw,expected", [
    ("John Smith", ("John", "Smith", "name_split")),
    ("Smith, John A.", ("John", "Smith", "name_split")),
    ("Dr. Maria de la Cruz Jr.", ("Maria", "de la Cruz", "name_split")),
    ("  José   O'Neil-Ramírez ", ("José", "O'Neil-Ramírez", "name_split")),
    ("Cher", ("Cher", None, "single_name")),  # never guess a last name
    ("ABC Bail Bonds LLC", (None, None, "name_looks_like_business")),
    ("J&R Bonding", (None, None, "name_looks_like_business")),
    ("info@acme.com", (None, None, "name_looks_like_business")),
    ("", (None, None, None)),
])
def test_split_name_is_conservative(raw, expected):
    assert split_name(raw) == expected


def _xlsx(rows) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Prospects"
    for r in rows:
        ws.append(r)
    ws.append([None] * len(rows[0]))  # trailing blank row is ignored
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


SHEET = [
    ["Client Name", "Business Name", "Email Address", "Website", "Phone", "City", "State", None],
    ["Maria Garcia", "Lone Star Bail Bonds", "Maria@LoneStarBail.com", "lonestarbail.com", 5125550100, "Austin", "TX", None],
    ["Smith, Dave", None, "dave@bluebonnet.com", "bluebonnet.com", None, "Houston", "Texas", None],
    ["Gulf Coast Bail LLC", "Gulf Coast Bail", "info@gulfcoastbail.com", None, None, None, "TX", None],
    ["Tina", "Tina's Bonds", "not-an-email", None, None, None, None, None],
    [None, "No Email Bonds", None, None, None, None, "FL", None],
    ["Maria G.", "Lone Star Bail Bonds", "maria@lonestarbail.com", None, None, None, None, None],
]


def _upload(client, data: bytes, name="prospects.xlsx", **opts):
    mapping = opts.pop("mapping", None) or client.post(
        "/api/contacts/import/preview", files={"file": (name, data, "application/octet-stream")}).json()["suggested_mapping"]
    return client.post("/api/contacts/import", files={"file": (name, data, "application/octet-stream")},
                       data={"options": json.dumps({"mapping": mapping, **opts})})


def test_xlsx_preview_dry_run_report_and_import(client, db):
    data = _xlsx(SHEET)
    p = client.post("/api/contacts/import/preview", files={"file": ("prospects.xlsx", data, "application/octet-stream")})
    assert p.status_code == 200, p.text
    p = p.json()
    assert p["format"] == "xlsx" and p["sheet"] == "Prospects" and p["total_rows"] == 6
    assert p["headers"] == ["Client Name", "Business Name", "Email Address", "Website", "Phone", "City", "State"]
    assert p["suggested_mapping"]["Client Name"] == "full_name" and p["previous_import"] is None
    assert p["sample_rows"][0][4] == "5125550100"  # numeric phone not turned into 5125550100.0

    dry = _upload(client, data, dry_run=True).json()
    assert dry["dry_run"] and dry["contact_ids"] == []
    status = {r["row"]: r["status"] for r in dry["rows"]}
    assert status == {2: "new", 3: "new", 4: "new", 5: "invalid_email", 6: "missing_email", 7: "duplicate_in_file"}
    assert db.scalar(select(func.count()).select_from(Contact)) == 0  # dry run saved nothing
    rows = {r["row"]: r for r in dry["rows"]}
    assert (rows[2]["first_name"], rows[2]["last_name"], rows[2]["company_name"]) == ("Maria", "Garcia", "Lone Star Bail Bonds")
    assert (rows[3]["first_name"], rows[3]["last_name"]) == ("Dave", "Smith")
    # a business in the name column is never turned into a person
    assert rows[4]["first_name"] is None and "name_looks_like_business" in rows[4]["warnings"]
    assert "missing_first_name" in rows[4]["warnings"]
    # no company name in the file → none invented (not even from the website domain)
    assert rows[3]["company_name"] is None and "missing_company_name" in rows[3]["warnings"]
    assert dry["missing_fields"] == 2

    res = _upload(client, data, tags=["tx-prospects"]).json()
    assert res["created"] == 3 and len(res["contact_ids"]) == 3 and res["invalid"] == 2
    maria = db.scalar(select(Contact).where(Contact.email == "maria@lonestarbail.com"))
    assert maria.phone == "5125550100" and maria.state == "TX" and maria.custom_fields["source_name"] == "Maria Garcia"
    dave = db.scalar(select(Contact).where(Contact.email == "dave@bluebonnet.com"))
    assert dave.company_id is None and dave.company_name is None and template_variables(dave)["company_name"] == ""
    assert dave.custom_fields["source_name"] == "Smith, Dave"  # original preserved
    assert list(db.scalars(select(Company.name))) == []  # business names stay on the prospects

    # uploading the same file again is flagged and imports nothing new
    again = client.post("/api/contacts/import/preview", files={"file": ("copy.xlsx", data, "application/octet-stream")}).json()
    assert again["previous_import"]["filename"] == "prospects.xlsx" and again["previous_import"]["created"] == 3
    res2 = _upload(client, data, name="copy.xlsx").json()
    assert res2["created"] == 0 and {r["status"] for r in res2["rows"] if r["row"] in (2, 3, 4)} == {"existing_skipped"}


def test_user_can_correct_mapping_and_csv_still_works(client, db):
    csv_text = "Contact Name,Agency,Email\nPat Lee,Lee Bail,pat@leebail.com\n"
    # user overrides the suggestion: treat "Contact Name" as first name only (no split)
    res = _upload(client, csv_text.encode(), name="x.csv",
                  mapping={"Contact Name": "first_name", "Agency": "company_name", "Email": "email"}).json()
    assert res["created"] == 1
    c = db.scalar(select(Contact))
    assert c.first_name == "Pat Lee" and c.last_name is None and "source_name" not in (c.custom_fields or {})


def test_xlsx_zip_bomb_is_rejected_before_parsing(client):
    import zipfile

    buf = io.BytesIO(_xlsx([["Email"], ["a@example.com"]]))
    with zipfile.ZipFile(buf, "a", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/bomb.bin", bytes(201 * 1024 * 1024))  # ~200 KB compressed, 201 MB expanded
    data = buf.getvalue()
    assert len(data) < 2 * 1024 * 1024
    r = client.post("/api/contacts/import/preview", files={"file": ("big.xlsx", data, "application/octet-stream")})
    assert r.status_code == 422 and "200 MB" in r.json()["detail"]


def test_old_xls_and_bad_xlsx_are_rejected(client):
    r = client.post("/api/contacts/import/preview", files={"file": ("old.xls", b"\xd0\xcf\x11\xe0", "application/vnd.ms-excel")})
    assert r.status_code == 415 and ".xlsx" in r.json()["detail"]
    r = client.post("/api/contacts/import/preview", files={"file": ("broken.xlsx", b"PK\x03\x04garbage", "application/octet-stream")})
    assert r.status_code == 422 and "Excel" in r.json()["detail"]


def test_legacy_domain_named_company_is_not_used_as_company_name(db):
    company = Company(name="acmebail.com", domain="acmebail.com")
    db.add(company)
    db.flush()
    c = Contact(email="x@acmebail.com", first_name="X", company_id=company.id, custom_fields={})
    db.add(c)
    db.commit()
    assert template_variables(c)["company_name"] == ""


STEPS = [
    {"subject": "Quick question for {{company_name}}", "body": "Hi {{first_name}},\n\nI looked at {{company_name}}."},
    {"delay_days": 3, "body": "Hi {{first_name}}, following up.", "reply_in_thread": True},
]


def _setup(client, db):
    client.put("/api/settings/compliance", json={"physical_address": "123 Main St, Suite 4, Austin, TX 78701"})
    ids = {}
    for email, first, company, status in [
        ("ready@a.com", "Ready", "Ready Bail", None),
        ("nocompany@b.com", "Nocomp", None, None),
        ("suppressed@c.com", "Supp", "Supp Bail", None),
        ("won@d.com", "Won", "Won Bail", "won"),
        ("enrolled@e.com", "Enr", "Enr Bail", None),
    ]:
        body = {"email": email, "first_name": first, "company_name": company}
        if status:
            body["lead_status"] = status
        ids[email] = client.post("/api/contacts", json=body).json()["id"]
    db.add(SuppressionEntry(email="suppressed@c.com", reason="unsubscribe"))
    db.commit()
    cid = client.post("/api/campaigns", json={"name": "Prospects"}).json()["id"]
    client.put(f"/api/campaigns/{cid}/steps", json={"steps": STEPS})
    client.post(f"/api/campaigns/{cid}/contacts", json={"contact_ids": [ids["enrolled@e.com"]]})
    return cid, ids


def test_enrollment_dry_run_then_only_ready(client, db):
    cid, ids = _setup(client, db)
    before = db.scalar(select(func.count()).select_from(CampaignContact))
    r = client.post(f"/api/campaigns/{cid}/contacts", json={"contact_ids": list(ids.values()), "dry_run": True}).json()
    assert r["dry_run"] and r["campaign_status"] == "draft"
    by_email = {row["email"]: row for row in r["leads"]}
    assert {e: row["status"] for e, row in by_email.items()} == {
        "ready@a.com": "ready", "nocompany@b.com": "missing_fields", "suppressed@c.com": "suppressed",
        "won@d.com": "closed_stage", "enrolled@e.com": "already_enrolled"}
    assert by_email["nocompany@b.com"]["missing"] == ["company_name"]
    assert r["summary"]["ready"] == 1 and r["summary"]["missing_fields"] == 1
    assert db.scalar(select(func.count()).select_from(CampaignContact)) == before  # dry run enrolled nobody

    r = client.post(f"/api/campaigns/{cid}/contacts", json={"contact_ids": list(ids.values()), "only_ready": True}).json()
    assert r["enrolled"] == 1 and r["skipped_missing_fields"] == 1
    enrolled = set(db.scalars(select(Contact.email).join(CampaignContact, CampaignContact.contact_id == Contact.id)))
    assert enrolled == {"ready@a.com", "enrolled@e.com"}
    # enrollment never starts the campaign or queues/sends anything
    assert client.get(f"/api/campaigns/{cid}").json()["status"] == "draft"
    assert db.scalar(select(func.count()).select_from(ScheduledMessage)) == 0
    assert db.scalar(select(func.count()).select_from(SentMessage)) == 0


def test_import_into_a_campaign_leaves_out_prospects_without_a_name(client, db):
    """How the failed prospects of Nov 2026 got in: an upload sent straight to a campaign added the whole list. Now
    the prospects its emails can't be written for stay out (on the Missing names list), and are added once filled."""
    cid = client.post("/api/campaigns", json={"name": "Kentucky"}).json()["id"]
    client.put(f"/api/campaigns/{cid}/steps", json={"steps": STEPS})
    sheet = (b"Name,Company,Email\n"
             b"Jane Doe,Doe Bail,jane@doebail.com\n"
             b"Lone Star Bail Bonds LLC,Lone Star Bail Bonds,info@lonestarbail.com\n")
    r = _upload(client, sheet, "kentucky.csv", campaign_id=cid)
    assert r.status_code == 200, r.text
    assert r.json()["enrollment"]["enrolled"] == 1 and r.json()["enrollment"]["skipped_missing_fields"] == 1
    assert set(db.scalars(select(Contact.email).join(CampaignContact, CampaignContact.contact_id == Contact.id))) == {
        "jane@doebail.com"}
    assert client.get("/api/contacts/count", params={"missing_name": "true"}).json()["total"] == 1

    # names filled in and uploaded back to the same campaign: now they join
    filled = b"Email,First Name\ninfo@lonestarbail.com,Maria\n"
    r = _upload(client, filled, "filled.csv", campaign_id=cid, duplicate_strategy="update")
    assert r.status_code == 200, r.text
    assert r.json()["enrollment"]["enrolled"] == 1 and r.json()["enrollment"]["skipped_missing_fields"] == 0


def test_missing_data_tab_also_lists_prospects_without_a_company(client, db):
    """The tab holds everyone a campaign would leave out for {{first_name}} or {{company_name}}, says what each one
    lacks, and they leave it once the export is filled in and uploaded back."""
    sheet = (b"First Name,Company,Email\n"
             b"Jane,Doe Bail,jane@doebail.com\n"
             b"George,,anbba@americannationalbail.com\n"
             b",Acme Bail,office@acmebail.com\n")
    assert _upload(client, sheet, "nh.csv").status_code == 200
    linked = Company(name="Linked Bail")
    db.add(linked)
    db.flush()
    db.add(Contact(email="sam@linkedbail.com", first_name="Sam", company_id=linked.id))  # company from the client record
    db.commit()

    listed = client.get("/api/contacts", params={"missing_data": "true"}).json()["items"]
    assert {c["email"]: c["missing_fields"] for c in listed} == {
        "anbba@americannationalbail.com": ["company_name"], "office@acmebail.com": ["first_name"]}
    assert client.get("/api/contacts/count", params={"missing_data": "true"}).json()["total"] == 2

    r = client.get("/api/contacts/missing-names/export")
    assert r.status_code == 200 and "missing-data-" in r.headers["content-disposition"]
    filled = r.text.replace("anbba@americannationalbail.com,George,,,", "anbba@americannationalbail.com,George,,American National Bail,"
                            ).replace("office@acmebail.com,,", "office@acmebail.com,Tom,")
    assert filled != r.text
    assert _upload(client, filled.encode(), "filled.csv", duplicate_strategy="update").status_code == 200
    assert client.get("/api/contacts/count", params={"missing_data": "true"}).json()["total"] == 0


def test_per_lead_personalized_preview(client, db):
    cid, ids = _setup(client, db)
    p = client.get(f"/api/campaigns/{cid}/preview/{ids['ready@a.com']}").json()
    assert p["contact"]["status"] == "ready" and p["footer_problem"] is None
    s1, s2 = p["steps"]
    assert s1["ok"] and s1["subject"] == "Quick question for Ready Bail"
    assert s1["text"].startswith("Hi Ready,\n\nI looked at Ready Bail.") and "/u/" in s1["text"]  # unsubscribe footer
    assert s2["ok"] and s2["threaded"] and s2["subject"] == "Re: Quick question for Ready Bail"

    p = client.get(f"/api/campaigns/{cid}/preview/{ids['nocompany@b.com']}").json()
    assert p["contact"]["status"] == "missing_fields"
    assert p["steps"][0]["ok"] is False and p["steps"][0]["missing"] == ["company_name"]
    assert p["steps"][1]["ok"] is True  # follow-up only needs first_name
    assert db.scalar(select(func.count()).select_from(SentMessage)) == 0


def test_missing_names_collect_export_and_fill_back(client, db):
    """Prospects uploaded without a usable first name collect in one list; exporting it, filling the names and
    re-uploading with "fill in blank fields" stores them, and they leave the list."""
    first = (b"Name,Company,Email\n"
             b"Jane Doe,Doe Bail,jane@doebail.com\n"
             b"Lone Star Bail Bonds LLC,Lone Star Bail Bonds,info@lonestarbail.com\n")
    second = b"Email,Company\noffice@acmebail.com,Acme Bail\n"
    assert _upload(client, first, "first.csv").status_code == 200
    assert _upload(client, second, "second.csv").status_code == 200

    # both uploads feed the same list; Jane has a name and stays out
    assert client.get("/api/contacts/count", params={"missing_name": "true"}).json()["total"] == 2
    listed = client.get("/api/contacts", params={"missing_name": "true"}).json()["items"]
    assert sorted(c["email"] for c in listed) == ["info@lonestarbail.com", "office@acmebail.com"]

    r = client.get("/api/contacts/missing-names/export")
    assert r.status_code == 200 and "missing-data-" in r.headers["content-disposition"]
    lines = r.text.strip().splitlines()
    assert lines[0] == "email,first_name,last_name,company_name,website,job_title,city,state,linkedin_url,original_text"
    assert any(line.startswith("info@lonestarbail.com,,,Lone Star Bail Bonds,") and
               line.endswith("Lone Star Bail Bonds LLC") for line in lines)

    # fill the names in (as Claude would) and upload the file back
    filled = r.text.replace("info@lonestarbail.com,,", "info@lonestarbail.com,Maria,Garza").replace(
        "office@acmebail.com,,", "office@acmebail.com,Tom,")
    result = _upload(client, filled.encode(), "missing-names-filled.csv", duplicate_strategy="update")
    assert result.status_code == 200, result.text
    assert result.json()["updated"] == 2
    assert client.get("/api/contacts/count", params={"missing_name": "true"}).json()["total"] == 0
    maria = db.scalar(select(Contact).where(Contact.email == "info@lonestarbail.com"))
    assert (maria.first_name, maria.last_name) == ("Maria", "Garza")
    assert maria.custom_fields["source_name"] == "Lone Star Bail Bonds LLC"  # the original is not overwritten
