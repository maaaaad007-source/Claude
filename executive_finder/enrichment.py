"""Real email lookup via Hunter.io.

Pattern guessing is wrong whenever a company does not use the pattern you
assumed, and it gives no signal about which addresses actually exist.  This
module replaces the guess with observed data where it can:

* one ``domain-search`` call per run returns the company's *actual* dominant
  pattern plus every address Hunter already holds for that domain, which often
  covers several of the executives outright;
* an optional per-person ``email-finder`` call resolves the rest;
* anything still unresolved falls back to a pattern guess, clearly labelled.

Every address carries its provenance so a verified address is never confused
with a guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import requests

from .emails import build_email, sanitize_name

__all__ = [
    "DomainProfile",
    "EmailResult",
    "HunterClient",
    "HunterError",
    "resolve_email",
]

DOMAIN_SEARCH_ENDPOINT = "https://api.hunter.io/v2/domain-search"
EMAIL_FINDER_ENDPOINT = "https://api.hunter.io/v2/email-finder"

# Provenance labels, most trustworthy first.
SOURCE_DIRECTORY = "directory"
SOURCE_FINDER = "finder"
SOURCE_COMPANY_PATTERN = "company-pattern"
SOURCE_DISCOVERED_PATTERN = "discovered-pattern"
SOURCE_GUESS = "guess"


class HunterError(RuntimeError):
    """Raised when Hunter refuses a request; carries the API's own message."""


@dataclass(frozen=True)
class EmailResult:
    """An address plus where it came from and how much to trust it."""

    address: str
    source: str
    confidence: int = 0
    status: str = ""

    @property
    def is_observed(self) -> bool:
        """True when Hunter actually saw this address, rather than inferring it."""
        return self.source in (SOURCE_DIRECTORY, SOURCE_FINDER)

    def label(self) -> str:
        """Human-readable provenance for the results table."""
        # Kept short so the results column reads at a glance, but every
        # inferred address still carries the word "Guess".
        if self.source == SOURCE_DIRECTORY:
            base = "Verified"
        elif self.source == SOURCE_FINDER:
            base = "Found"
        elif self.source == SOURCE_COMPANY_PATTERN:
            base = "Guess · company pattern"
        elif self.source == SOURCE_DISCOVERED_PATTERN:
            base = "Guess · inferred pattern"
        else:
            base = "Guess · default pattern"
        parts = [base]
        if self.confidence:
            parts.append("{}%".format(self.confidence))
        if self.status and self.status != "unknown":
            parts.append(self.status)
        return " · ".join(parts)


@dataclass
class DomainProfile:
    """What one ``domain-search`` call told us about a company."""

    domain: str = ""
    pattern: str = ""
    by_name: Dict[Tuple[str, str], EmailResult] = field(default_factory=dict)
    total: int = 0

    def lookup(self, full_name: str) -> Optional[EmailResult]:
        """Find an observed address for a person by first and last name."""
        tokens = sanitize_name(full_name)
        if len(tokens) < 2:
            return None
        return self.by_name.get((tokens[0], tokens[-1]))


class HunterClient:
    """Thin Hunter.io client returning provenance-tagged addresses."""

    def __init__(
        self,
        api_key: str,
        session: Optional[requests.Session] = None,
        timeout: float = 20.0,
    ) -> None:
        self.api_key = (api_key or "").strip().strip("\"'‘’“”").strip()
        self.session = session or requests.Session()
        self.timeout = timeout

    # -- internals -------------------------------------------------------- #
    def _get(self, endpoint: str, params: dict) -> dict:
        if not self.api_key:
            raise HunterError("no Hunter API key configured")
        response = self.session.get(
            endpoint,
            params={**params, "api_key": self.api_key},
            timeout=self.timeout,
        )
        if not response.ok:
            detail = ""
            try:
                errors = response.json().get("errors") or []
                detail = "; ".join(e.get("details", "") for e in errors)
            except Exception:
                detail = (response.text or "").strip()[:200]
            raise HunterError(
                "Hunter HTTP {}: {}".format(response.status_code, detail or "no detail")
            )
        return response.json()

    # -- public API ------------------------------------------------------- #
    def domain_search(self, domain: str, limit: int = 100) -> DomainProfile:
        """Fetch the company's observed pattern and known addresses in one call."""
        payload = self._get(
            DOMAIN_SEARCH_ENDPOINT, {"domain": domain, "limit": limit}
        ).get("data") or {}

        profile = DomainProfile(
            domain=payload.get("domain") or domain,
            pattern=payload.get("pattern") or "",
        )
        for entry in payload.get("emails") or []:
            address = entry.get("value") or ""
            first = entry.get("first_name") or ""
            last = entry.get("last_name") or ""
            if not address or not first or not last:
                continue
            key = (
                "".join(sanitize_name(first)[:1] or [""]),
                "".join(sanitize_name(last)[-1:] or [""]),
            )
            if not key[0] or not key[1]:
                continue
            profile.by_name[key] = EmailResult(
                address=address,
                source=SOURCE_DIRECTORY,
                confidence=int(entry.get("confidence") or 0),
                status=((entry.get("verification") or {}).get("status") or ""),
            )
        profile.total = len(profile.by_name)
        return profile

    def email_finder(self, domain: str, full_name: str) -> Optional[EmailResult]:
        """Resolve one person's address by name."""
        tokens = sanitize_name(full_name)
        if len(tokens) < 2:
            return None
        payload = self._get(
            EMAIL_FINDER_ENDPOINT,
            {"domain": domain, "first_name": tokens[0], "last_name": tokens[-1]},
        ).get("data") or {}

        address = payload.get("email") or ""
        if not address:
            return None
        return EmailResult(
            address=address,
            source=SOURCE_FINDER,
            confidence=int(payload.get("score") or 0),
            status=((payload.get("verification") or {}).get("status") or ""),
        )


def resolve_email(
    full_name: str,
    domain: str,
    profile: Optional[DomainProfile] = None,
    client: Optional[HunterClient] = None,
    use_finder: bool = False,
    fallback_pattern: str = "first.last",
    discovered_pattern: str = "",
) -> Optional[EmailResult]:
    """Best available address for one person, with its provenance.

    Tries the already-fetched directory first (free — it came from the single
    domain-search call), then an optional per-person lookup, then a pattern
    guess using the company's own pattern when Hunter reported one.
    """
    if not domain:
        return None

    if profile is not None:
        observed = profile.lookup(full_name)
        if observed is not None:
            return observed

    if use_finder and client is not None:
        try:
            found = client.email_finder(domain, full_name)
        except HunterError:
            found = None
        if found is not None:
            return found

    company_pattern = profile.pattern if profile is not None else ""
    if company_pattern:
        guess = build_email(full_name, domain, company_pattern)
        if guess:
            return EmailResult(guess, SOURCE_COMPANY_PATTERN)

    # A pattern mined from published addresses beats an assumed default.
    if discovered_pattern:
        guess = build_email(full_name, domain, discovered_pattern)
        if guess:
            return EmailResult(guess, SOURCE_DISCOVERED_PATTERN)

    guess = build_email(full_name, domain, fallback_pattern)
    return EmailResult(guess, SOURCE_GUESS) if guess else None
