"""Email generation logic.

Names are sanitised (accents folded, special characters removed) and formatted
using standard corporate syntax, ``first.last@domain.com`` by default.
"""

from __future__ import annotations

import re
import unicodedata
from typing import List, Optional
from urllib.parse import urlparse

__all__ = ["EMAIL_PATTERNS", "normalise_domain", "sanitize_name", "build_email"]

# Supported corporate address shapes, keyed by the label shown in the UI.
EMAIL_PATTERNS = {
    "first.last": "{first}.{last}",
    "firstlast": "{first}{last}",
    "f.last": "{f}.{last}",
    "flast": "{f}{last}",
    "first_last": "{first}_{last}",
    "first": "{first}",
    "last.first": "{last}.{first}",
}

DEFAULT_PATTERN = "first.last"

# Name particles that are conventionally dropped from generated addresses.
_PARTICLES = {"van", "von", "de", "del", "della", "der", "den", "di", "da", "du",
              "la", "le", "el", "al", "bin", "ibn", "af", "ter", "op", "ten"}

_NON_ALPHA = re.compile(r"[^a-z]")


def sanitize_name(value: str) -> List[str]:
    """Fold a display name into lowercase ASCII tokens fit for an address."""
    if not value:
        return []

    decomposed = unicodedata.normalize("NFKD", value)
    # Handle characters NFKD does not decompose (ø, ł, ß, æ ...).
    folded = (
        decomposed.replace("ø", "o").replace("Ø", "O")
        .replace("ł", "l").replace("Ł", "L")
        .replace("ß", "ss")
        .replace("æ", "ae").replace("Æ", "Ae")
        .replace("œ", "oe").replace("Œ", "Oe")
        .replace("đ", "d").replace("Đ", "D")
        .replace("ð", "d").replace("Ð", "D")
        .replace("þ", "th").replace("Þ", "Th")
    )
    ascii_only = "".join(ch for ch in folded if not unicodedata.combining(ch))

    tokens = []
    for raw in re.split(r"[\s\-_.]+", ascii_only):
        token = _NON_ALPHA.sub("", raw.lower())
        if token:
            tokens.append(token)
    return tokens


def normalise_domain(domain: str, company: str = "") -> str:
    """Reduce user input to a bare mail domain.

    Accepts full URLs, ``www.`` prefixes and stray whitespace.  Falls back to
    ``companyname.com`` when no domain was supplied.
    """
    domain = (domain or "").strip()
    if domain:
        if "://" in domain:
            domain = urlparse(domain).netloc or domain
        domain = domain.split("/")[0].split("@")[-1].strip().lower()
        domain = re.sub(r"^www\.", "", domain)
        domain = _clean_host(domain)
        if domain:
            return domain

    slug = "".join(sanitize_name(company))
    return "{}.com".format(slug) if slug else ""


def _clean_host(host: str) -> str:
    host = re.sub(r"[^a-z0-9.\-]", "", host).strip(".-")
    return host if "." in host else ""


def build_email(
    full_name: str,
    domain: str,
    pattern: str = DEFAULT_PATTERN,
) -> Optional[str]:
    """Format a corporate address for ``full_name`` at ``domain``.

    Returns ``None`` when the name or domain cannot produce a usable address.
    """
    if not domain:
        return None

    tokens = sanitize_name(full_name)
    if not tokens:
        return None

    first = tokens[0]
    remainder = tokens[1:]
    # Drop nobiliary particles from the middle so "Jan van der Berg" -> jan.berg
    surname_tokens = [t for t in remainder if t not in _PARTICLES] or remainder
    last = surname_tokens[-1] if surname_tokens else ""

    # Accept either a named pattern ("first.last") or a raw template
    # ("{first}.{last}") — Hunter reports a company's observed pattern in the
    # latter form, using the same placeholders.
    if pattern in EMAIL_PATTERNS:
        template = EMAIL_PATTERNS[pattern]
    elif pattern and "{" in pattern:
        template = pattern
    else:
        template = EMAIL_PATTERNS[DEFAULT_PATTERN]
    try:
        local = template.format(
            first=first,
            last=last,
            f=first[0],
            l=last[0] if last else "",
        )
    except (KeyError, IndexError):
        # An unexpected placeholder in a provider-supplied pattern must not
        # take down the row; fall back to the default shape.
        local = EMAIL_PATTERNS[DEFAULT_PATTERN].format(
            first=first, last=last, f=first[0], l=last[0] if last else ""
        )
    local = local.strip("._").replace("..", ".")
    if not local:
        local = first

    return "{}@{}".format(local, domain)
