"""Title & link unpackaging.

Search result headers for LinkedIn profiles follow a small number of shapes::

    Daniel Ek - Chief Executive Officer at Spotify - Spotify | LinkedIn
    Daniel Ek – CEO – Spotify | LinkedIn
    Daniel Ek on LinkedIn: some post text
    Daniel Ek, MBA - Managing Director - Volvo Cars - LinkedIn

This module reduces any of them to a clean ``(full_name, designation)`` pair
and rejects headers that are not an individual's profile at all.
"""

from __future__ import annotations

import re
import unicodedata
from typing import NamedTuple, Optional

__all__ = ["ParsedTitle", "parse_title", "is_plausible_name", "clean_name"]

# Separators LinkedIn and the search engines use between title segments.
_SEPARATOR = re.compile(r"\s+[-–—|·]+\s+")

# Trailing site branding, with or without a locale suffix.
_TRAILING_BRAND = re.compile(
    r"\s*[-–—|·]\s*LinkedIn(?:\s*\(.*?\))?\s*$", re.IGNORECASE
)
_FEED_SUFFIX = re.compile(r"\s+on LinkedIn\s*:.*$", re.IGNORECASE)

# Post-nominals and credentials that trail a name.
_CREDENTIALS = re.compile(
    r",\s*(?:M\.?B\.?A|Ph\.?D|M\.?Sc|B\.?Sc|M\.?A|CPA|PMP|CFA|MD|JD|MSc|BSc|"
    r"CIPD|SHRM(?:-[A-Z]+)?)\.?\b.*$",
    re.IGNORECASE,
)

# Words that mark a header as an aggregate/listing page rather than a person.
_NON_NAME_TOKENS = {
    "linkedin", "profiles", "profile", "jobs", "job", "top", "best", "list",
    "people", "employees", "company", "search", "results", "directory",
    "hiring", "posts", "post", "articles", "vacancies", "team",
}

_TITLE_HINTS = re.compile(
    r"\b(?:ceo|cto|coo|cfo|chief|head|director|vp|vice\s+president|president|"
    r"manager|lead|officer|founder|partner|owner|principal)\b",
    re.IGNORECASE,
)


class ParsedTitle(NamedTuple):
    """The unpacked components of a search result header."""

    full_name: str
    designation: str


def _strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def clean_name(candidate: str) -> str:
    """Trim credentials, emoji and decorative punctuation from a name."""
    candidate = _CREDENTIALS.sub("", candidate)
    candidate = candidate.split(",")[0]
    # Drop anything that is not a letter, space, apostrophe, hyphen or dot.
    candidate = "".join(
        ch for ch in candidate if ch.isalpha() or ch in " '-." or ch.isspace()
    )
    return " ".join(candidate.split()).strip(" .-'")


def is_plausible_name(candidate: str) -> bool:
    """True when ``candidate`` reads like a person's name rather than a phrase."""
    if not candidate:
        return False
    if len(candidate) > 60 or any(ch.isdigit() for ch in candidate):
        return False

    tokens = candidate.split()
    if not 1 < len(tokens) <= 5:
        return False

    for token in tokens:
        stripped = _strip_accents(token).strip(".'-")
        if not stripped or not stripped.replace("'", "").replace("-", "").isalpha():
            return False
        if stripped.lower() in _NON_NAME_TOKENS:
            return False

    # A header that reads like a job title on its own is not a name.
    return not _TITLE_HINTS.search(candidate)


def _format_designation(segments: list, company: str) -> str:
    """Join the non-name segments into a ``Title at Company`` designation."""
    segments = [segment.strip() for segment in segments if segment and segment.strip()]
    if not segments:
        return ""

    role = segments[0]
    if re.search(r"\bat\b", role, re.IGNORECASE):
        return role

    employer = segments[1] if len(segments) > 1 else company
    if not employer or employer.lower() == role.lower():
        return role
    return "{} at {}".format(role, employer)


def parse_title(title: str, company: str = "") -> Optional[ParsedTitle]:
    """Unpack a result header into a name and designation.

    Returns ``None`` when the header is not an individual profile — listing
    pages, company pages and post excerpts are all rejected here.
    """
    if not title:
        return None

    header = " ".join(title.split())
    header = _FEED_SUFFIX.sub("", header)
    header = _TRAILING_BRAND.sub("", header)
    header = re.sub(r"\s*[-–—|·]\s*$", "", header).strip()
    if not header:
        return None

    segments = _SEPARATOR.split(header)
    name = clean_name(segments[0])
    if not is_plausible_name(name):
        return None

    return ParsedTitle(
        full_name=name,
        designation=_format_designation(segments[1:], company),
    )
