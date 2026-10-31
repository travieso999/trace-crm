"""Video library and MP4 attachments: upload, validation, access, MIME, limits, the sandbox and Gmail paths, previews.
Real Postgres; Google and SMTP are in-process fakes, so nothing leaves the machine."""

import email
import json
import struct
from email import policy

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models import ScheduledMessage, VideoAsset, VideoAssetChunk
from app.providers import mime
from app.providers.base import Attachment, InlineImage, OutgoingMessage, PermanentProviderError
from app.providers.mime import build_mime
from app.providers.smtp import ServerConfig, SmtpImapProvider
from app.services import app_settings, google_oauth, sending
from app.services import video_assets as svc
from tests.conftest import login, make_user
from tests.test_gmail import ALWAYS, FakeGoogle, connect
from tests.test_sending import due_now, make_contacts, outbox, sent_files, setup_campaign, setup_mailbox  # noqa: F401

JPEG = b"\xff\xd8\xff\xe0" + b"thumbnail" * 20
LINK = "https://videos.example.com/watch/42"


def box(kind: bytes, payload: bytes = b"") -> bytes:
    return struct.pack(">I", len(payload) + 8) + kind + payload


def make_mp4(size: int = 4096, brand: bytes = b"isom", track: bytes = b"vide") -> bytes:
    head = box(b"ftyp", brand + b"\0\0\2\0" + brand + b"mp41")
    head += box(b"moov", box(b"hdlr", b"\0" * 8 + track + b"\0" * 16))
    return head + box(b"mdat", bytes(i % 251 for i in range(max(0, size - len(head) - 8))))


def upload(client, data: bytes, filename="demo.mp4", complete=True):
    r = client.post("/api/video-assets", json={"filename": filename, "size_bytes": len(data)})
    assert r.status_code == 201, r.text
    started = r.json()
    for i in range(started["total_chunks"]):
        part = data[i * started["chunk_bytes"]:(i + 1) * started["chunk_bytes"]]
        assert client.put(f"/api/video-assets/{started['id']}/chunks/{i}", content=part).status_code == 200
    if not complete:
        return started
    r = client.post(f"/api/video-assets/{started['id']}/complete")
    assert r.status_code == 200, r.text
    return r.json()["asset"]


def video_step(asset_id, preview=False, attach=True, **kw):
    return {"subject": "Hi {{first_name}}", "body": "Hello {{first_name}} at {{company_name}}", "video_asset_id": asset_id,
            "video_preview": preview, "video_attach": attach, **kw}


def video_parts(m) -> list:
    return [p for p in m.walk() if p.get_content_type() == "video/mp4"]


# --- upload and validation -----------------------------------------------------------------------------------------

def test_chunked_upload_round_trips_the_exact_bytes(client, db):
    data = make_mp4(7 * 1024 * 1024)  # more than one hosted request can carry
    started = upload(client, data, complete=False)
    assert started["total_chunks"] == 3 and started["chunk_bytes"] <= 4_500_000
    r = client.post(f"/api/video-assets/{started['id']}/complete")
    assert r.status_code == 200 and r.json()["asset"]["status"] == "ready"
    assert r.json()["asset"]["size_bytes"] == len(data)
    assert svc.load_attachment(db, started["id"]).data == data


@pytest.mark.parametrize(("data", "message"), [
    (b"this is not a video" * 100, "not an MP4"),
    (make_mp4(brand=b"qt  "), "not an MP4"),
    (make_mp4(brand=b"M4A "), "not an MP4"),
])
def test_files_that_are_not_mp4_are_refused_on_the_first_part(client, db, data, message):
    aid = client.post("/api/video-assets", json={"filename": "x.mp4", "size_bytes": len(data)}).json()["id"]
    r = client.put(f"/api/video-assets/{aid}/chunks/0", content=data)
    assert r.status_code == 422 and message in r.text


@pytest.mark.parametrize("data", [
    make_mp4(4096)[:-100],  # truncated
    box(b"ftyp", b"isom\0\0\2\0isom") + box(b"mdat", b"x" * 100),  # no moov
    make_mp4(4096, track=b"soun"),  # audio only
])
def test_damaged_files_are_refused_and_not_kept(client, db, data):
    aid = upload(client, data, complete=False)["id"]
    r = client.post(f"/api/video-assets/{aid}/complete")
    assert r.status_code == 422
    db.expire_all()
    assert db.get(VideoAsset, aid) is None
    assert db.scalar(select(VideoAssetChunk).where(VideoAssetChunk.asset_id == aid)) is None


