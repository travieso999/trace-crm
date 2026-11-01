"""Custom video thumbnails and the email block: image validation, public URLs, persistence, what recipients actually get.
Real Postgres; the sandbox and a fake Gmail stand in for real mail, so nothing leaves the machine."""

import base64
import io
import re
from html.parser import HTMLParser

import pytest
from PIL import Image
from sqlalchemy import select

from app.core.config import get_settings
from app.models import EmailImage, VideoAsset
from app.services import sending
from app.services.rendering import DEFAULT_VIDEO_CTA, VideoLink, render_email
from tests.test_sending import make_contacts, outbox, sent_files, setup_campaign, setup_mailbox  # noqa: F401
from tests.test_video_attachments import FakeGmailUpload, connected_gmail, gmail, make_mp4, upload, video_parts  # noqa: F401

LINK = "https://videos.example.com/watch/42"
PUBLIC = "https://crm.example.com"
RED = (200, 30, 30)
BAR = 'bgcolor="#14213d"'  # appears once per video block


def picture(size=(1280, 720), fmt="PNG", mode="RGB", color=RED, **save) -> bytes:
    out = io.BytesIO()
    Image.new(mode, size, color).save(out, fmt, **save)
    return out.getvalue()


def put_image(client, data, filename="my thumb.png"):
    return client.c.post("/api/video-assets/thumbnails", params={"filename": filename}, content=data,
                         headers={"X-CSRF-Token": client.csrf})


def video_with_link(client) -> str:
    aid = upload(client, make_mp4())["id"]
    assert client.patch(f"/api/video-assets/{aid}", json={"link_url": LINK}).status_code == 200
    return aid


def step(aid, thumb=None, **kw):
    return {"subject": "Hi {{first_name}}", "body": "Hello {{first_name}} at {{company_name}}", "video_asset_id": aid,
            "video_preview": True, "video_attach": False, "video_thumbnail_id": thumb, **kw}


@pytest.fixture
def public_site(monkeypatch):
    monkeypatch.setattr(get_settings(), "public_url", PUBLIC)


def stored(db, image_id) -> Image.Image:
    db.expire_all()
    return Image.open(io.BytesIO(db.get(EmailImage, image_id).data))


# --- upload and validation -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP"])
def test_supported_formats_become_a_1280x720_jpeg(client, db, fmt):
    r = put_image(client, picture(fmt=fmt), f"thumb.{fmt.lower()}")
    assert r.status_code == 201, r.text
    t = r.json()
    assert (t["width"], t["height"]) == (1280, 720) and t["filename"] == f"thumb.{fmt.lower()}"
    assert stored(db, t["id"]).format == "JPEG"


def test_larger_16_9_images_are_scaled_down(client):
    assert put_image(client, picture((1920, 1080))).json()["width"] == 1280


def test_transparent_png_is_flattened_on_white(client, db):
    t = put_image(client, picture(mode="RGBA", color=(0, 0, 0, 0))).json()
    r, g, b = stored(db, t["id"]).convert("RGB").getpixel((10, 10))
    assert min(r, g, b) > 240


def test_play_button_is_baked_into_the_center_of_the_image(client, db):
    img = stored(db, put_image(client, picture()).json()["id"]).convert("RGB")
    r, g, b = img.getpixel((640, 360 - 75))  # the white disc, away from the triangle
    assert min(r, g, b) > 225
    r, g, b = img.getpixel((640 + 20, 360))  # the navy triangle
    assert max(r, g, b) < 90
    r, g, b = img.getpixel((30, 30))  # the picture itself is untouched
    assert r > 170 and g < 60 and b < 60


def test_metadata_in_the_upload_is_not_kept(client, db):
    exif = Image.Exif()
    exif[0x010E] = "secret description"
    t = put_image(client, picture(fmt="JPEG", exif=exif)).json()
    assert b"secret description" not in db.get(EmailImage, t["id"]).data


@pytest.mark.parametrize(("data", "message"), [
    (picture((800, 800)), "16:9"),
    (picture((1280, 960)), "16:9"),
    (picture((320, 180)), "too small"),
    (picture((6400, 3600)), "at most"),
    (b"not an image at all", "valid"),
    (b"<svg xmlns='http://www.w3.org/2000/svg' onload='alert(1)'/>", "valid"),
    (picture(fmt="GIF"), "valid"),
    (picture(fmt="BMP"), "valid"),
    (picture()[:200], "valid"),
], ids=["square", "4x3", "tiny", "huge", "not-an-image", "svg", "gif", "bmp", "truncated"])
def test_bad_images_are_refused_with_a_useful_message(client, db, data, message):
    r = put_image(client, data)
    assert r.status_code == 422 and message in r.text
    assert db.scalar(select(EmailImage)) is None


