from app.core.config import get_settings
from app.models import EmailAccount
from app.providers.base import MailProvider, ProviderUnavailable

# What a provider is being built for. Real (non-sandbox) mailboxes may only send for purposes allowed by REAL_SENDING.
PURPOSES = ("campaign", "reply", "test")


def send_block_reason(account: EmailAccount, purpose: str | None, to_email: str | None = None) -> str | None:
    """None when ``account`` may send for ``purpose``; otherwise a message explaining why not.

    REAL_SENDING=disabled blocks every real send; enabled allows every purpose. test_only allows every purpose but
    only to ALLOWED_TEST_RECIPIENTS, so a whole campaign can be tested from a real mailbox without reaching anyone
    else. Mailbox-level checks pass no ``to_email``; every actual send is checked again with its recipient."""
    if account.provider == "sandbox":
        return None
    s = get_settings()
    if s.real_sending == "enabled" and purpose in PURPOSES:
        return None
    if s.real_sending == "test_only":
        if purpose in PURPOSES and (to_email is None or to_email.strip().lower() in s.test_recipient_allowlist):
            return None
        return ("Real sending is in test-only mode (REAL_SENDING=test_only): "
                f"{to_email or 'this recipient'} is not in ALLOWED_TEST_RECIPIENTS")
    return "Real sending is disabled on this server (REAL_SENDING=disabled)"


def get_provider(account: EmailAccount, purpose: str | None = None) -> MailProvider:
    """Adapter for a mailbox. ``purpose`` (campaign | reply | test) is required for a real mailbox to send at all;
    without it the adapter can still sync replies and reconcile, but ``send`` raises ProviderUnavailable."""
    if account.provider == "sandbox":
        if not get_settings().sandbox_enabled:
            raise ProviderUnavailable("Sandbox mailboxes are disabled in production")
        from app.providers.sandbox import SandboxProvider

        return SandboxProvider(account.email)
    if account.provider == "gmail":
        if not get_settings().google_oauth_configured:
            raise ProviderUnavailable("Google OAuth is not configured on this server")
        from app.providers.gmail import GmailProvider

        # evaluated per message, with its recipient, at the moment of sending
        return GmailProvider(account.id, send_block_reason=lambda to: send_block_reason(account, purpose, to or ""))
    raise ProviderUnavailable(f"The {account.provider} adapter is not configured in this deployment")