@pytest.mark.parametrize("name", ["clip.mov", "clip.mp4.exe", "clip", ".mp4"])
def test_only_mp4_filenames_are_accepted(client, name):
    r = client.post("/api/video-assets", json={"filename": name, "size_bytes": 100})
    assert r.status_code == 422 and ".mp4" in r.text


def test_filename_is_stripped_of_paths_and_header_characters():
    assert svc.clean_filename('C:\\videos\\de"m\r\no.mp4') == "demo.mp4"
    assert svc.clean_filename("../../etc/intro.MP4") == "intro.mp4"


def test_wrong_size_and_missing_parts_are_refused(client):
    data = make_mp4(4 * 1024 * 1024)
    aid = client.post("/api/video-assets", json={"filename": "a.mp4", "size_bytes": len(data)}).json()["id"]
    assert client.put(f"/api/video-assets/{aid}/chunks/0", content=data[:1000]).status_code == 422
    assert client.put(f"/api/video-assets/{aid}/chunks/9", content=data[:1000]).status_code == 422
    assert client.post(f"/api/video-assets/{aid}/complete").status_code == 409


def test_a_chunk_cannot_exceed_the_part_size(client):
    data = make_mp4(4 * 1024 * 1024)
    aid = client.post("/api/video-assets", json={"filename": "a.mp4", "size_bytes": len(data)}).json()["id"]
    assert client.put(f"/api/video-assets/{aid}/chunks/0", content=b"\0" * (svc.CHUNK_BYTES + 1)).status_code == 413


def test_uploading_the_same_file_twice_reuses_the_stored_copy(client, db):
    data = make_mp4()
    first = upload(client, data, "first.mp4")
    started = upload(client, data, "again.mp4", complete=False)
    r = client.post(f"/api/video-assets/{started['id']}/complete").json()
    assert r["duplicate"] is True and r["asset"]["id"] == first["id"]
    assert [a["filename"] for a in client.get("/api/video-assets").json()["items"]] == ["first.mp4"]


def test_one_upload_is_reused_across_campaigns(client, db, outbox, compliance):  # noqa: F811
    asset = upload(client, make_mp4())
    mid = setup_mailbox(client, db)
    c1, c2 = make_contacts(client, "a@acme.com", "b@acme.com")
    setup_campaign(client, mid, [c1], steps=[video_step(asset["id"])])
    setup_campaign(client, mid, [c2], steps=[video_step(asset["id"])])
    sending.send_due()
    db.execute(sending.EmailAccount.__table__.update().values(last_sent_at=None))
    db.commit()
    sending.send_due()
    assert len(sent_files(outbox)) == 2
    assert all(video_parts(m) for m in sent_files(outbox))


def test_thumbnail_must_be_a_small_image(client):
    aid = upload(client, make_mp4())["id"]
    assert client.put(f"/api/video-assets/{aid}/thumbnail", content=b"<svg onload=alert(1)>").status_code == 422
    assert client.put(f"/api/video-assets/{aid}/thumbnail", content=JPEG + b"x" * svc.MAX_THUMBNAIL_BYTES).status_code == 413
    r = client.put(f"/api/video-assets/{aid}/thumbnail", content=JPEG)
    assert r.status_code == 200 and r.json()["has_thumbnail"] is True


@pytest.mark.parametrize("url", ["javascript:alert(1)", "not a link", "https://x.example/a\"onmouseover=1", "ftp://x.example/a"])
def test_video_link_must_be_a_plain_web_address(client, url):
    aid = upload(client, make_mp4())["id"]
    assert client.patch(f"/api/video-assets/{aid}", json={"link_url": url}).status_code == 422


# --- access -------------------------------------------------------------------------------------------------------

def test_no_route_serves_video_bytes(app):
    paths = {p: m for p, m in app.openapi()["paths"].items() if p.startswith("/api/video-assets")}
    assert "/api/video-assets/{asset_id}/complete" in paths  # the map really covers this router
    assert [p for p, methods in paths.items() if "get" in methods] == ["/api/video-assets"]  # the JSON listing only


