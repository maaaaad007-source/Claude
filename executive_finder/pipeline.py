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
from .enrichment import (
    DomainProfile,
    HunterClient,
    HunterError,
    SOURCE_DIRECTORY,
    SOURCE_FINDER,
    resolve_email,
)
from .geo import country_match, known_country
from .patterns import discover_pattern
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
    "Email",
    "Email Source",
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
    email_source: str = "Guess — default pattern"

    def as_row(self) -> Dict[str, str]:
        return {
            "Full Name": self.full_name,
            "Designation": self.designation,
            "Email": self.estimated_email,
            "Email Source": self.email_source,
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
    dropped_wrong_country: int = 0
    dropped_duplicate: int = 0
    kept: int = 0
    emails_observed: int = 0
    emails_guessed: int = 0
    company_pattern: str = ""
    discovered_pattern: str = ""
    enrichment_note: str = ""
    pattern_note: str = ""

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
            "{ot} off-target, {wc} wrong-country, {dup} duplicate; "
            "kept {kept} ({obs} observed emails, {gs} guessed)".format(
                raw=self.raw_results,
                np=self.dropped_not_profile,
                ut=self.dropped_unparsed_title,
                ot=self.dropped_off_target,
                wc=self.dropped_wrong_country,
                dup=self.dropped_duplicate,
                kept=self.kept,
                obs=self.emails_observed,
                gs=self.emails_guessed,
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
    country_mode: str = "off",
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

    # The country check reads the locale subdomain off the raw URL, so it must
    # run before canonical_linkedin_url() rewrites every host to www.
    if country_mode != "off":
        verdict = country_match(
            country,
            result.url,
            " ".join((result.title, result.snippet)),
            strict=(country_mode == "strict"),
        )
        if not verdict.matches:
            return None, "wrong_country"

    contact = Contact(
        full_name=parsed.full_name,
        designation=parsed.designation or fallback_category,
        estimated_email=build_email(parsed.full_name, domain, pattern) or "",
        country=country.strip(),
        category=category,
        linkedin_profile=canonical_linkedin_url(result.url),
    )
    return contact, "kept"


def _enrich_emails(
    contacts: List[Contact],
    domain: str,
    fallback_pattern: str,
    hunter_key: str,
    use_finder: bool,
    session: Optional[requests.Session],
    report: SearchReport,
    progress: Optional[ProgressHook] = None,
    discover_patterns: bool = True,
) -> List[Contact]:
    """Replace guessed addresses with observed ones wherever Hunter has them.

    A single ``domain-search`` call does most of the work: it returns the
    company's real pattern plus every address Hunter already holds for the
    domain, so several executives resolve outright and the rest are guessed
    with the company's own pattern rather than an assumed one.  Per-person
    lookups are opt-in because they cost one API credit each.
    """
    if not contacts or not domain:
        return contacts

    names = [contact.full_name for contact in contacts]

    # Free step: mine published addresses for the company's real shape. This
    # runs with or without a Hunter key, because knowing the pattern fixes
    # every guessed row at once.
    discovered = ""
    if discover_patterns:
        if progress:
            progress("Looking for published addresses…", 0.9)
        report.queries.append('"@{}"'.format(domain))
        found = discover_pattern(
            domain, search_detailed, known_names=names, session=session
        )
        report.pattern_note = found.describe()
        if found.samples:
            report.pattern_note += " (samples: {})".format(
                ", ".join(s + "@" + domain for s in found.samples[:3])
            )
        if found.usable:
            discovered = found.template
            report.discovered_pattern = found.template

    client: Optional[HunterClient] = None
    profile: Optional[DomainProfile] = None

    if not hunter_key:
        report.enrichment_note = (
            "No Hunter API key — addresses are pattern guesses"
            + (" using the inferred pattern {}.".format(discovered)
               if discovered else " using the default pattern.")
        )
        return _apply_emails(
            contacts, domain, fallback_pattern, None, None, False,
            discovered, report,
        )

    if progress:
        progress("Looking up real email addresses…", 0.95)

    client = HunterClient(hunter_key, session=session)
    try:
        profile = client.domain_search(domain)
        report.company_pattern = profile.pattern
        report.enrichment_note = (
            "Hunter knows {} addresses at {}{}.".format(
                profile.total,
                domain,
                " (pattern {})".format(profile.pattern) if profile.pattern else "",
            )
        )
    except HunterError as exc:
        report.enrichment_note = "Hunter lookup failed: {}".format(exc)

    return _apply_emails(
        contacts, domain, fallback_pattern, profile, client, use_finder,
        discovered, report,
    )


def _apply_emails(
    contacts: List[Contact],
    domain: str,
    fallback_pattern: str,
    profile: Optional[DomainProfile],
    client: Optional[HunterClient],
    use_finder: bool,
    discovered_pattern: str,
    report: SearchReport,
) -> List[Contact]:
    """Attach the best available address to each contact."""
    enriched: List[Contact] = []
    for contact in contacts:
        result = resolve_email(
            contact.full_name,
            domain,
            profile=profile,
            client=client,
            use_finder=use_finder,
            fallback_pattern=fallback_pattern,
            discovered_pattern=discovered_pattern,
        )
        if result is None:
            enriched.append(contact)
            report.emails_guessed += 1
            continue

        if result.is_observed:
            report.emails_observed += 1
        else:
            report.emails_guessed += 1

        enriched.append(
            Contact(
                full_name=contact.full_name,
                designation=contact.designation,
                estimated_email=result.address,
                country=contact.country,
                category=contact.category,
                linkedin_profile=contact.linkedin_profile,
                email_source=result.label(),
            )
        )
    return enriched


def find_contacts_detailed(
    company: str,
    domain: str = "",
    country: str = "",
    categories: Optional[Iterable[str]] = None,
    email_pattern: str = DEFAULT_PATTERN,
    max_per_category: int = 10,
    require_company: bool = False,
    country_filter: str = "strict",
    hunter_key: str = "",
    use_email_finder: bool = False,
    discover_patterns: bool = True,
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

    # Filtering on an unrecognised country would silently drop every row, so
    # the filter only engages for countries geo.py can actually verify.
    country_mode = country_filter if (country.strip() and
                                      known_country(country)) else "off"

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
                category, tokens, require_company, country_mode,
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

    contacts = _enrich_emails(
        contacts, mail_domain, email_pattern, hunter_key, use_email_finder,
        session, report, progress, discover_patterns,
    )

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
