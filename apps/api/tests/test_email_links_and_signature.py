"""Links on words and mailbox signatures: what recipients get, what can't get in, what an existing email keeps doing."""

import pytest

from app.services import sending
from app.services.rendering import MissingVariables, VideoLink, render_email, signature_to_html, signature_to_text
from tests.test_sending import make_contacts, outbox, sent_files, setup_campaign, setup_mailbox  # noqa: F401

VIDEO = VideoLink("https://videos.example.com/1", None, "Watch")


def test_a_link_on_words_is_a_real_link_and_the_plain_text_keeps_the_address():
    r = render_email("S", "See [our work](https://trace-studio.tech/a?x=1&y=2) today", {})
    assert '<a href="https://trace-studio.tech/a?x=1&amp;y=2">our work</a>' in r.html
    assert "[" not in r.html and "our work (https://trace-studio.tech/a?x=1&y=2)" in r.text


def test_bare_addresses_and_ordinary_brackets_work_as_before():
    r = render_email("S", "Go to https://a.example.com [today] (really)", {})
    assert '<a href="https://a.example.com">https://a.example.com</a>' in r.html
    assert "[today] (really)" in r.html and r.text == "Go to https://a.example.com [today] (really)"


def test_a_label_and_a_url_can_use_variables():
    r = render_email("S", "[{{company_name}} site]({{website}})", {"company_name": "Acme", "website": "acme.com"})
    assert '<a href="https://acme.com">Acme site</a>' in r.html and r.text == "Acme site (https://acme.com)"


def test_a_missing_variable_in_a_link_still_blocks_the_email():
    with pytest.raises(MissingVariables):
        render_email("S", "[Site]({{website}})", {})


@pytest.mark.parametrize("value", ["javascript:alert(1)", "data:text/html,x", "https://a.com/\"onmouseover=\"x"])
def test_a_bad_address_from_a_data_field_loses_the_link_but_keeps_the_words(value):
    r = render_email("S", "[Site]({{website}})", {"website": value})
    assert "<a " not in r.html and "Site" in r.html and r.text == "Site"


def test_prospect_data_cannot_add_a_link_or_break_out():
    evil = "[click](https://evil.example.com) <script>xhttps://evil.example.com"
    r = render_email("S", "Hi {{company_name}}", {"company_name": evil})
    assert 'href="https://evil.example.com">click' not in r.html and "<script>" not in r.html
    assert "" not in r.html and "" not in r.text


def test_the_signature_goes_where_the_body_says_only_once():
    r = render_email("S", "Hi\n\nBest,\n{{signature}}\n\n{{signature}}", {}, signature="Travis\n[Trace Studio](https://trace-studio.tech)")
    assert r.text == "Hi\n\nBest,\nTravis\nTrace Studio (https://trace-studio.tech)"
    assert 'Best,<br>Travis<br><a href="https://trace-studio.tech">Trace Studio</a>' in r.html


def test_no_signature_marker_means_no_signature_and_no_signature_set_leaves_no_trace():
    assert "Travis" not in render_email("S", "Hi", {}, signature="Travis").text
    r = render_email("S", "Hi\n\n{{signature}}", {})
    assert r.text == "Hi" and "<p></p>" not in r.html and "" not in r.html


def test_a_signature_is_never_read_as_a_template():
    assert "{{first_name}}" in render_email("S", "{{signature}}", {}, signature="{{first_name}}").text


@pytest.mark.parametrize(("body", "signature", "expected"), [
    ("Message\n\nBest,\n{{signature}}", "Travis", ["Message", "Watch: ", "Best,\nTravis"]),
    ("Message\n\n{{signature}}", "Travis\nStudio", ["Message", "Watch: ", "Travis\nStudio"]),
    ("Message\n\n{{signature}}", "Best,\nTravis", ["Message", "Watch: ", "Best,\nTravis"]),
    ("Message\n\n{{video}}\n\nMore\n\n{{signature}}", "Travis", ["Message", "Watch: ", "More", "Travis"]),
], ids=["sign-off in the body", "no sign-off", "sign-off in the signature", "marker wins"])
def test_the_video_block_stays_above_the_signature(body, signature, expected):
    text = render_email("S", body, {}, video=VIDEO, signature=signature).text
    assert [text.index(part) for part in expected] == sorted(text.index(part) for part in expected)