def test_library_requires_a_session_and_writes_need_a_writer(app, client):
    aid = upload(client, make_mp4())["id"]
    from fastapi.testclient import TestClient

    anon = TestClient(app)
    assert anon.get("/api/video-assets").status_code == 401
    assert anon.post("/api/video-assets", json={"filename": "a.mp4", "size_bytes": 1}).status_code == 401
    assert anon.put(f"/api/video-assets/{aid}/chunks/0", content=b"x").status_code == 401
    assert client.c.put(f"/api/video-assets/{aid}/thumbnail", content=JPEG).status_code == 403  # no CSRF token

    make_user(email="viewer@trace.studio", role="viewer")
    viewer = login(app, email="viewer@trace.studio")
    assert viewer.get("/api/video-assets").status_code == 200
    assert viewer.post("/api/video-assets", json={"filename": "a.mp4", "size_bytes": 1}).status_code == 403
    assert viewer.delete(f"/api/video-assets/{aid}").status_code == 403


def test_listing_never_contains_the_file_or_thumbnail(client):
    data = make_mp4()
    aid = upload(client, data)["id"]
    client.put(f"/api/video-assets/{aid}/thumbnail", content=JPEG)
    r = client.get("/api/video-assets")
    assert set(r.json()["items"][0]) == {"id", "filename", "size_bytes", "status", "link_url", "has_thumbnail", "warnings",
                                         "created_at"}
    assert r.headers["cache-control"] == "no-store"


def test_a_video_in_use_cannot_be_deleted_until_its_campaign_lets_go(client, db):
    aid = upload(client, make_mp4())["id"]
    cid = client.post("/api/campaigns", json={"name": "Uses video"}).json()["id"]
    assert client.put(f"/api/campaigns/{cid}/steps", json={"steps": [video_step(aid)]}).status_code == 200
    r = client.delete(f"/api/video-assets/{aid}")
    assert r.status_code == 409 and "Uses video" in r.text
    assert client.put(f"/api/campaigns/{cid}/steps", json={"steps": [video_step(None, attach=False)]}).status_code == 200
    assert client.delete(f"/api/video-assets/{aid}").status_code == 200
    assert db.scalar(select(VideoAssetChunk).where(VideoAssetChunk.asset_id == aid)) is None


def test_a_deleted_campaign_does_not_keep_a_video_alive(client):
    aid = upload(client, make_mp4())["id"]
    cid = client.post("/api/campaigns", json={"name": "Old"}).json()["id"]
    client.put(f"/api/campaigns/{cid}/steps", json={"steps": [video_step(aid)]})
    assert client.delete(f"/api/campaigns/{cid}").status_code == 200
    assert client.delete(f"/api/video-assets/{aid}").status_code == 200


# --- MIME ---------------------------------------------------------------------------------------------------------

def message(**kw) -> OutgoingMessage:
    return OutgoingMessage(from_email="sam@trace.studio", from_name="Sam", to_email="lead@acme.com", subject="Hi",
                           text="Plain body", html="<p>HTML body</p>", rfc_message_id="<1@trace.studio>",
                           list_unsubscribe_url="https://app.example/u/token", **kw)


def test_attachment_is_a_standard_mime_part_with_the_exact_bytes():
    data = make_mp4(50_000)
    raw = build_mime(message(attachments=[Attachment("Demo reel.mp4", "video/mp4", data)])).as_bytes()
    m = email.message_from_bytes(raw, policy=policy.default)
    assert m.get_content_type() == "multipart/mixed"
    [part] = video_parts(m)
    assert part.get_content_disposition() == "attachment"
    assert part.get_filename() == "Demo reel.mp4"
    assert part["Content-Transfer-Encoding"] == "base64"
    assert part.get_content() == data
    assert m.get_body(("plain",)).get_content().strip() == "Plain body"
    assert "HTML body" in m.get_body(("html",)).get_content()
    assert m["List-Unsubscribe"] == "<https://app.example/u/token>" and m["List-Unsubscribe-Post"]


def test_non_ascii_filename_survives():
    data = make_mp4()
    raw = build_mime(message(attachments=[Attachment("présentation.mp4", "video/mp4", data)])).as_bytes()
    assert video_parts(email.message_from_bytes(raw, policy=policy.default))[0].get_filename() == "présentation.mp4"


def test_inline_thumbnail_is_related_to_the_html_and_not_a_download():
    img = InlineImage("video-1@trace.local", "image/jpeg", JPEG)
    raw = build_mime(message(inline_images=[img], attachments=[Attachment("a.mp4", "video/mp4", make_mp4())])).as_bytes()
    m = email.message_from_bytes(raw, policy=policy.default)
    [image] = [p for p in m.walk() if p.get_content_type() == "image/jpeg"]
    assert image["Content-ID"] == "<video-1@trace.local>" and image.get_content_disposition() == "inline"
    assert [a.get_filename() for a in m.iter_attachments()] == ["a.mp4"]


