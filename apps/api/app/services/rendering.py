"""Safe template rendering.

Templates are plain text with ``{{variable}}`` or ``{{variable|fallback text}}`` placeholders, and ``[label](url)``
for a link on words. No expressions, no code execution. Values are HTML-escaped for the HTML part.
Rendering raises ``MissingVariables`` rather than ever producing an unresolved placeholder.
"""

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser

PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z][a-zA-Z0-9_]*)\s*(?:\|([^{}]*))?\}\}")
URL = re.compile(r"(https?://[^\s<>\"']+)")
STANDARD_VARIABLES = [
    "first_name", "last_name", "full_name", "email", "company_name", "city", "state", "website",
    "job_title", "industry", "phone", "custom_intro",
]


class MissingVariables(Exception):
    def __init__(self, names: list[str]):
        self.names = sorted(set(names))
        super().__init__(f"Missing values for: {', '.join(self.names)}")


@dataclass
class Rendered:
    subject: str
    text: str
    html: str


DEFAULT_VIDEO_CTA = "Watch Our 60-Second Demo"
VIDEO_BAR_COLOR = "#14213d"
VIDEO_BLOCK_WIDTH = 600
# Where the video goes in the body. Both forms are removed from what recipients see, so neither can be delivered as text
VIDEO_MARKER = re.compile(r"\*{0,2}\[\s*INSERT VIDEO THUMBNAIL HERE\s*\]\*{0,2}|\{\{\s*video\s*\}\}", re.IGNORECASE)
_VIDEO_SLOT = ""  # private-use character: survives templating and can't be typed by accident
# a whole line that closes the message; deliberately exact, so "Thanks for your time" in a sentence doesn't count
_SIGN_OFF = re.compile(r"(best|best regards|best wishes|regards|kind regards|warm regards|warmly|thanks|thanks so much|"
                       r"many thanks|thank you|cheers|sincerely|yours truly|talk soon)[,.!]?", re.IGNORECASE)


# Where the sending mailbox's signature goes
SIGNATURE_MARKER = re.compile(r"\{\{\s*signature\s*(?:\|[^{}]*)?\}\}")
_SIGNATURE_SLOT = "\ue004"
# [label](url), where the url can be a placeholder: [Our site]({{website}}). Becomes private-use markers before any
# values go in, so a prospect's company name can never turn itself into a link.
MARKUP_LINK = re.compile(r"\[([^\[\]\n]+)\]\(\s*((?:https?://|mailto:|tel:|(?=\{\{))(?:\{\{[^{}]*\}\}|[^\s()<>\[\]{}])*)\s*\)")
_LINK_OPEN, _LINK_MID, _LINK_CLOSE = "\ue001", "\ue002", "\ue003"
_MARKED_LINK = re.compile(f"{_LINK_OPEN}([^{_LINK_MID}]*){_LINK_MID}([^{_LINK_CLOSE}]*){_LINK_CLOSE}")
_PRIVATE_MARKERS = re.compile(f"[{_LINK_OPEN}{_LINK_MID}{_LINK_CLOSE}{_SIGNATURE_SLOT}]")
_SAFE_SCHEMES = {"http", "https", "mailto", "tel"}


def _split_before_sign_off(text: str) -> tuple[str, str]:
    """With no marker, the video belongs after the message but above "Best, Name", not under the signature."""
    paragraphs = re.split(r"\n{2,}", text)
    for i in range(len(paragraphs) - 1, 0, -1):
        if _SIGN_OFF.fullmatch(paragraphs[i].strip().split("\n")[0].strip()):
            return "\n\n".join(paragraphs[:i]), "\n\n".join(paragraphs[i:])
    return text, ""


@dataclass
class VideoLink:
    """A clickable video preview: a thumbnail (when there is one) and a bar below it, both linking to the video page."""

    url: str
    image_src: str | None = None  # public https in a real email (cid: without one), data: in an on-screen preview
    cta: str = DEFAULT_VIDEO_CTA


