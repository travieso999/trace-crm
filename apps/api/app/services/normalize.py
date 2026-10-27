import re
from urllib.parse import urlparse

from email_validator import EmailNotValidError, validate_email

US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "district of columbia": "DC", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA",
    "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY", "puerto rico": "PR",
}
_ABBREVS = set(US_STATES.values())

# Best-effort default time zone per state (states spanning zones use the majority zone).
STATE_TZ = {
    "AL": "America/Chicago", "AK": "America/Anchorage", "AZ": "America/Phoenix", "AR": "America/Chicago",
    "CA": "America/Los_Angeles", "CO": "America/Denver", "CT": "America/New_York", "DE": "America/New_York",
    "DC": "America/New_York", "FL": "America/New_York", "GA": "America/New_York", "HI": "Pacific/Honolulu",
    "ID": "America/Boise", "IL": "America/Chicago", "IN": "America/Indiana/Indianapolis", "IA": "America/Chicago",
    "KS": "America/Chicago", "KY": "America/New_York", "LA": "America/Chicago", "ME": "America/New_York",
    "MD": "America/New_York", "MA": "America/New_York", "MI": "America/Detroit", "MN": "America/Chicago",
    "MS": "America/Chicago", "MO": "America/Chicago", "MT": "America/Denver", "NE": "America/Chicago",
    "NV": "America/Los_Angeles", "NH": "America/New_York", "NJ": "America/New_York", "NM": "America/Denver",
    "NY": "America/New_York", "NC": "America/New_York", "ND": "America/Chicago", "OH": "America/New_York",
    "OK": "America/Chicago", "OR": "America/Los_Angeles", "PA": "America/New_York", "RI": "America/New_York",
    "SC": "America/New_York", "SD": "America/Chicago", "TN": "America/Chicago", "TX": "America/Chicago",
    "UT": "America/Denver", "VT": "America/New_York", "VA": "America/New_York", "WA": "America/Los_Angeles",
    "WV": "America/New_York", "WI": "America/Chicago", "WY": "America/Denver", "PR": "America/Puerto_Rico",
}


class InvalidEmail(ValueError):
    pass


def normalize_email(raw: str | None) -> str:
    """Trim, lowercase and syntax-validate. Does not alter local parts (no Gmail dot stripping)."""
    value = (raw or "").strip().strip("<>").strip()
    if not value:
        raise InvalidEmail("Email is empty")
    try:
        result = validate_email(value, check_deliverability=False)
    except EmailNotValidError as exc:
        raise InvalidEmail(str(exc)) from exc
    return result.normalized.lower()


def normalize_website(raw: str | None) -> str | None:
    value = (raw or "").strip()
    if not value:
        return None
    if not re.match(r"^https?://", value, re.I):
        value = "https://" + value
    parsed = urlparse(value)
    if not parsed.hostname or "." not in parsed.hostname:
        return None
    return f"{parsed.scheme.lower()}://{parsed.hostname.lower()}{parsed.path if parsed.path not in ('', '/') else ''}"


def domain_of(website: str | None) -> str | None:
    if not website:
        return None
    host = urlparse(website if "://" in website else "https://" + website).hostname
    if not host:
        return None
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


def normalize_state(raw: str | None) -> str | None:
    value = (raw or "").strip()
    if not value:
        return None
    if value.upper() in _ABBREVS:
        return value.upper()
    return US_STATES.get(value.lower(), value[:60])


def guess_timezone(state: str | None) -> str | None:
    return STATE_TZ.get((state or "").upper())


def clean(value: str | None, max_len: int) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value[:max_len] or None