def test_a_message_without_video_keeps_its_shape():
    m = email.message_from_bytes(build_mime(message()).as_bytes(), policy=policy.default)
    assert m.get_content_type() == "multipart/alternative"


# --- limits -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("provider", ["gmail", "smtp", "sandbox", "microsoft"])
def test_the_largest_allowed_file_really_fits_with_the_largest_thumbnail(provider):
    size = mime.max_attachment_bytes(provider)
    msg = message(attachments=[Attachment("a.mp4", "video/mp4", bytes(size))],
                  inline_images=[InlineImage("t@x", "image/jpeg", bytes(svc.MAX_THUMBNAIL_BYTES))])
    msg.text, msg.html = "x" * 20_000, "x" * 40_000
    raw = build_mime(msg).as_bytes().replace(b"\n", b"\r\n")  # as it travels
    assert len(raw) <= mime.message_limit(provider)


def test_encoded_size_matches_what_is_really_built():
    data = make_mp4(1_000_000)
    with_file = len(build_mime(message(attachments=[Attachment("a.mp4", "video/mp4", data)])).as_bytes().replace(b"\n", b"\r\n"))
    without = len(build_mime(message()).as_bytes().replace(b"\n", b"\r\n"))
    assert abs((with_file - without) - mime.encoded_size(len(data))) < 500


def test_configured_limit_can_go_down_but_never_above_the_provider(client, db):
    app_settings.update_section(db, "video_attachments", {"max_attachment_mb": 5})
    db.commit()
    assert svc.max_attachment_bytes(db, "gmail") == 5_000_000
    app_settings.update_section(db, "video_attachments", {"max_attachment_mb": 500})
    db.commit()
    assert svc.max_attachment_bytes(db, "gmail") == mime.max_attachment_bytes("gmail") < 25_000_000
    assert client.get("/api/video-assets").json()["limits"]["max_upload_bytes"] == mime.max_attachment_bytes("gmail")


def test_settings_section_validates_numbers(client):
    r = client.put("/api/settings/video_attachments", json={"max_attachment_mb": -1})
    assert r.status_code == 422
    assert client.put("/api/settings/video_attachments", json={"unknown": 1}).status_code == 422
    r = client.put("/api/settings/video_attachments", json={"max_attachment_mb": 8})
    assert r.status_code == 200 and r.json()["max_attachment_mb"] == 8


def test_upload_over_the_limit_and_over_the_library_cap_is_refused(client, db):
    r = client.post("/api/video-assets", json={"filename": "big.mp4", "size_bytes": 26_000_000})
    assert r.status_code == 413 and "limit" in r.text
    app_settings.update_section(db, "video_attachments", {"library_max_mb": 1})
    db.commit()
    r = client.post("/api/video-assets", json={"filename": "b.mp4", "size_bytes": 2_000_000})
    assert r.status_code == 413 and "library" in r.text


def test_large_files_get_warnings_and_small_ones_do_not_get_the_large_warning(client, db):
    small = upload(client, make_mp4())
    assert not any("Large attachment" in w for w in small["warnings"])
    app_settings.update_section(db, "video_attachments", {"warn_attachment_mb": 0.001})
    db.commit()
    r = client.get("/api/video-assets").json()["items"][0]
    assert any("Large attachment" in w for w in r["warnings"])


def test_preflight_blocks_a_video_over_the_mailbox_limit(client, db, outbox, compliance):  # noqa: F811
    aid = upload(client, make_mp4(2_000_000))["id"]
    app_settings.update_section(db, "video_attachments", {"max_attachment_mb": 1})
    db.commit()
    mid = setup_mailbox(client, db)
    contacts = make_contacts(client, "a@acme.com")
    cid = setup_campaign(client, mid, contacts, steps=[video_step(aid)], start=False)
    r = client.post(f"/api/campaigns/{cid}/start")
    assert r.status_code == 409
    assert any("attachment limit" in p for p in r.json()["detail"]["problems"])


def test_a_running_campaign_waits_rather_than_sending_without_its_video(client, db, outbox, compliance):  # noqa: F811
    aid = upload(client, make_mp4(2_000_000))["id"]
    mid = setup_mailbox(client, db)
    contacts = make_contacts(client, "a@acme.com")
    setup_campaign(client, mid, contacts, steps=[video_step(aid)])
    app_settings.update_section(db, "video_attachments", {"max_attachment_mb": 1})  # lowered after the start
    db.commit()
    assert sending.send_due() == {"deferred": 1}
    assert not (outbox / "outreach@trace.studio" / "sent").exists()
    sm = db.scalar(select(ScheduledMessage))
    assert sm.status == "queued" and "attachment limit" in sm.last_error


