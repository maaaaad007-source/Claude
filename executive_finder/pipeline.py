"""Search & parsing pipeline orchestration.

Ties the role matrix, X-Ray querying, title unpackaging and email generation
together into the six-column contact matrix rendered by the UI.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence

import requests

from . import roles
from .emails import DEFAULT_PATTERN, build_email, normalise_domain, sanitize_name
from .parsing import parse_title
from .search import (
    SearchError,
    SearchResult,
    build_query,
    canonical_linkedin_url,
    is_linkedin_profile,
    search,
)

__all__ = ["Contact", "COLUMNS", "find_contacts", "contacts_to_records"]

COLUMNS = [
    "Full Name",
    "Designation",
    "Estimated Email",
    "Country",
    "Category",
    "LinkedIn Profile",
]

ProgressHook = Callable[[str, float], None]


@dataclass(frozen=True)
class Contact:
    """One row of the output matrix."""

    full_name: str
    designation: str
    estimated_email: str
    country: str
    category: str
    linkedin_profile: str

    def as_row(self) -> Dict[str, str]:
        return {
            "Full Name": self.full_name,
            "Designation": self.designation,
            "Estimated Email": self.estimated_email,
            "Country": self.country,
            "Category": self.category,
            "LinkedIn Profile": self.linkedin_profile,
        }


def _company_tokens(company: str) -> List[str]:
    """Significant lowercase tokens of a company name, for loose matching."""
    ignore = {"ab", "inc", "ltd", "llc", "gmbh", "plc", "sa", "as", "oy", "bv",
              "group", "the", "and", "co", "corp", "company"}
    tokens = [t for t in re.split(r"[^a-z0-9]+", company.lower()) if len(t) > 2]
    return [t for t in tokens if t not in ignore] or tokens


def _mentions_company(text: str, tokens: Sequence[str]) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in tokens)


def _dedupe_key(contact_url: str, full_name: str) -> str:
    if contact_url:
        return canonical_linkedin_url(contact_url).lower()
    return "name:" + "".join(sanitize_name(full_name))


def _to_contact(
    result: SearchResult,
    company: str,
    country: str,
    domain: str,
    pattern: str,
    fallback_category: str,
    company_tokens: Sequence[str],
    require_company: bool,
) -> Optional[Contact]:
    """Turn one raw result node into a contact row, or reject it."""
    if not is_linkedin_profile(result.url):
        return None

    parsed = parse_title(result.title, company)
    if parsed is None:
        return None

    category = roles.classify(parsed.designation) or roles.classify(result.snippet)
    if category is None:
        # The header carried no recognisable role keyword; only trust the
        # query's own category when the snippet at least names the company.
        if not _mentions_company(result.snippet + " " + result.title, company_tokens):
            return None
        category = fallback_category

    if require_company and not _mentions_company(
        " ".join((result.title, result.snippet)), company_tokens
    ):
        return None

    return Contact(
        full_name=parsed.full_name,
        designation=parsed.designation or fallback_category,
        estimated_email=build_email(parsed.full_name, domain, pattern) or "",
        country=country.strip(),
        category=category,
        linkedin_profile=canonical_linkedin_url(result.url),
    )


def find_contacts(
    company: str,
    domain: str = "",
    country: str = "",
    categories: Optional[Iterable[str]] = None,
    email_pattern: str = DEFAULT_PATTERN,
    max_per_category: int = 10,
    require_company: bool = False,
    pause: float = 1.5,
    session: Optional[requests.Session] = None,
    progress: Optional[ProgressHook] = None,
) -> List[Contact]:
    """Run the full pipeline and return de-duplicated contact rows.

    ``progress`` is called as ``progress(message, fraction)`` before each
    category is searched so the UI can render a live status bar.
    """
    if not company or not company.strip():
        raise ValueError("Company name is required.")

    company = company.strip()
    selected = [c for c in (categories or roles.CATEGORIES) if c in roles.ROLE_MATRIX]
    if not selected:
        raise ValueError("Select at least one role category.")

    mail_domain = normalise_domain(domain, company)
    tokens = _company_tokens(company)
    session = session or requests.Session()

    contacts: List[Contact] = []
    seen: set = set()
    failures: List[str] = []

    for index, category in enumerate(selected):
        if progress:
            progress("Searching {}…".format(category), index / len(selected))

        query = build_query(company, roles.ROLE_MATRIX[category], country)
        try:
            results = search(query, session=session, pause=min(pause, 1.0))
        except SearchError as exc:
            failures.append(str(exc))
            results = []

        kept = 0
        for result in results:
            if kept >= max_per_category:
                break
            contact = _to_contact(
                result, company, country, mail_domain, email_pattern,
                category, tokens, require_company,
            )
            if contact is None:
                continue
            key = _dedupe_key(contact.linkedin_profile, contact.full_name)
            if key in seen:
                continue
            seen.add(key)
            contacts.append(contact)
            kept += 1

        if pause and index < len(selected) - 1:
            time.sleep(pause)

    if progress:
        progress("Done", 1.0)

    if failures and not contacts:
        raise SearchError(failures[0])

    order = {category: i for i, category in enumerate(roles.CATEGORIES)}
    contacts.sort(key=lambda c: (order.get(c.category, 99), c.full_name.lower()))
    return contacts


def contacts_to_records(contacts: Iterable[Contact]) -> List[Dict[str, str]]:
    """Render contacts as dicts keyed by the output matrix column headers."""
    return [contact.as_row() for contact in contacts]