def _video_html(video: VideoLink) -> str:
    """Tables and inline styles only: no background images, positioning or scripts, which Gmail and Outlook drop. The
    play button is part of the image itself. width= is what desktop Outlook obeys; the styles make it fluid elsewhere."""
    url = html.escape(video.url, quote=True)
    cta = html.escape(video.cta)
    image = ""
    if video.image_src:
        image = (f'<tr><td style="padding:0;line-height:0;font-size:0"><a href="{url}" target="_blank" '
                 f'style="display:block;text-decoration:none"><img src="{html.escape(video.image_src, quote=True)}" '
                 f'alt="{html.escape(video.cta, quote=True)}" width="{VIDEO_BLOCK_WIDTH}" border="0" '
                 f'style="display:block;width:100%;max-width:{VIDEO_BLOCK_WIDTH}px;height:auto;border:0;outline:none">'
                 '</a></td></tr>')
    # desktop Outlook ignores max-width and would stretch the bar across the pane: it alone reads this fixed-width wrapper
    return (f'<!--[if mso]><table role="presentation" width="{VIDEO_BLOCK_WIDTH}" cellpadding="0" cellspacing="0" border="0">'
            '<tr><td><![endif]-->'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="width:100%;max-width:{VIDEO_BLOCK_WIDTH}px;border-collapse:collapse;margin:16px 0">{image}'
            f'<tr><td align="center" bgcolor="{VIDEO_BAR_COLOR}" style="background-color:{VIDEO_BAR_COLOR};padding:0">'
            f'<a href="{url}" target="_blank" style="display:block;padding:9px 12px;color:#ffffff;font-family:Arial,'
            f'sans-serif;font-size:14px;font-weight:bold;line-height:18px;text-align:center;text-decoration:none">{cta}</a>'
            '</td></tr></table><!--[if mso]></td></tr></table><![endif]-->')


def render_string(template: str, variables: dict[str, str], *, policy: str = "block",
                  fallbacks: dict[str, str] | None = None) -> str:
    fallbacks = fallbacks or {}
    missing: list[str] = []

    def repl(m: re.Match) -> str:
        name, inline = m.group(1), m.group(2)
        if name in ("video", "signature"):  # positions, not values: render_email fills them
            return ""
        value = (variables.get(name) or "").strip()
        if value:
            return value
        if inline is not None and inline.strip():
            return inline.strip()
        if policy == "fallback" and (fallbacks.get(name) or "").strip():
            return fallbacks[name].strip()
        missing.append(name)
        return ""

    out = PLACEHOLDER.sub(repl, template)
    if missing:
        raise MissingVariables(missing)
    if "{{" in out or "}}" in out:
        # malformed placeholder such as "{{ first name }}" – never send it
        raise MissingVariables(["malformed_placeholder"])
    return out


def _mark_links(text: str) -> str:
    return MARKUP_LINK.sub(lambda m: f"{_LINK_OPEN}{m.group(1)}{_LINK_MID}{m.group(2)}{_LINK_CLOSE}", text)


def _safe_href(url: str) -> str | None:
    """The address to link to, or None for anything but web, mail and phone links (javascript: from a data field)."""
    url = url.strip()
    if not url or re.search(r"[\s<>\"]", url):
        return None
    scheme = re.match(r"([a-zA-Z][a-zA-Z0-9+.-]*):", url)
    if scheme:
        return url if scheme.group(1).lower() in _SAFE_SCHEMES else None
    return f"https://{url}"  # a website field holding "acme.com"


def _plain_html(text: str) -> str:
    escaped = html.escape(_PRIVATE_MARKERS.sub("", text))
    escaped = URL.sub(lambda m: f'<a href="{m.group(1)}">{m.group(1)}</a>', escaped)
    return escaped.replace("\n", "<br>")


def _inline_html(text: str) -> str:
    parts, pos = [], 0
    for m in _MARKED_LINK.finditer(text):
        parts.append(_plain_html(text[pos:m.start()]))
        label, href = html.escape(m.group(1)), _safe_href(m.group(2))
        parts.append(f'<a href="{html.escape(href, quote=True)}">{label}</a>' if href else label)
        pos = m.end()
    parts.append(_plain_html(text[pos:]))
    return "".join(parts)


def _links_as_text(text: str) -> str:
    """The plain-text part has no links on words, so the address follows them."""
    def repl(m: re.Match) -> str:
        label, href = m.group(1), _safe_href(m.group(2))
        return f"{label} ({href})" if href and href != label else label
    return _PRIVATE_MARKERS.sub("", _MARKED_LINK.sub(repl, text))


def text_to_html(text: str) -> str:
    return "\n".join("<p>" + _inline_html(p) + "</p>" for p in re.split(r"\n{2,}", text.strip()))