def test_editor_signatures_round_trip_through_the_stored_html():
    text = "Travis\nTrace Studio\n\n[Our site](https://trace-studio.tech)\nhttps://b.example.com\nA & B"
    stored = signature_to_html(text)
    assert stored.count("<br>") == 5 and "A &amp; B" in stored
    assert signature_to_text(stored) == text


def test_an_older_raw_html_signature_keeps_its_words_and_links():
    old = ('<div style="color:red"><b>Sam</b> Smith</div><style>p{}</style><p>Call <a href="tel:+15551234">me</a>'
           '<br><a href="javascript:x">bad</a><img src="https://x/y.png"><script>alert(1)</script></p>')
    assert signature_to_text(old) == "Sam Smith\nCall [me](tel:+15551234)\nbad"


# --- through the app --------------------------------------------------------------------------------------------------

def test_a_mailbox_saves_its_signature_and_the_campaign_email_carries_it(client, db, outbox, compliance):  # noqa: F811
    mid = setup_mailbox(client, db)
    r = client.patch(f"/api/email-accounts/{mid}", json={"signature_text": "Sam\n[Trace](https://trace-studio.tech)"})
    assert r.status_code == 200 and r.json()["signature_text"] == "Sam\n[Trace](https://trace-studio.tech)"
    assert r.json()["signature_html"] == 'Sam<br><a href="https://trace-studio.tech">Trace</a>'
    setup_campaign(client, mid, make_contacts(client, "a@acme.com"), steps=[
        {"subject": "Hi", "body": "See [this](https://x.example.com)\n\nBest,\n{{signature}}"}])
    assert sending.send_due() == {"sent": 1}
    [m] = sent_files(outbox)
    html = m.get_body(("html",)).get_content()
    assert '<a href="https://x.example.com">this</a>' in html and '<a href="https://trace-studio.tech">Trace</a>' in html
    plain = m.get_body(("plain",)).get_content()
    assert "this (https://x.example.com)" in plain and "Sam\nTrace (https://trace-studio.tech)" in plain.replace("\r\n", "\n")


def test_settings_saved_without_the_signature_leave_it_alone_and_clearing_removes_it(client, db):
    mid = setup_mailbox(client, db)
    client.patch(f"/api/email-accounts/{mid}", json={"signature_html": "<b>Sam</b>"})
    assert client.patch(f"/api/email-accounts/{mid}", json={"daily_limit": 5}).json()["signature_html"] == "<b>Sam</b>"
    assert client.patch(f"/api/email-accounts/{mid}", json={"signature_text": "  "}).json()["signature_html"] is None


def test_a_signature_cannot_hold_variables(client, db):
    mid = setup_mailbox(client, db)
    r = client.patch(f"/api/email-accounts/{mid}", json={"signature_text": "{{first_name}}"})
    assert r.status_code == 422 and "variables" in r.text


def test_a_campaign_using_a_signature_cannot_start_while_its_mailbox_has_none(client, db, compliance):
    mid = setup_mailbox(client, db)
    cid = setup_campaign(client, mid, make_contacts(client, "a@acme.com"), start=False,
                         steps=[{"subject": "Hi", "body": "Hi\n\n{{signature}}"}])
    r = client.post(f"/api/campaigns/{cid}/start")
    assert r.status_code != 200 and "signature" in r.text
    client.patch(f"/api/email-accounts/{mid}", json={"signature_text": "Sam"})
    assert client.post(f"/api/campaigns/{cid}/start").status_code == 200


def test_the_preview_shows_the_signature_or_says_it_is_missing(client, db, compliance):
    mid = setup_mailbox(client, db)
    cid = setup_campaign(client, mid, make_contacts(client, "a@acme.com"), start=False,
                         steps=[{"subject": "Hi", "body": "Hi\n\n{{signature}}"}])
    body = {"subject": "Hi", "body": "Hi\n\n{{signature}}", "campaign_id": cid}
    r = client.post("/api/campaigns/preview", json=body).json()
    assert r["ok"] and r["signature_missing"] and r["variables_used"] == []
    client.patch(f"/api/email-accounts/{mid}", json={"signature_text": "Sam Smith"})
    r = client.post("/api/campaigns/preview", json=body).json()
    assert r["signature_missing"] == [] and "Sam Smith" in r["html"]