def test_oversized_and_empty_uploads_are_refused(client):
    assert put_image(client, b"\0" * (3 * 1024 * 1024 + 1)).status_code == 413
    assert put_image(client, b"").status_code == 422


def test_filename_cannot_carry_a_path_or_markup(client):
    t = put_image(client, picture(), r"..\..\windows\sy<s>tem32.png").json()
    assert t["filename"] == "system32.png"


def test_uploading_needs_a_session_a_writer_and_a_csrf_token(app, client):
    from fastapi.testclient import TestClient

    from tests.conftest import login, make_user

    assert TestClient(app).post("/api/video-assets/thumbnails", content=picture()).status_code == 401
    assert client.c.post("/api/video-assets/thumbnails", content=picture()).status_code == 403
    make_user(email="viewer@trace.studio", role="viewer")
    viewer = login(app, email="viewer@trace.studio")
    assert put_image(viewer, picture()).status_code == 403


# --- public image route --------------------------------------------------------------------------------------------

def test_public_link_serves_the_image_without_a_login(app, client):
    from fastapi.testclient import TestClient

    t = put_image(client, picture()).json()
    r = TestClient(app).get(t["url"])
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert "immutable" in r.headers["cache-control"] and r.headers["x-content-type-options"] == "nosniff"
    assert r.content[:3] == b"\xff\xd8\xff"


@pytest.mark.parametrize("token", ["nope", "nope.jpg", "bm9wZQ.bm9wZQ.jpg", "..%2f..%2fetc%2fpasswd"])
def test_public_link_refuses_anything_unsigned(app, token):
    from fastapi.testclient import TestClient

    assert TestClient(app).get(f"/u/thumb/{token}").status_code == 404


def test_an_image_id_alone_or_a_token_for_another_purpose_opens_nothing(app, client):
    from fastapi.testclient import TestClient

    from app.core.crypto import sign

    t = put_image(client, picture()).json()
    anon = TestClient(app)
    assert anon.get(f"/u/thumb/{t['id']}.jpg").status_code == 404
    assert anon.get(f"/u/thumb/{sign(t['id'], 'unsubscribe')}.jpg").status_code == 404


def test_the_video_itself_has_no_public_route(app, client, db):
    from fastapi.testclient import TestClient

    from app.core.crypto import sign

    aid = video_with_link(client)
    assert TestClient(app).get(f"/u/thumb/{sign(aid, 'email-image')}.jpg").status_code == 404


# --- saved with the step -------------------------------------------------------------------------------------------

def test_thumbnail_and_button_text_are_saved_with_the_step(client):
    aid = video_with_link(client)
    t = put_image(client, picture()).json()
    cid = client.post("/api/campaigns", json={"name": "C"}).json()["id"]
    r = client.put(f"/api/campaigns/{cid}/steps", json={"steps": [step(aid, t["id"], video_cta_text="  See   it  ")]})
    assert r.status_code == 200, r.text
    [s] = client.get(f"/api/campaigns/{cid}").json()["steps"]  # as when reopening the campaign
    assert s["video_thumbnail_id"] == t["id"] and s["video_cta_text"] == "See it"
    assert client.get(f"/api/video-assets/thumbnails/{t['id']}").json()["filename"] == "my thumb.png"


def test_replacing_and_removing_a_thumbnail(client):
    aid = video_with_link(client)
    client.put(f"/api/video-assets/{aid}/thumbnail", content=picture((320, 180), "JPEG", color=(10, 200, 10)))
    first, second = put_image(client, picture()).json(), put_image(client, picture(color=(10, 10, 200))).json()
    cid = client.post("/api/campaigns", json={"name": "C"}).json()["id"]

    def preview(thumb):
        client.put(f"/api/campaigns/{cid}/steps", json={"steps": [step(aid, thumb)]})
        s = client.get(f"/api/campaigns/{cid}").json()["steps"][0]
        assert s["video_thumbnail_id"] == thumb
        html = client.post("/api/campaigns/preview", json={"subject": "S", "body": "B", "video_asset_id": aid,
                                                           "video_preview": True, "video_thumbnail_id": thumb}).json()["html"]
        return re.search(r'src="data:image/jpeg;base64,([^"]+)"', html).group(1)

    a, b, auto = preview(first["id"]), preview(second["id"]), preview(None)
    assert len({a, b, auto}) == 3  # replacing changes it, removing goes back to the video's own thumbnail
    colors = [Image.open(io.BytesIO(base64.b64decode(x))).convert("RGB").getpixel((5, 5)) for x in (a, b, auto)]
    assert colors[0][0] > 150 and colors[1][2] > 150 and colors[2][1] > 150