def test_a_preview_without_a_link_blocks_the_campaign(client, db, outbox, compliance):  # noqa: F811
    aid = upload(client, make_mp4())["id"]
    mid = setup_mailbox(client, db)
    cid = setup_campaign(client, mid, make_contacts(client, "a@acme.com"), steps=[video_step(aid, preview=True, attach=False)],
                         start=False)
    r = client.post(f"/api/campaigns/{cid}/start")
    assert r.status_code == 409 and any("video link" in p for p in r.json()["detail"]["problems"])


def test_step_must_name_a_ready_video(client):
    cid = client.post("/api/campaigns", json={"name": "No video"}).json()["id"]
    r = client.put(f"/api/campaigns/{cid}/steps", json={"steps": [video_step(None)]})
    assert r.status_code == 409 and "video" in r.text
    unfinished = upload(client, make_mp4(), complete=False)["id"]
    assert client.put(f"/api/campaigns/{cid}/steps", json={"steps": [video_step(unfinished)]}).status_code == 409


def test_unticking_both_options_removes_the_video_from_the_email(client):
    aid = upload(client, make_mp4())["id"]
    cid = client.post("/api/campaigns", json={"name": "C"}).json()["id"]
    r = client.put(f"/api/campaigns/{cid}/steps", json={"steps": [video_step(aid, attach=False)]})
    assert r.json()["steps"][0]["video_asset_id"] is None


def test_duplicating_a_campaign_keeps_the_video(client):
    aid = upload(client, make_mp4())["id"]
    cid = client.post("/api/campaigns", json={"name": "C"}).json()["id"]
    client.put(f"/api/campaigns/{cid}/steps", json={"steps": [video_step(aid, preview=True)]})
    [step] = client.post(f"/api/campaigns/{cid}/duplicate").json()["steps"]
    assert step["video_asset_id"] == aid and step["video_preview"] and step["video_attach"]


# --- sandbox .eml -------------------------------------------------------------------------------------------------

def test_sandbox_campaign_keeps_personalization_unsubscribe_and_threading(client, db, outbox, compliance):  # noqa: F811
    data = make_mp4(20_000)
    aid = upload(client, data, "Our demo.mp4")["id"]
    mid = setup_mailbox(client, db)
    contacts = make_contacts(client, "owner@acmebail.com")
    steps = [video_step(aid), {**video_step(aid), "delay_days": 2, "subject": "", "reply_in_thread": True}]
    cid = setup_campaign(client, mid, contacts, steps=steps)

    assert sending.send_due() == {"sent": 1}
    due_now(db, cid)
    db.execute(sending.EmailAccount.__table__.update().values(last_sent_at=None))
    db.commit()
    assert sending.send_due() == {"sent": 1}

    first, second = sent_files(outbox)
    assert first["Subject"] == "Hi Lead0"
    text = first.get_body(("plain",)).get_content()
    assert "Hello Lead0 at Co 0" in text and "/u/" in text and "123 Main St" in text
    assert first["List-Unsubscribe"] and first["List-Unsubscribe-Post"]
    for m in (first, second):
        [part] = video_parts(m)
        assert part.get_filename() == "Our demo.mp4" and part.get_content() == data
        assert part.get_content_disposition() == "attachment"
    assert second["In-Reply-To"] == first["Message-ID"] and second["Subject"] == "Re: Hi Lead0"


@pytest.mark.parametrize(("preview", "attach"), [(True, False), (False, True), (True, True)])
def test_each_option_includes_exactly_what_it_says(client, db, outbox, compliance, preview, attach):  # noqa: F811
    data = make_mp4()
    aid = upload(client, data)["id"]
    client.patch(f"/api/video-assets/{aid}", json={"link_url": LINK})
    client.put(f"/api/video-assets/{aid}/thumbnail", content=JPEG)
    mid = setup_mailbox(client, db)
    setup_campaign(client, mid, make_contacts(client, "a@acme.com"), steps=[video_step(aid, preview=preview, attach=attach)])
    assert sending.send_due() == {"sent": 1}
    [m] = sent_files(outbox)
    assert bool(video_parts(m)) is attach
    html = m.get_body(("html",)).get_content()
    text = m.get_body(("plain",)).get_content()
    assert (LINK in html and LINK in text) is preview
    assert ("cid:video-" in html) is preview
    # the link sits above the unsubscribe footer in both parts
    if preview:
        assert html.index(LINK) < html.index("/u/") and text.index(LINK) < text.index("/u/")


