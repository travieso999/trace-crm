"""Gmail API adapter (users.messages.send / users.history.list / users.messages.get).

Sending is refused unless the caller was granted it by the REAL_SENDING policy (see providers.get_provider).
"""

import base64
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

import httpx

from app.providers.base import (
    AmbiguousSendError,
    InboundRaw,
    OutgoingMessage,
    PermanentProviderError,
    ProviderAuthError,
    ProviderUnavailable,
    SendResult,
    TemporaryProviderError,
)
from app.providers.mime import build_mime, check_message_size
from app.services import google_oauth

MAX_HISTORY_PAGES = 20
UPLOAD_TIMEOUT = httpx.Timeout(120.0, connect=10.0)


class GmailProvider:
    def __init__(self, account_id: uuid.UUID, *, send_block_reason: Callable[[str], str | None],
                 token: Callable[..., str] | None = None):
        """``send_block_reason(to_email)`` is asked before every send; a non-None answer refuses that message."""
        self.account_id = account_id
        self.send_block_reason = send_block_reason
        self._token = token or (lambda force=False: google_oauth.access_token(account_id, force_refresh=force))

    # --- HTTP -------------------------------------------------------------------------------------

    def _request(self, method: str, path: str, *, ambiguous_on_read_timeout: bool = False, headers: dict | None = None,
                 **kw) -> httpx.Response:
        url = path if path.startswith("https://") else f"{google_oauth.GMAIL_API}{path}"
        for attempt in (0, 1):
            auth = {"Authorization": f"Bearer {self._token(force=attempt == 1)}"}
            try:
                with google_oauth.http_client() as c:
                    r = c.request(method, url, headers={**auth, **(headers or {})}, **kw)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                raise TemporaryProviderError(f"Could not reach Gmail ({type(exc).__name__})") from exc
            except httpx.TimeoutException as exc:  # request was sent; Gmail may have accepted it
                if ambiguous_on_read_timeout:
                    raise AmbiguousSendError("Gmail timed out after the message was uploaded") from exc
                raise TemporaryProviderError("Gmail timed out") from exc
            except httpx.HTTPError as exc:
                raise TemporaryProviderError(f"Gmail request failed ({type(exc).__name__})") from exc
            if r.status_code == 401 and attempt == 0:
                continue  # token expired early or was rotated: refresh once and retry
            return self._check(r)
        raise ProviderAuthError("Gmail rejected the access token after a refresh; reconnect this mailbox")

    @staticmethod
    def _check(r: httpx.Response) -> httpx.Response:
        if r.status_code < 400:
            return r
        try:
            err = r.json().get("error", {})
        except ValueError:
            err = {}
        reasons = {e.get("reason") for e in err.get("errors", []) if isinstance(e, dict)}
        message = f"Gmail {r.status_code}: {err.get('message') or r.reason_phrase}"
        throttled = reasons & {"rateLimitExceeded", "userRateLimitExceeded", "dailyLimitExceeded", "backendError"}
        if r.status_code == 429 or r.status_code >= 500 or throttled:
            raise TemporaryProviderError(message)
        if r.status_code in (401, 403):  # revoked token, missing scope, or Gmail API disabled for the project
            raise ProviderAuthError(message)
        if r.status_code == 404:
            return r  # callers decide (e.g. expired history cursor)
        raise PermanentProviderError(message)

    # --- MailProvider -----------------------------------------------------------------------------

    def send(self, msg: OutgoingMessage) -> SendResult:
        if blocked := self.send_block_reason(msg.to_email if msg else ""):
            raise ProviderUnavailable(blocked)
        mime = build_mime(msg).as_bytes()
        thread = {"threadId": msg.provider_thread_id} if msg.provider_thread_id else {}
        if msg.attachments:
            check_message_size("gmail", mime)
            data = self._send_resumable(mime, thread)
        else:
            raw = base64.urlsafe_b64encode(mime).decode()
            data = self._request("POST", "/messages/send", json={"raw": raw, **thread},
                                 ambiguous_on_read_timeout=True).json()
        return SendResult(provider_message_id=data["id"], provider_thread_id=data.get("threadId"))

    def _send_resumable(self, mime: bytes, metadata: dict) -> dict:
        """Upload endpoint, for messages too large to send base64-encoded inside a JSON body."""
        start = self._request("POST", f"{google_oauth.GMAIL_UPLOAD}/messages/send", params={"uploadType": "resumable"},
                              json=metadata, headers={"X-Upload-Content-Type": "message/rfc822",
                                                      "X-Upload-Content-Length": str(len(mime))})
        session = start.headers.get("location")
        if not session:  # nothing has been uploaded yet, so trying again is safe
            raise TemporaryProviderError("Gmail did not start the upload")
        r = self._request("PUT", session, content=mime, headers={"Content-Type": "message/rfc822"},
                          ambiguous_on_read_timeout=True, timeout=UPLOAD_TIMEOUT)
        if r.status_code not in (200, 201):  # e.g. 308: Gmail kept the bytes but did not confirm a sent message
            raise AmbiguousSendError(f"Gmail answered {r.status_code} to the upload")
        return r.json()

    def find_sent(self, rfc_message_id: str) -> SendResult | None:
        r = self._request("GET", "/messages", params={"q": f"rfc822msgid:{rfc_message_id.strip('<>')}", "maxResults": 1,
                                                      "includeSpamTrash": "true"})
        found = (r.json().get("messages") or []) if r.status_code == 200 else []
        return SendResult(found[0]["id"], found[0].get("threadId")) if found else None

    def fetch_new(self, cursor: str | None) -> tuple[list[InboundRaw], str | None]:
        """New INBOX messages since history ``cursor``. A missing/expired cursor restarts from "now"."""
        if not cursor:
            return [], self._current_history_id()
        ids: list[str] = []
        latest = cursor
        page_token = None
        for _ in range(MAX_HISTORY_PAGES):
            params = {"startHistoryId": cursor, "historyTypes": "messageAdded", "labelId": "INBOX", "maxResults": 500}
            if page_token:
                params["pageToken"] = page_token
            r = self._request("GET", "/history", params=params)
            if r.status_code == 404:  # cursor too old (Gmail keeps ~1 week): resume from now
                return [], self._current_history_id()
            data = r.json()
            latest = data.get("historyId", latest)
            for h in data.get("history", []):
                for added in h.get("messagesAdded", []):
                    m = added.get("message", {})
                    labels = set(m.get("labelIds", []))
                    if m.get("id") and not labels & {"SENT", "DRAFT"} and m["id"] not in ids:
                        ids.append(m["id"])
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        out = []
        for mid in ids:
            r = self._request("GET", f"/messages/{mid}", params={"format": "raw"})
            if r.status_code == 404:
                continue  # deleted before we fetched it
            m = r.json()
            raw = base64.urlsafe_b64decode(m["raw"] + "=" * (-len(m["raw"]) % 4))
            received = datetime.fromtimestamp(int(m["internalDate"]) / 1000, UTC) if m.get("internalDate") else None
            out.append(InboundRaw(provider_message_id=m["id"], provider_thread_id=m.get("threadId"), raw=raw,
                                  received_at=received))
        return out, latest

    def _current_history_id(self) -> str | None:
        r = self._request("GET", "/profile")
        return str(r.json().get("historyId")) if r.status_code == 200 else None