def test_removing_a_thumbnail_keeps_it_for_emails_already_delivered(app, client, db):
    from fastapi.testclient import TestClient

    aid = video_with_link(client)
    t = put_image(client, picture()).json()
    cid = client.post("/api/campaigns", json={"name": "C"}).json()["id"]
    client.put(f"/api/campaigns/{cid}/steps", json={"steps": [step(aid, t["id"])]})
    client.put(f"/api/campaigns/{cid}/steps", json={"steps": [step(aid, None)]})
    assert TestClient(app).get(t["url"]).status_code == 200


def test_a_step_cannot_use_a_thumbnail_that_does_not_exist(client):
    aid = video_with_link(client)
    cid = client.post("/api/campaigns", json={"name": "C"}).json()["id"]
    r = client.put(f"/api/campaigns/{cid}/steps", json={"steps": [step(aid, "00000000-0000-0000-0000-000000000000")]})
    assert r.status_code == 409 and "thumbnail" in r.text


def test_two_emails_can_use_one_video_with_different_thumbnails(client, db, outbox, compliance):  # noqa: F811
    aid = video_with_link(client)
    a, b = put_image(client, picture()).json(), put_image(client, picture(color=(10, 10, 200))).json()
    mid = setup_mailbox(client, db)
    steps = [step(aid, a["id"]), {**step(aid, b["id"]), "delay_days": 1, "subject": "", "reply_in_thread": True}]
    cid = setup_campaign(client, mid, make_contacts(client, "a@acme.com"), steps=steps)
    assert [s["video_thumbnail_id"] for s in client.get(f"/api/campaigns/{cid}").json()["steps"]] == [a["id"], b["id"]]


def test_duplicating_a_campaign_keeps_the_thumbnail(client):
    aid = video_with_link(client)
    t = put_image(client, picture()).json()
    cid = client.post("/api/campaigns", json={"name": "C"}).json()["id"]
    client.put(f"/api/campaigns/{cid}/steps", json={"steps": [step(aid, t["id"], video_cta_text="Go")]})
    [s] = client.post(f"/api/campaigns/{cid}/duplicate").json()["steps"]
    assert s["video_thumbnail_id"] == t["id"] and s["video_cta_text"] == "Go"


def test_automatic_thumbnails_get_the_play_button_and_keep_any_shape(client, db):
    aid = video_with_link(client)
    assert client.put(f"/api/video-assets/{aid}/thumbnail", content=picture((360, 640), "JPEG")).status_code == 200
    db.expire_all()
    asset = db.get(VideoAsset, aid)
    assert (asset.thumbnail_image.width, asset.thumbnail_image.height) == (360, 640)
    assert client.get("/api/video-assets").json()["items"][0]["thumbnail_url"].startswith("/u/thumb/")


# --- the email block -----------------------------------------------------------------------------------------------

class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.text = [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)


def parse(html: str) -> Tags:
    p = Tags()
    p.feed(html)
    return p


def block(**kw) -> str:
    video = VideoLink(LINK, kw.pop("image", "https://crm.example.com/u/thumb/t.jpg"), kw.pop("cta", DEFAULT_VIDEO_CTA))
    return render_email("S", kw.pop("body", "Hello"), {}, video=video, footer="Footer").html


def test_block_is_email_safe():
    html = block()
    for banned in ("background-image", "background:url", "position:", "<script", "<style", "float:", "<video", "flex"):
        assert banned not in html.lower(), banned
    tags = parse(html).tags
    table = next(a for t, a in tags if t == "table")
    assert table["role"] == "presentation" and table["cellpadding"] == "0"
    img = next(a for t, a in tags if t == "img")
    assert img["width"] == "600" and "height:auto" in img["style"] and "width:100%" in img["style"]
    assert img["alt"] == DEFAULT_VIDEO_CTA


def test_desktop_outlook_gets_a_fixed_width_wrapper_that_is_balanced():
    html = block()
    assert '<!--[if mso]><table role="presentation" width="600"' in html
    assert html.count("<!--[if mso]>") == 2 and html.count("<![endif]-->") == 2
    assert html.index('<!--[if mso]><table') < html.index(BAR) < html.index("<!--[if mso]></td>")


