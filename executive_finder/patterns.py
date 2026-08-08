"""Infer a company's email pattern from publicly published addresses.

The single most valuable thing a paid lookup gives us is the company's *actual*
address shape — knowing Volvo Cars uses ``{f}{last}`` fixes every row at once,
whereas assuming ``first.last`` gets every row wrong.  That shape can usually be
recovered for free: companies publish addresses in press pages, job ads, PDFs
and ``mailto:`` links, and one search turns up enough samples to vote on.

Nothing here confirms that any particular mailbox exists — a pattern inferred
this way is still a guess, and is labelled as one.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .emails import EMAIL_PATTERNS, sanitize_name

__all__ = [
    "PatternDiscovery",
    "ROLE_ACCOUNTS",
    "extract_addresses",
    "infer_pattern",
    "discover_pattern",
]

# Shared mailboxes carry no naming information and would poison the vote.
ROLE_ACCOUNTS = frozenset({
    "info", "contact", "hello", "hi", "mail", "email", "office", "admin",
    "support", "help", "helpdesk", "service", "customerservice", "care",
    "sales", "marketing", "press", "media", "pr", "communications", "comms",
    "jobs", "careers", "recruitment", "recruiting", "hr", "people",
    "investor", "investors", "ir", "legal", "privacy", "dpo", "gdpr",
    "security", "abuse", "postmaster", "webmaster", "hostmaster",
    "noreply", "no-reply", "donotreply", "notifications", "newsletter",
    "billing", "accounts", "accounting", "invoice", "finance", "orders",
    "team", "general", "enquiries", "inquiries", "ask", "web", "website",
})

# Candidate shapes, in the order ties are broken (most specific first).
_CANDIDATE_TEMPLATES: Tuple[str, ...] = (
    "{first}.{last}",
    "{f}.{last}",
    "{first}_{last}",
    "{first}-{last}",
    "{last}.{first}",
    "{f}{last}",
    "{first}{last}",
    "{first}{l}",
    "{first}",
    "{last}",
)

_LOCAL_PART = r"[A-Za-z0-9._%+-]+"


@dataclass
class PatternDiscovery:
    """What the harvested addresses told us about the company's shape."""

    template: str = ""
    confidence: int = 0
    samples: List[str] = field(default_factory=list)
    evidence: str = ""
    addresses_found: int = 0

    @property
    def usable(self) -> bool:
        return bool(self.template)

    def describe(self) -> str:
        if not self.template:
            return "No pattern could be inferred from public addresses."
        return "Inferred {} from {} public address(es) — {}".format(
            self.template, self.addresses_found, self.evidence
        )


def extract_addresses(text: str, domain: str) -> List[str]:
    """Pull every address at ``domain`` out of a blob of text."""
    if not text or not domain:
        return []
    pattern = re.compile(
        r"\b({})@{}\b".format(_LOCAL_PART, re.escape(domain)), re.IGNORECASE
    )
    seen: List[str] = []
    for local in pattern.findall(text):
        local = local.lower().strip(".-_")
        if local and local not in seen:
            seen.append(local)
    return seen


def _is_personal(local: str) -> bool:
    """Reject shared mailboxes and anything that cannot encode a name."""
    if not local or local in ROLE_ACCOUNTS:
        return False
    if local.replace(".", "").replace("-", "").replace("_", "").isdigit():
        return False
    # "info-uk", "press.se" and friends are still role accounts.
    head = re.split(r"[._-]", local)[0]
    return head not in ROLE_ACCOUNTS


def _apply(template: str, first: str, last: str) -> str:
    try:
        return template.format(
            first=first, last=last, f=first[:1], l=last[:1]
        ).strip("._-")
    except (KeyError, IndexError):
        return ""


def _shape_of(local: str) -> Optional[str]:
    """Classify a local part that has an unambiguous separator."""
    for separator, templates in (
        (".", ("{f}.{last}", "{first}.{last}")),
        ("_", ("{first}_{last}",)),
        ("-", ("{first}-{last}",)),
    ):
        if separator in local:
            head, _, tail = local.partition(separator)
            if not head.isalpha() or not tail.isalpha():
                return None
            if separator == "." and len(head) == 1:
                return "{f}.{last}"
            return templates[-1]
    return None


def infer_pattern(
    locals_seen: Sequence[str],
    known_names: Iterable[str] = (),
) -> PatternDiscovery:
    """Vote on the most likely template for a set of observed local parts.

    Two kinds of evidence are combined.  Where a local part can be reproduced
    by applying a candidate template to a name we already have, that is direct
    proof and outweighs everything else.  Otherwise the separator structure of
    the address is used, which distinguishes ``first.last`` from ``f.last`` but
    cannot tell ``flast`` from ``firstlast`` on its own.
    """
    personal = [local for local in locals_seen if _is_personal(local)]
    if not personal:
        return PatternDiscovery(addresses_found=0)

    name_pairs: List[Tuple[str, str]] = []
    for name in known_names:
        tokens = sanitize_name(name)
        if len(tokens) >= 2:
            name_pairs.append((tokens[0], tokens[-1]))

    # 1. Direct evidence: template + known name reproduces an observed address.
    matched: Counter = Counter()
    for template in _CANDIDATE_TEMPLATES:
        for first, last in name_pairs:
            candidate = _apply(template, first, last)
            if candidate and candidate in personal:
                matched[template] += 1

    if matched:
        template, hits = matched.most_common(1)[0]
        return PatternDiscovery(
            template=template,
            confidence=min(95, 60 + 15 * hits),
            samples=personal[:8],
            evidence="{} address(es) matched a known executive's name".format(hits),
            addresses_found=len(personal),
        )

    # 2. Structural evidence: separators reveal the shape without needing names.
    shapes = Counter(
        shape for shape in (_shape_of(local) for local in personal) if shape
    )
    if shapes:
        template, hits = shapes.most_common(1)[0]
        if hits >= 2 or len(personal) == 1:
            return PatternDiscovery(
                template=template,
                confidence=min(80, 40 + 15 * hits),
                samples=personal[:8],
                evidence="{} of {} addresses share this structure".format(
                    hits, len(personal)
                ),
                addresses_found=len(personal),
            )

    return PatternDiscovery(
        samples=personal[:8],
        evidence="addresses found but none revealed a usable shape",
        addresses_found=len(personal),
    )


def discover_pattern(
    domain: str,
    search_fn: Callable,
    known_names: Iterable[str] = (),
    session=None,
    max_queries: int = 1,
) -> PatternDiscovery:
    """Search for published addresses at ``domain`` and infer the pattern.

    Deliberately cheap: one query by default, because the caller pays for it
    out of the same search quota the contact search uses.
    """
    if not domain:
        return PatternDiscovery()

    queries = ['"@{}"'.format(domain), '"@{}" contact email'.format(domain)]
    harvested: List[str] = []

    for query in queries[:max_queries]:
        try:
            results, _outcomes = search_fn(query, session=session, pause=0)
        except Exception:
            continue
        for result in results:
            blob = " ".join((result.title or "", result.snippet or "",
                             result.url or ""))
            for local in extract_addresses(blob, domain):
                if local not in harvested:
                    harvested.append(local)

    return infer_pattern(harvested, known_names)
