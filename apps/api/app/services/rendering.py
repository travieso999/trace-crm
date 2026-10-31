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


@dataclass
class VideoLink:
    """A clickable video preview: a thumbnail (when there is one) and a link to where the video plays."""

    url: str
    image_src: str | None = None  # cid: in a real email, data: in an on-screen preview


WATCH_VIDEO = "Watch the video"


def _video_html(video: VideoLink) -> str:
    url = html.escape(video.url, quote=True)
    link = f'<p><a href="{url}">{WATCH_VIDEO}</a></p>'
    if not video.image_src:
        return link
    img = (f'<img src="{html.escape(video.image_src, quote=True)}" alt="{WATCH_VIDEO}" width="480" '
           'style="display:block;max-width:100%;height:auto;border:0;border-radius:6px">')
    return f'<p><a href="{url}">{img}</a></p>\n{link}'


def render_string(template: str, variables: dict[str, str], *, policy: str = "block",
                  fallbacks: dict[str, str] | None = None) -> str:
    fallbacks = fallbacks or {}
    missing: list[str] = []

    def repl(m: re.Match) -> str:
        name, inline = m.group(1), m.group(2)
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
    text = render_string(body, variables, policy=policy, fallbacks=fallbacks).strip()
    full_text = text + (f"\n\n{WATCH_VIDEO}: {video.url}" if video else "") + ("\n\n" + footer.strip() if footer else "")
    html_body = text_to_html(text)
    if video:  # after the message, always above the unsubscribe footer
        html_body += "\n" + _video_html(video)
    if footer:
        html_body += '\n<p style="color:#6b7280;font-size:12px;margin-top:24px">' + _inline_html(footer.strip()) + "</p>"
    return Rendered(subject=subj, text=full_text, html=f'<div style="font-family:Arial,sans-serif;font-size:14px">{html_body}</div>')


def variables_in(template: str) -> set[str]:
    return {m.group(1) for m in PLACEHOLDER.finditer(template)}