def test_image_and_bar_both_link_to_the_video_and_the_url_is_not_shown():
    p = parse(block())
    links = [a["href"] for t, a in p.tags if t == "a"]
    assert links == [LINK, LINK]
    assert LINK not in "".join(p.text)


def test_bar_is_navy_with_white_text():
    html = block()
    assert 'bgcolor="#14213d"' in html and "background-color:#14213d" in html and "color:#ffffff" in html
    assert "Watch Our 60-Second Demo" in html


def test_button_text_can_be_customised_and_is_escaped():
    assert "See it &lt;now&gt; &amp; more" in block(cta="See it <now> & more")
    assert "<now>" not in block(cta="See it <now> & more")


def test_plain_text_part_has_the_link():
    r = render_email("S", "Hello", {}, video=VideoLink(LINK), footer="Footer")
    assert f"{DEFAULT_VIDEO_CTA}: {LINK}" in r.text and r.text.index(LINK) < r.text.index("Footer")


def test_without_an_image_the_bar_still_links():
    p = parse(block(image=None))
    assert not [t for t, _ in p.tags if t == "img"] and [a["href"] for t, a in p.tags if t == "a"] == [LINK]


# --- the placeholder -----------------------------------------------------------------------------------------------

BODY = "Hi {{first_name}} at {{company_name}},\n\nLook:\n\n**[INSERT VIDEO THUMBNAIL HERE]**\n\nOpen to it?"
VARS = {"first_name": "Ana", "company_name": "Acme"}


def test_placeholder_becomes_the_block_once_in_that_position_and_keeps_personalization():
    r = render_email("S", BODY, VARS, video=VideoLink(LINK, "https://x/t.jpg"), footer="Footer")
    assert "INSERT VIDEO" not in r.html and "INSERT VIDEO" not in r.text and "**" not in r.html
    assert "Hi Ana at Acme" in r.html and "{{" not in r.html
    assert r.html.count(BAR) == 1
    assert r.html.index("Look:") < r.html.index("<table") < r.html.index("Open to it?") < r.html.index("Footer")
    assert r.text.index("Look:") < r.text.index(LINK) < r.text.index("Open to it?")


@pytest.mark.parametrize("marker", ["{{video}}", "{{ video }}", "[INSERT VIDEO THUMBNAIL HERE]", "**[insert video thumbnail here]**"])
def test_every_form_of_the_marker_works(marker):
    r = render_email("S", f"A\n\n{marker}\n\nB", {}, video=VideoLink(LINK))
    assert r.html.index(">A<") < r.html.index("<table") < r.html.index(">B<")


def test_several_markers_never_duplicate_the_block():
    body = "A\n\n**[INSERT VIDEO THUMBNAIL HERE]**\n\nB\n\n{{video}}\n\nC"
    r = render_email("S", body, {}, video=VideoLink(LINK))
    assert r.html.count(BAR) == 1 and r.text.count(LINK) == 1 and "INSERT" not in r.text


def test_placeholder_disappears_when_there_is_no_preview():
    r = render_email("S", BODY, VARS, footer="Footer")
    assert "INSERT VIDEO" not in r.html and "INSERT VIDEO" not in r.text and "<table" not in r.html
    assert "Look:" in r.text and "Open to it?" in r.text


def test_video_marker_does_not_count_as_a_missing_variable(client, db, outbox, compliance):  # noqa: F811
    aid = video_with_link(client)
    mid = setup_mailbox(client, db)
    setup_campaign(client, mid, make_contacts(client, "a@acme.com"),
                   steps=[{**step(aid), "body": "Hello {{first_name}}\n\n{{video}}\n\nBye"}])
    assert sending.send_due() == {"sent": 1}


# --- what is sent --------------------------------------------------------------------------------------------------

def sent_image_src(html: str) -> str:
    return next(a["src"] for t, a in parse(html).tags if t == "img")