def render_email(subject: str, body: str, variables: dict[str, str], *, policy: str = "block",
                 fallbacks: dict[str, str] | None = None, footer: str | None = None,
                 video: VideoLink | None = None, signature: str | None = None) -> Rendered:
    """``signature`` is the sending mailbox's, as ``signature_to_text`` gives it. It appears only where the body says."""
    subj = render_string(subject, variables, policy=policy, fallbacks=fallbacks)
    subj = " ".join(subj.split())  # no header injection via newlines
    marked = VIDEO_MARKER.sub(_VIDEO_SLOT, body)
    marked = SIGNATURE_MARKER.sub("", SIGNATURE_MARKER.sub(_SIGNATURE_SLOT, marked, count=1))
    variables = {k: _PRIVATE_MARKERS.sub("", v) if v else v for k, v in variables.items()}
    text = render_string(_mark_links(marked), variables, policy=policy, fallbacks=fallbacks)
    # the signature goes in after the values, so it is never read as a template
    text = text.replace(_SIGNATURE_SLOT, _SIGNATURE_SLOT + _mark_links((signature or "").strip()))
    # the video goes at the first marker, or above the sign-off; further markers just disappear, so it can't appear twice
    before, marker, after = text.partition(_VIDEO_SLOT)
    if video and not marker:
        message, slot, signed = before.partition(_SIGNATURE_SLOT)
        before, closing = _split_before_sign_off(message)
        after = closing + signed  # with no sign-off, directly above the signature
    before, after = before.replace(_SIGNATURE_SLOT, "").strip(), re.sub(f"[{_VIDEO_SLOT}{_SIGNATURE_SLOT}]", "", after).strip()
    parts = [_links_as_text(before).strip(), f"{video.cta}: {video.url}" if video else "", _links_as_text(after).strip(),
             footer.strip() if footer else ""]
    full_text = "\n\n".join(p for p in parts if p)
    html_parts = [text_to_html(before) if before else "", _video_html(video) if video else "",
                  text_to_html(after) if after else ""]
    html_body = "\n".join(p for p in html_parts if p)
    if footer:
        html_body += '\n<p style="color:#6b7280;font-size:12px;margin-top:24px">' + _inline_html(footer.strip()) + "</p>"
    return Rendered(subject=subj, text=full_text, html=f'<div style="font-family:Arial,sans-serif;font-size:14px">{html_body}</div>')


class _SignatureText(HTMLParser):
    BLOCKS = {"p", "div", "tr", "li", "table", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6"}
    SKIP = {"style", "script", "head", "title"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.href: str | None = None
        self.label: list[str] = []
        self.skipping = 0

    def _line_end(self):
        if self.out and not self.out[-1].endswith("\n"):
            self.out.append("\n")

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skipping += 1
        elif tag == "br":
            self.out.append("\n")
        elif tag == "a":
            self.href, self.label = dict(attrs).get("href") or "", []
        elif tag in self.BLOCKS:
            self._line_end()

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skipping = max(0, self.skipping - 1)
        elif tag == "a" and self.href is not None:
            label = re.sub(r"[\[\]]", "", "".join(self.label)).strip()
            href = _safe_href(self.href)
            self.out.append(f"[{label}]({href})" if label and href and label != href else label)
            self.href = None
        elif tag in self.BLOCKS:
            self._line_end()

    def handle_data(self, data):
        if self.skipping:
            return
        data = re.sub(r"\s+", " ", data)
        if self.href is not None:
            self.label.append(data)
        elif data.strip() or (self.out and not self.out[-1].endswith("\n")):
            self.out.append(data)


def signature_to_text(signature_html: str | None) -> str:
    """A mailbox's stored signature as lines and [label](url) links, which is what the editor and the sender work with.
    Older signatures typed as raw HTML keep their words and links, but not their images or styling."""
    if not signature_html:
        return ""
    parser = _SignatureText()
    parser.feed(signature_html)
    parser.close()
    return "\n".join(line.strip() for line in "".join(parser.out).split("\n")).strip()


def signature_to_html(text: str) -> str:
    return _inline_html(_mark_links(text.strip()))


def uses_signature(body: str) -> bool:
    return bool(SIGNATURE_MARKER.search(body))


def variables_in(template: str) -> set[str]:
    return {m.group(1) for m in PLACEHOLDER.finditer(template)} - {"video", "signature"}
