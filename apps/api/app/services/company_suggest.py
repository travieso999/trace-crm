"""Suggest a company name from a business email address: anbba@americannationalbail.com -> "American National Bail".

Only a suggestion, shown on the Missing data tab for a person to accept or ignore; nothing is stored without that.
Free mailbox domains (gmail, yahoo...) say nothing about the business, so they get no suggestion. The domain is split
into words with a small vocabulary of common business words (bail-bond heavy, since that's who we email); a piece
that isn't a known word is kept whole, so the worst case is a capitalised domain.
"""

import functools
import re

FREE_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "rocketmail.com", "aol.com", "aim.com", "hotmail.com",
    "outlook.com", "live.com", "msn.com", "icloud.com", "me.com", "mac.com", "comcast.net", "att.net", "sbcglobal.net",
    "bellsouth.net", "verizon.net", "cox.net", "charter.net", "earthlink.net", "protonmail.com", "proton.me",
    "gmx.com", "mail.com", "zoho.com", "yandex.com", "frontier.com", "windstream.net", "centurylink.net", "q.com",
    "optonline.net", "juno.com", "netzero.net", "roadrunner.com", "twc.com", "rr.com", "embarqmail.com",
}

WORDS = set("""
aa aaa ace action active advance advantage affordable agency agent agents alliance all allied always amazing america
american americas and angel angels anytime apex area associates assurance atlas bail bails bay beach best
big blue bond bondsman bondsmen bonding bonds bonded border bridge brothers budget capital care central champion city
citizens coast coastal co community company county courthouse court criminal crown custom day defense direct discount
dream eagle east easy elite empire equal express faith family fast father first five freedom free friendly front gate
general get go gold golden good grand great green group guardian hands help helping heart home honest hope hour hours
inc independent insurance integrity jail jails justice key king legacy legal liberty lion local lone low lucky main
metro mid midwest mountain mr ms my nation national new night north now one out pacific patriot peace people
personal pioneer plus premier premium price pro professional quick rapid red release reliable right river rock
royal safe safety secure security service services seven solutions south southern speedy star state states statewide
sun superior support sure surety swift team texas the today top total town trust trusted twenty united us usa valley
victory vip west western white yes your
alabama alaska arizona arkansas california colorado connecticut delaware florida georgia hawaii idaho illinois indiana
iowa kansas kentucky louisiana maine maryland massachusetts michigan minnesota mississippi missouri montana nebraska
nevada hampshire jersey mexico york carolina dakota ohio oklahoma oregon pennsylvania rhode island tennessee utah vermont
virginia washington wisconsin wyoming
""".split())

UNKNOWN_CHAR_COST = 3.0  # a letter left out of any known word costs more than a whole extra word


def suggest(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    domain = email.rsplit("@", 1)[1].lower().strip()
    if domain in FREE_DOMAINS:
        return None
    labels = [p for p in domain.split(".") if p]
    if len(labels) < 2:
        return None
    name = labels[-3] if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in ("co", "com") else labels[-2]
    if name == "www" or not re.search(r"[a-z]", name):
        return None
    words = [w for part in re.split(r"[-_]+", name) if part for w in _split(part)]
    return " ".join(w.upper() if w in ("llc", "usa", "vip", "aa", "aaa") else w.capitalize() for w in words) or None


def _split(text: str) -> list[str]:
    """Fewest-words split that leaves as few letters as possible outside known words (digits stay as they are)."""
    @functools.cache
    def best(i: int) -> tuple[float, tuple[str, ...]]:
        if i == len(text):
            return 0.0, ()
        options = []
        for j in range(i + 1, len(text) + 1):
            piece = text[i:j]
            if piece in WORDS or piece.isdigit():
                cost, rest = best(j)
                options.append((cost + 1, (piece, *rest)))
        # or the next letter is part of an unknown piece, joined to the unknown piece after it
        cost, rest = best(i + 1)
        if rest and rest[0] not in WORDS and not rest[0].isdigit():
            options.append((cost + UNKNOWN_CHAR_COST, (text[i] + rest[0], *rest[1:])))
        else:
            options.append((cost + UNKNOWN_CHAR_COST + 1, (text[i], *rest)))
        return min(options, key=lambda o: o[0])

    return list(best(0)[1])
