"""Search & parsing pipeline orchestration.

Ties the role matrix, X-Ray querying, title unpackaging and email generation
together into the six-column contact matrix rendered by the UI.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence

import requests

from . import roles
from .emails import DEFAULT_PATTERN, build_email, normalise_domain, sanitize_name
from .parsing import parse_title
from .search import (
    ProviderOutcome,
    SearchError,
    SearchResult,
    build_query,
    canonical_linkedin_url,
    is_linkedin_profile,
    search_detailed,
)

__all__ = [
    "COLUMNS",
    "Contact",
    "SearchReport",
    "contacts_to_records",
    "find_contacts",
    "find_contacts_detailed",
    "split_company_input",
]

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


@dataclass
class SearchReport:
    """Why a run produced the rows it did — surfaced in the UI diagnostics."""

    queries: List[str] = field(default_factory=list)
    outcomes: List[ProviderOutcome] = field(default_factory=list)
    raw_results: int = 0
    dropped_not_profile: int = 0
    dropped_unparsed_title: int = 0
    dropped_off_target: int = 0
    dropped_duplicate: int = 0
    kept: int = 0

    @property
    def blocked(self) -> bool:
        """True when providers refused us rather than genuinely finding nothing."""
        if self.raw_results:
            return False
        return any(o.status in ("blocked", "error") for o in self.outcomes)

    @property
    def usable_provider(self) -> bool:
        return any(o.status == "ok" for o in self.outcomes)

    def summary(self) -> str:
        return (
            "{raw} raw results — dropped {np} non-profile, {ut} unparseable, "
            "{ot} off-target, {dup} duplicate; kept {kept}".format(
                raw=self.raw_results,
                np=self.dropped_not_profile,
                ut=self.dropped_unparsed_title,
                ot=self.dropped_off_target,
                dup=self.dropped_duplicate,
                kept=self.kept,
            )
        )


# A company field that is really a domain: "Spotify.com", "volvocars.com".
_DOMAINISH = re.compile(
    r"^\s*(?:https?://)?(?:www\.)?([a-z0-9][a-z0-9\-]*)"
    r"((?:\.[a-z]{2,})+)\s*/?\s*$",
    re.IGNORECASE,
)


def split_company_input(company: str, domain: str = "") -> tuple:
    """Recover a company name and domain when the name field holds a domain.

    Typing "Spotify.com" into the company field is a natural mistake, and it
    poisons every query — no LinkedIn headline contains the string
    "Spotify.com".  Split it into ``("Spotify", "spotify.com")`` instead, and
    only adopt the domain when the user did not supply one.
    """
    match = _DOMAINISH.match(company or "")
    if not match:
        return (company or "").strip(), (domain or "").strip()

    label, suffix = match.group(1), match.group(2)
    recovered_domain = (label + suffix).lower()
    return label, (domain or "").strip() or recovered_domain


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
) -> tuple:
    """Turn one raw result node into ``(contact, reason)``.

    ``contact`` is ``None`` when the node was rejected, and ``reason`` names the
    filter that rejected it so the UI can explain an empty result set.
    """
    if not is_linkedin_profile(result.url):
        return None, "not_profile"

    parsed = parse_title(result.title, company)
    if parsed is None:
        return None, "unparsed_title"

    category = roles.classify(parsed.designation) or roles.classify(result.snippet)
    if category is None:
        # The header carried no recognisable role keyword; only trust the
        # query's own category when the snippet at least names the company.
        if not _mentions_company(result.snippet + " " + result.title, company_tokens):
            return None, "off_target"
        category = fallback_category

    if require_company and not _mentions_company(
        " ".join((result.title, result.snippet)), company_tokens
    ):
        return None, "off_target"

    contact = Contact(
        full_name=parsed.full_name,
        designation=parsed.designation or fallback_category,
        estimated_email=build_email(parsed.full_name, domain, pattern) or "",
        country=country.strip(),
        category=category,
        linkedin_profile=canonical_linkedin_url(result.url),
    )
    return contact, "kept"


def find_contacts_detailed(
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
) -> tuple:
    """Run the full pipeline, returning ``(contacts, report)``.

    ``progress`` is called as ``progress(message, fraction)`` before each
    category is searched so the UI can render a live status bar.  The report
    records every query, every provider outcome and every filter drop, so an
    empty result set can be explained rather than merely announced.
    """
    company, domain = split_company_input(company, domain)
    if not company:
        raise ValueError("Company name is required.")

    selected = [c for c in (categories or roles.CATEGORIES) if c in roles.ROLE_MATRIX]
    if not selected:
        raise ValueError("Select at least one role category.")

    mail_domain = normalise_domain(domain, company)
    tokens = _company_tokens(company)
    session = session or requests.Session()

    contacts: List[Contact] = []
    seen: set = set()
    report = SearchReport()
    failures: List[str] = []

    for index, category in enumerate(selected):
        if progress:
            progress("Searching {}\u2026".format(category), index / len(selected))

        query = build_query(company, roles.ROLE_MATRIX[category], country)
        report.queries.append(query)
        try:
            results, outcomes = search_detailed(
                query, session=session, pause=min(pause, 1.0)
            )
        except SearchError as exc:
            failures.append(str(exc))
            results, outcomes = [], getattr(exc, "outcomes", [])
        report.outcomes.extend(outcomes)
        report.raw_results += len(results)

        kept = 0
        for result in results:
            if kept >= max_per_category:
                break
            contact, reason = _to_contact(
                result, company, country, mail_domain, email_pattern,
                category, tokens, require_company,
            )
            if contact is None:
                setattr(report, "dropped_" + reason,
                        getattr(report, "dropped_" + reason) + 1)
                continue
            key = _dedupe_key(contact.linkedin_profile, contact.full_name)
            if key in seen:
                report.dropped_duplicate += 1
                continue
            seen.add(key)
            contacts.append(contact)
            kept += 1

        if pause and index < len(selected) - 1:
            time.sleep(pause)

    if progress:
        progress("Done", 1.0)

    if failures and not contacts:
        error = SearchError(failures[0])
        error.outcomes = report.outcomes
        error.report = report
        raise error

    order = {category: i for i, category in enumerate(roles.CATEGORIES)}
    contacts.sort(key=lambda c: (order.get(c.category, 99), c.full_name.lower()))
    report.kept = len(contacts)
    return contacts, report


def find_contacts(*args, **kwargs) -> List[Contact]:
    """Run the pipeline and return de-duplicated contact rows."""
    return find_contacts_detailed(*args, **kwargs)[0]


def contacts_to_records(contacts: Iterable[Contact]) -> List[Dict[str, str]]:
    """Render contacts as dicts keyed by the output matrix column headers."""
    return [contact.as_row() for contact in contacts]