def test_preview_without_a_thumbnail_falls_back_to_a_text_link(client, db, outbox, compliance):  # noqa: F811
    aid = upload(client, make_mp4())["id"]
    client.patch(f"/api/video-assets/{aid}", json={"link_url": LINK})
    mid = setup_mailbox(client, db)
    setup_campaign(client, mid, make_contacts(client, "a@acme.com"), steps=[video_step(aid, preview=True, attach=False)])
    sending.send_due()
    [m] = sent_files(outbox)
    html = m.get_body(("html",)).get_content()
    assert LINK in html and "<img" not in html


def test_sandbox_test_send_writes_the_attachment(client, db, outbox, compliance):  # noqa: F811
    data = make_mp4()
    aid = upload(client, data, "demo.mp4")["id"]
    mid = setup_mailbox(client, db)
    r = client.post("/api/campaigns/test-send", json={"subject": "S", "body": "B", "email_account_id": mid,
                                                      "to_email": "admin@trace.studio", "video_asset_id": aid,
                                                      "video_attach": True})
    assert r.status_code == 200 and "demo.mp4" in r.json()["message"]
    [m] = sent_files(outbox)
    assert video_parts(m)[0].get_content() == data and m["Subject"] == "[TEST] S"


def test_time_budget_leaves_the_rest_queued(client, db, outbox, compliance, monkeypatch):  # noqa: F811
    mid = setup_mailbox(client, db)
    setup_campaign(client, mid, make_contacts(client, "a@acme.com", "b@acme.com"))
    monkeypatch.setattr(sending, "SEND_BUDGET_SECONDS", -1)
    assert sending.send_due() == {"left_for_next_pass": 2}
    assert not (outbox / "outreach@trace.studio" / "sent").exists()
    assert {s.status for s in db.scalars(select(ScheduledMessage))} == {"queued"}


# --- previews -----------------------------------------------------------------------------------------------------

def test_editor_preview_shows_filename_size_and_warnings(client):
    aid = upload(client, make_mp4(), "demo.mp4")["id"]
    client.patch(f"/api/video-assets/{aid}", json={"link_url": LINK})
    client.put(f"/api/video-assets/{aid}/thumbnail", content=JPEG)
    p = client.post("/api/campaigns/preview", json={"subject": "S", "body": "Hello", "video_asset_id": aid, "video_preview": True,
                                                    "video_attach": True}).json()
    assert p["attachment"]["filename"] == "demo.mp4" and p["attachment"]["size_bytes"] == 4096
    assert p["attachment"]["content_type"] == "video/mp4"
    assert p["attachment_warnings"] and p["preview_link_missing"] is False
    assert "data:image/jpeg;base64," in p["html"] and "cid:" not in p["html"]


def test_preview_without_video_is_unchanged_and_flags_a_missing_link(client):
    p = client.post("/api/campaigns/preview", json={"subject": "S", "body": "Hello"}).json()
    assert p["attachment"] is None and p["attachment_warnings"] == [] and p["preview_link_missing"] is False
    aid = upload(client, make_mp4())["id"]
    p = client.post("/api/campaigns/preview", json={"subject": "S", "body": "Hello", "video_asset_id": aid,
                                                    "video_preview": True}).json()
    assert p["preview_link_missing"] is True


def test_per_prospect_preview_shows_the_attachment(client):
    aid = upload(client, make_mp4(), "demo.mp4")["id"]
    [contact] = make_contacts(client, "a@acme.com")
    cid = client.post("/api/campaigns", json={"name": "C"}).json()["id"]
    client.put(f"/api/campaigns/{cid}/steps", json={"steps": [video_step(aid)]})
    [step] = client.get(f"/api/campaigns/{cid}/preview/{contact}").json()["steps"]
    assert step["attachment"]["filename"] == "demo.mp4" and step["ok"]


# --- Gmail --------------------------------------------------------------------------------------------------------