def test_on_a_public_site_the_image_is_a_public_https_link_not_a_part_of_the_email(client, db, outbox, compliance, public_site):  # noqa: F811
    data = make_mp4(30_000)
    aid = upload(client, data)["id"]
    client.patch(f"/api/video-assets/{aid}", json={"link_url": LINK})
    t = put_image(client, picture()).json()
    mid = setup_mailbox(client, db)
    setup_campaign(client, mid, make_contacts(client, "a@acme.com"), steps=[{**step(aid, t["id"]), "video_attach": True}])
    assert sending.send_due() == {"sent": 1}
    [m] = sent_files(outbox)
    html = m.get_body(("html",)).get_content()
    assert sent_image_src(html) == f"{PUBLIC}{t['url']}"
    assert "localhost" not in sent_image_src(html) and "/api/" not in sent_image_src(html)
    assert html.count(BAR) == 1 and html.count("<img") == 1
    assert not [p for p in m.walk() if p.get_content_type().startswith("image/")]
    [video] = video_parts(m)  # both options together: the MP4 is unchanged and is the only attachment
    assert video.get_content() == data and [a.get_filename() for a in m.iter_attachments()] == [video.get_filename()]


def test_without_a_public_site_the_image_travels_inside_the_email_so_it_never_links_to_localhost(client, db, outbox, compliance):  # noqa: F811
    aid = video_with_link(client)
    t = put_image(client, picture()).json()
    mid = setup_mailbox(client, db)
    setup_campaign(client, mid, make_contacts(client, "a@acme.com"), steps=[step(aid, t["id"])])
    assert sending.send_due() == {"sent": 1}
    [m] = sent_files(outbox)
    html = m.get_body(("html",)).get_content()
    assert sent_image_src(html).startswith("cid:") and "localhost" not in sent_image_src(html)
    [image] = [p for p in m.walk() if p.get_content_type() == "image/jpeg"]
    assert image.get_content_disposition() == "inline" and not list(m.iter_attachments())


def test_each_option_works_independently_with_a_custom_thumbnail(client, db, outbox, compliance, public_site):  # noqa: F811
    aid = video_with_link(client)
    t = put_image(client, picture()).json()
    mid = setup_mailbox(client, db)
    setup_campaign(client, mid, make_contacts(client, "a@acme.com"),
                   steps=[{**step(aid, t["id"]), "video_preview": False, "video_attach": True}])
    assert sending.send_due() == {"sent": 1}
    [m] = sent_files(outbox)
    assert video_parts(m) and "<img" not in m.get_body(("html",)).get_content()


def test_preview_and_test_email_use_the_same_thumbnail_and_text(app, client, db, outbox, compliance, public_site):  # noqa: F811
    from fastapi.testclient import TestClient

    aid = video_with_link(client)
    t = put_image(client, picture()).json()
    mid = setup_mailbox(client, db)
    body = {"subject": "S", "body": "B", "video_asset_id": aid, "video_preview": True, "video_thumbnail_id": t["id"],
            "video_cta_text": "See the demo"}
    p = client.post("/api/campaigns/preview", json=body).json()
    shown = base64.b64decode(re.search(r'src="data:image/jpeg;base64,([^"]+)"', p["html"]).group(1))
    assert "See the demo" in p["html"]

    r = client.post("/api/campaigns/test-send", json={**body, "email_account_id": mid, "to_email": "admin@trace.studio"})
    assert r.status_code == 200, r.text
    [m] = sent_files(outbox)
    html = m.get_body(("html",)).get_content()
    assert "See the demo" in html
    delivered = TestClient(app).get(sent_image_src(html).removeprefix(PUBLIC)).content
    assert delivered == shown  # byte for byte what the preview showed


def test_gmail_send_with_custom_thumbnail_and_attachment(client, gmail, db, monkeypatch, compliance, public_site):  # noqa: F811
    monkeypatch.setattr(get_settings(), "real_sending", "test_only")
    monkeypatch.setattr(get_settings(), "allowed_test_recipients", "me@gmail.com")
    data = make_mp4(6_000_000)
    aid = upload(client, data, "demo.mp4")["id"]
    client.patch(f"/api/video-assets/{aid}", json={"link_url": LINK})
    t = put_image(client, picture()).json()
    mid = connected_gmail(client, db)
    r = client.post("/api/campaigns/test-send", json={
        "subject": "S", "body": "Hi\n\n**[INSERT VIDEO THUMBNAIL HERE]**\n\nBye", "email_account_id": mid, "to_email": "me@gmail.com",
        "video_asset_id": aid, "video_preview": True, "video_attach": True, "video_thumbnail_id": t["id"]})
    assert r.status_code == 200, r.text
    m = gmail.uploaded_mime()
    html = m.get_body(("html",)).get_content()
    assert sent_image_src(html) == f"{PUBLIC}{t['url']}" and "INSERT VIDEO" not in html and html.count(BAR) == 1
    assert video_parts(m)[0].get_content() == data
    assert not [p for p in m.walk() if p.get_content_type().startswith("image/")]
