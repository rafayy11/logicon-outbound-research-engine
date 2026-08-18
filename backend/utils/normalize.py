"""Shared normalization helpers. Domain normalization is the backbone of
company identity -- everything else (dedup, Clay lookups, exports) relies
on this being consistent.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlparse

_WWW_RE = re.compile(r"^www\.", re.IGNORECASE)

# Free/personal email providers are not company domains -- an association
# member using one tells us nothing about a company's identity. Shared by
# every source adapter that derives company domain from a person's email
# (NCRA, TCRA, ...). Includes legacy US ISP webmail domains (confirmed
# live: a real TCRA search returned webtv.net, sbcglobal.net, and rr.com
# regional subdomains as "companies" before this list was widened).
FREE_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com",
    "icloud.com", "live.com", "msn.com", "comcast.net", "att.net",
    "protonmail.com", "me.com", "webtv.net", "sbcglobal.net",
    "bellsouth.net", "verizon.net", "earthlink.net", "charter.net",
    "cox.net", "windstream.net", "centurylink.net", "frontier.com",
    "embarqmail.com", "juno.com", "netzero.net", "prodigy.net",
    "mail.com", "gmx.com", "ymail.com", "rocketmail.com",
}

# Road Runner/Spectrum email is issued per-region as <region>.rr.com
# (gt.rr.com, nyc.rr.com, ...) -- a suffix check catches all of them
# without enumerating every region.
_FREE_EMAIL_SUFFIXES = (".rr.com",)


def is_free_email_domain(domain: str | None) -> bool:
    if not domain:
        return False
    d = domain.lower()
    return d in FREE_EMAIL_DOMAINS or d.endswith(_FREE_EMAIL_SUFFIXES)

_COMPANY_SUFFIXES = [
    "inc", "inc.", "llc", "l.l.c.", "llp", "l.l.p.", "ltd", "ltd.", "co",
    "co.", "corp", "corp.", "corporation", "company", "pllc", "pc", "p.c.",
]

_NON_ALNUM_RE = re.compile(r"[^a-z0-9 ]+")
_MULTI_SPACE_RE = re.compile(r"\s+")


def normalize_domain(value: str | None) -> str | None:
    """https://www.Example.com/path -> example.com. Returns None for
    empty/unparseable input rather than guessing."""
    if not value:
        return None
    value = value.strip()
    if not value:
        return None

    candidate = value if "://" in value else f"http://{value}"

    try:
        parsed = urlparse(candidate)
    except ValueError:
        return None

    host = (parsed.netloc or "").strip().lower()
    host = host.split(":")[0]  # drop port
    host = _WWW_RE.sub("", host)

    if not host or "." not in host:
        return None
    return host


def normalize_company_name(name: str | None) -> str | None:
    """For fuzzy-dedup purposes only -- lowercase, strip legal suffixes and
    punctuation. Never used for display."""
    if not name:
        return None
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    n = n.lower().strip()
    n = _NON_ALNUM_RE.sub(" ", n)
    n = _MULTI_SPACE_RE.sub(" ", n).strip()
    tokens = [t for t in n.split(" ") if t not in _COMPANY_SUFFIXES]
    return " ".join(tokens) if tokens else None


def normalize_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits or None


def identity_key(company_name: str | None, city: str | None, state: str | None) -> str | None:
    """Fallback identity when there is no domain: normalized name + location."""
    name = normalize_company_name(company_name)
    if not name:
        return None
    loc = f"{(city or '').strip().lower()}|{(state or '').strip().lower()}"
    return f"{name}|{loc}"


_US_ADDRESS_RE = re.compile(
    r"^(?P<street>.*?),\s*(?P<city>[A-Za-z .'-]+),\s*(?P<state>[A-Z]{2})\s*(?P<zip>\d{5}(-\d{4})?)?$"
)


def parse_us_address(text: str | None) -> dict[str, str | None]:
    """'13101 Northwest Freeway Suite 210, Houston, TX 77040' ->
    {street, city, state, zip}. Returns all-None fields (not an exception)
    if the free-text address doesn't match the common 'Street, City, ST
    ZIP' pattern -- callers keep the raw text as `address` regardless."""
    empty = {"street": None, "city": None, "state": None, "zip": None}
    if not text:
        return empty
    m = _US_ADDRESS_RE.match(text.strip())
    if not m:
        return empty
    return {
        "street": m.group("street").strip(),
        "city": m.group("city").strip(),
        "state": m.group("state"),
        "zip": m.group("zip"),
    }


def domain_to_title_case_name(domain: str | None) -> str | None:
    """Fallback company-name guess from a bare domain, used only when a
    source has no explicit company name (e.g. a member-email-derived
    domain). Clay's company-domain enrichment overwrites this with the
    real registered name during the enrichment stage."""
    if not domain:
        return None
    label = domain.split(".")[0]
    label = re.sub(r"[-_]+", " ", label)
    return label.strip().title() or None