class FakeGmailUpload(FakeGoogle):
    """Adds the resumable upload endpoint in front of the regular fake."""

    def __init__(self):
        super().__init__()
        self.uploads: list[dict] = []
        self.started: list[dict] = []
        self.put_response = None

    def handle(self, req: httpx.Request) -> httpx.Response:
        if "/upload/gmail/" in req.url.path and req.method == "POST":
            self.requests.append(req)
            assert req.headers["authorization"].startswith("Bearer at-")
            assert req.url.params["uploadType"] == "resumable"
            assert req.headers["x-upload-content-type"] == "message/rfc822"
            self.started.append({"meta": json.loads(req.content), "length": int(req.headers["x-upload-content-length"])})
            return httpx.Response(200, headers={"Location": "https://gmail.googleapis.com/upload/session?upload_id=abc"})
        if req.url.path == "/upload/session":
            self.requests.append(req)
            if self.put_response:
                return self.put_response(req)
            assert req.headers["content-type"] == "message/rfc822"
            self.n += 1
            mid, tid = f"g-{self.n}", self.started[-1]["meta"].get("threadId") or f"thread-{self.n}"
            self.uploads.append({"id": mid, "threadId": tid, "raw": req.content})
            return httpx.Response(200, json={"id": mid, "threadId": tid, "labelIds": ["SENT"]})
        return super().handle(req)

    def uploaded_mime(self, i=-1):
        return email.message_from_bytes(self.uploads[i]["raw"], policy=policy.default)


@pytest.fixture
def gmail(monkeypatch):
    fake = FakeGmailUpload()
    s = get_settings()
    monkeypatch.setattr(s, "google_client_id", "cid.apps.googleusercontent.com")
    monkeypatch.setattr(s, "google_client_secret", "shh")
    monkeypatch.setattr(s, "public_url", "http://localhost:3000")
    monkeypatch.setattr(s, "real_sending", "disabled")
    monkeypatch.setattr(google_oauth, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(fake.handle)))
    return fake


def connected_gmail(client, db) -> str:
    connect(client)
    mid = client.get("/api/email-accounts").json()[0]["id"]
    client.patch(f"/api/email-accounts/{mid}", json={"schedule": ALWAYS})
    db.execute(sending.EmailAccount.__table__.update().values(min_interval_seconds=0))
    db.commit()
    return mid


def test_gmail_test_email_uploads_the_real_file_to_an_allowed_inbox(client, gmail, db, monkeypatch, compliance):
    monkeypatch.setattr(get_settings(), "real_sending", "test_only")
    monkeypatch.setattr(get_settings(), "allowed_test_recipients", "me@gmail.com")
    data = make_mp4(6_000_000)  # the case the JSON endpoint is not meant for
    aid = upload(client, data, "demo.mp4")["id"]
    mid = connected_gmail(client, db)
    body = {"subject": "S", "body": "B", "email_account_id": mid, "video_asset_id": aid, "video_attach": True}

    assert client.post("/api/campaigns/test-send", json={**body, "to_email": "stranger@gmail.com"}).status_code == 403
    assert gmail.uploads == []

    r = client.post("/api/campaigns/test-send", json={**body, "to_email": "me@gmail.com"})
    assert r.status_code == 200, r.text
    assert "demo.mp4" in r.json()["message"] and "6.0 MB" in r.json()["message"]
    m = gmail.uploaded_mime()
    assert m["To"] == "me@gmail.com"
    [part] = video_parts(m)
    assert part.get_content() == data and part.get_content_disposition() == "attachment"
    assert gmail.started[-1]["length"] == len(gmail.uploads[-1]["raw"])
    assert gmail.sent == []  # never through the JSON endpoint


def test_gmail_test_email_respects_real_sending_disabled(client, gmail, db, monkeypatch, compliance):
    monkeypatch.setattr(get_settings(), "allowed_test_recipients", "me@gmail.com")
    aid = upload(client, make_mp4())["id"]
    mid = connected_gmail(client, db)
    r = client.post("/api/campaigns/test-send", json={"subject": "S", "body": "B", "email_account_id": mid, "video_asset_id": aid,
                                                      "video_attach": True, "to_email": "me@gmail.com"})
    assert r.status_code == 409 and "REAL_SENDING=disabled" in r.text
    assert gmail.uploads == [] and gmail.started == []
    assert get_settings().real_sending == "disabled"


def test_gmail_refuses_a_file_over_the_limit_before_contacting_google(client, gmail, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "real_sending", "test_only")
    monkeypatch.setattr(get_settings(), "allowed_test_recipients", "me@gmail.com")
    aid = upload(client, make_mp4(2_000_000))["id"]
    mid = connected_gmail(client, db)
    app_settings.update_section(db, "video_attachments", {"max_attachment_mb": 1})
    db.commit()
    r = client.post("/api/campaigns/test-send", json={"subject": "S", "body": "B", "email_account_id": mid, "video_asset_id": aid,
                                                      "video_attach": True, "to_email": "me@gmail.com"})
    assert r.status_code == 409 and "attachment limit" in r.text
    assert gmail.started == []


