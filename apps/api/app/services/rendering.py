"""Safe template rendering.

Templates are plain text with ``{{variable}}`` or ``{{variable|fallback text}}`` placeholders.
No expressions, no code execution. Values are HTML-escaped for the HTML part.
Rendering raises ``MissingVariables`` rather than ever producing an unresolved placeholder.
"""

import html
import re
from dataclasses import dataclass

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
            f'<a href="{url}" target="_blank" style="display:block;padding:14px 16px;color:#ffffff;font-family:Arial,'
            f'sans-serif;font-size:16px;font-weight:bold;line-height:20px;text-align:center;text-decoration:none">{cta}</a>'
            '</td></tr></table><!--[if mso]></td></tr></table><![endif]-->')


def render_string(template: str, variables: dict[str, str], *, policy: str = "block",
                  fallbacks: dict[str, str] | None = None) -> str:
    fallbacks = fallbacks or {}
    missing: list[str] = []

    def repl(m: re.Match) -> str:
        name, inline = m.group(1), m.group(2)
        if name == "video":  # a position, not a value: render_email puts the video there
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


def _inline_html(text: str) -> str:
    escaped = html.escape(text)
    escaped = URL.sub(lambda m: f'<a href="{m.group(1)}">{m.group(1)}</a>', escaped)
    return escaped.replace("\n", "<br>")


def text_to_html(text: str) -> str:
    return "\n".join("<p>" + _inline_html(p) + "</p>" for p in re.split(r"\n{2,}", text.strip()))


def render_email(subject: str, body: str, variables: dict[str, str], *, policy: str = "block",
                 fallbacks: dict[str, str] | None = None, footer: str | None = None,
                 video: VideoLink | None = None) -> Rendered:
    subj = render_string(subject, variables, policy=policy, fallbacks=fallbacks)
    subj = " ".join(subj.split())  # no header injection via newlines
    marked = VIDEO_MARKER.sub(_VIDEO_SLOT, body)
    text = render_string(marked, variables, policy=policy, fallbacks=fallbacks)
    # the video goes at the first marker, or after the message; further markers just disappear, so it can't appear twice
    before, _, after = text.partition(_VIDEO_SLOT)
    before, after = before.strip(), after.replace(_VIDEO_SLOT, "").strip()
    parts = [before, f"{video.cta}: {video.url}" if video else "", after, footer.strip() if footer else ""]
    full_text = "\n\n".join(p for p in parts if p)
    html_parts = [text_to_html(before) if before else "", _video_html(video) if video else "",
                  text_to_html(after) if after else ""]
    html_body = "\n".join(p for p in html_parts if p)
    if footer:
        html_body += '\n<p style="color:#6b7280;font-size:12px;margin-top:24px">' + _inline_html(footer.strip()) + "</p>"
    return Rendered(subject=subj, text=full_text, html=f'<div style="font-family:Arial,sans-serif;font-size:14px">{html_body}</div>')


def variables_in(template: str) -> set[str]:
    return {m.group(1) for m in PLACEHOLDER.finditer(template)}