def test_gmail_campaign_keeps_the_thread_on_followups_with_attachments(client, gmail, db, monkeypatch, compliance):
    monkeypatch.setattr(get_settings(), "real_sending", "test_only")
    monkeypatch.setattr(get_settings(), "allowed_test_recipients", "qa@tracestudio.dev")
    data = make_mp4(100_000)
    aid = upload(client, data)["id"]
    mid = connected_gmail(client, db)
    contacts = make_contacts(client, "qa@tracestudio.dev")
    steps = [video_step(aid), {**video_step(aid), "delay_days": 1, "subject": "", "reply_in_thread": True}]
    cid = setup_campaign(client, mid, contacts, steps=steps)
    assert sending.send_due() == {"sent": 1}
    due_now(db, cid)
    assert sending.send_due() == {"sent": 1}
    assert gmail.started[0]["meta"] == {}
    assert gmail.started[1]["meta"]["threadId"] == gmail.uploads[0]["threadId"]
    assert video_parts(gmail.uploaded_mime(1))[0].get_content() == data


def test_gmail_messages_without_a_video_still_use_the_json_endpoint(client, gmail, db, monkeypatch, compliance):
    monkeypatch.setattr(get_settings(), "real_sending", "test_only")
    monkeypatch.setattr(get_settings(), "allowed_test_recipients", "me@gmail.com")
    mid = connected_gmail(client, db)
    r = client.post("/api/campaigns/test-send", json={"subject": "S", "body": "B", "email_account_id": mid, "to_email": "me@gmail.com"})
    assert r.status_code == 200
    assert len(gmail.sent) == 1 and gmail.started == []


def test_gmail_timeout_during_upload_is_reconciled_not_resent(client, gmail, db, monkeypatch, compliance):
    monkeypatch.setattr(get_settings(), "real_sending", "test_only")
    monkeypatch.setattr(get_settings(), "allowed_test_recipients", "qa@tracestudio.dev")
    aid = upload(client, make_mp4())["id"]
    mid = connected_gmail(client, db)
    setup_campaign(client, mid, make_contacts(client, "qa@tracestudio.dev"), steps=[video_step(aid)])

    def timeout(req):
        raise httpx.ReadTimeout("no answer", request=req)

    gmail.put_response = timeout
    assert sending.send_due() == {"unknown": 1}
    assert db.scalar(select(ScheduledMessage)).status == "unknown"


# --- SMTP ---------------------------------------------------------------------------------------------------------

class FakeConn:
    def __init__(self, size=None):
        self.esmtp_features = {"size": str(size)} if size else {}
        self.data_calls = []

    def mail(self, _):
        return 250, b"OK"

    def rcpt(self, _):
        return 250, b"OK"

    def data(self, raw):
        self.data_calls.append(raw)
        return 250, b"OK"

    def quit(self):
        pass


def smtp_provider(monkeypatch, conn) -> tuple[SmtpImapProvider, list[bytes]]:
    cfg = ServerConfig("smtp.x", 465, "ssl", "imap.x", 993, "u", "p")
    p = SmtpImapProvider("sam@trace.studio", cfg, send_block_reason=lambda _: None)
    copies: list[bytes] = []
    monkeypatch.setattr(p, "_smtp", lambda: conn)
    monkeypatch.setattr(p, "_save_sent_copy", copies.append)
    return p, copies


def test_smtp_sends_the_file_but_the_sent_copy_leaves_it_out(monkeypatch):
    conn = FakeConn()
    p, copies = smtp_provider(monkeypatch, conn)
    data = make_mp4(50_000)
    p.send(message(attachments=[Attachment("a.mp4", "video/mp4", data)]))
    assert video_parts(email.message_from_bytes(conn.data_calls[0], policy=policy.default))[0].get_content() == data
    [copy] = copies
    m = email.message_from_bytes(copy, policy=policy.default)
    assert not video_parts(m) and m["Message-ID"] == "<1@trace.studio>"


def test_smtp_respects_the_size_the_server_announces(monkeypatch):
    conn = FakeConn(size=10_000)
    p, _ = smtp_provider(monkeypatch, conn)
    with pytest.raises(PermanentProviderError, match="accepts at most"):
        p.send(message(attachments=[Attachment("a.mp4", "video/mp4", make_mp4(50_000))]))
    assert conn.data_calls == []
    p.send(message())  # an ordinary small email is unaffected
    assert len(conn.data_calls) == 1
