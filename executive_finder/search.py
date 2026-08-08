"""Targeted X-Ray querying against public LinkedIn profile indexes.

The module builds ``site:linkedin.com/in/`` queries, issues them against HTML
search endpoints with desktop browser headers, and unpacks the raw result nodes
into ``SearchResult`` records with tracking redirects stripped.
"""

from __future__ import annotations

import base64
import os
import random
import re
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs, unquote, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

__all__ = [
    "PROVIDERS",
    "Provider",
    "ProviderOutcome",
    "SearchError",
    "SearchResult",
    "api_key",
    "build_query",
    "canonical_linkedin_url",
    "configure_api_keys",
    "is_linkedin_profile",
    "looks_blocked",
    "parse_bing",
    "parse_ddg_lite",
    "parse_duckduckgo",
    "parse_mojeek",
    "search",
    "search_detailed",
    "unwrap_redirect",
]

LINKEDIN_SITE = "linkedin.com/in/"

# Desktop browser headers.  Search front-ends serve a stripped-down or empty
# page to clients that look automated, so we emulate a normal browser session.
USER_AGENTS: Sequence[str] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.4 Safari/605.1.15",
)

BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

DUCKDUCKGO_ENDPOINT = "https://html.duckduckgo.com/html/"
DDG_LITE_ENDPOINT = "https://lite.duckduckgo.com/lite/"
BING_ENDPOINT = "https://www.bing.com/search"
MOJEEK_ENDPOINT = "https://www.mojeek.com/search"
SERPER_ENDPOINT = "https://google.serper.dev/search"
BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

_PROFILE_PATH = re.compile(r"^/in/[^/]+", re.IGNORECASE)
_LINKEDIN_HOST = re.compile(r"(?:^|\.)linkedin\.com$", re.IGNORECASE)


class SearchError(RuntimeError):
    """Raised when every configured search provider failed to answer."""


@dataclass(frozen=True)
class SearchResult:
    """A single unpacked search engine result node."""

    title: str
    url: str
    snippet: str = ""

    def __post_init__(self) -> None:  # pragma: no cover - trivial normalisation
        object.__setattr__(self, "title", " ".join(self.title.split()))
        object.__setattr__(self, "snippet", " ".join(self.snippet.split()))


# --------------------------------------------------------------------------- #
# Query construction
# --------------------------------------------------------------------------- #
def _quote(term: str) -> str:
    return '"{}"'.format(term.strip().replace('"', ""))


def build_query(
    company: str,
    role_keywords: Iterable[str],
    country: str = "",
) -> str:
    """Build an X-Ray query for one company / role group / region.

    A single query covers a whole role group by OR-ing its keywords together,
    which keeps the number of outbound requests to one per category::

        site:linkedin.com/in/ "Spotify" ("CEO" OR "Chief Executive Officer") "Sweden"
    """
    if not company or not company.strip():
        raise ValueError("company is required to build a query")

    keywords = [kw.strip() for kw in role_keywords if kw and kw.strip()]
    if not keywords:
        raise ValueError("at least one role keyword is required")

    parts = ["site:" + LINKEDIN_SITE, _quote(company)]
    if len(keywords) == 1:
        parts.append(_quote(keywords[0]))
    else:
        parts.append("(" + " OR ".join(_quote(kw) for kw in keywords) + ")")
    if country and country.strip():
        parts.append(_quote(country))
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# URL handling
# --------------------------------------------------------------------------- #
def _decode_bing_target(value: str) -> str:
    """Decode Bing's ``u=a1<base64url>`` redirect parameter.

    Bing routes every organic result through ``/ck/a?…&u=a1<base64url>``.
    Left encoded, the href never looks like a LinkedIn profile and the whole
    result set is discarded as off-site.
    """
    if not value:
        return ""
    payload = value[2:] if value[:2] in ("a1", "a2") else value
    try:
        # base64url, stripped of padding by Bing — restore it before decoding.
        decoded = base64.urlsafe_b64decode(
            payload + "=" * (-len(payload) % 4)
        ).decode("utf-8", "replace")
    except Exception:
        return ""
    return decoded if decoded.startswith(("http://", "https://")) else ""


def unwrap_redirect(href: str) -> str:
    """Strip search-engine tracking redirects from a result href.

    DuckDuckGo wraps results as ``//duckduckgo.com/l/?uddg=<encoded>`` and Bing
    uses ``/ck/a?...&u=a1<base64>``; both are reduced to the destination URL
    where possible, otherwise the input is returned unchanged.
    """
    if not href:
        return ""

    href = href.strip()
    if href.startswith("//"):
        href = "https:" + href

    parsed = urlparse(href)
    host = parsed.netloc.lower()
    query = parse_qs(parsed.query)

    if "duckduckgo.com" in host and "uddg" in query:
        return unquote(query["uddg"][0])
    if "google." in host and parsed.path == "/url" and "q" in query:
        return unquote(query["q"][0])
    if "bing.com" in host and "u" in query:
        decoded = _decode_bing_target(query["u"][0])
        if decoded:
            return decoded
    for key in ("url", "u", "r"):
        if key in query and query[key][0].startswith(("http://", "https://")):
            return unquote(query[key][0])
    return href


def is_linkedin_profile(url: str) -> bool:
    """True when the URL points at an individual LinkedIn profile page."""
    if not url:
        return False
    parsed = urlparse(url if "://" in url else "https://" + url)
    if not _LINKEDIN_HOST.search(parsed.netloc.split(":")[0]):
        return False
    return bool(_PROFILE_PATH.match(parsed.path))


def canonical_linkedin_url(url: str) -> str:
    """Normalise a profile URL: drop locale subdomain, query and fragment."""
    parsed = urlparse(url if "://" in url else "https://" + url)
    match = _PROFILE_PATH.match(parsed.path)
    path = match.group(0) if match else parsed.path
    return urlunparse(("https", "www.linkedin.com", path.rstrip("/"), "", "", ""))


# --------------------------------------------------------------------------- #
# Result-node parsing
# --------------------------------------------------------------------------- #
def _soup(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:  # pragma: no cover - lxml missing on the host
        return BeautifulSoup(html, "html.parser")


def parse_duckduckgo(html: str) -> List[SearchResult]:
    """Extract result nodes from the DuckDuckGo HTML endpoint."""
    results: List[SearchResult] = []
    soup = _soup(html)
    for node in soup.select("div.result, div.web-result"):
        anchor = node.select_one("a.result__a")
        if anchor is None:
            continue
        snippet_node = node.select_one(".result__snippet")
        results.append(
            SearchResult(
                title=anchor.get_text(" ", strip=True),
                url=unwrap_redirect(anchor.get("href", "")),
                snippet=snippet_node.get_text(" ", strip=True) if snippet_node else "",
            )
        )
    return results


def parse_ddg_lite(html: str) -> List[SearchResult]:
    """Extract result nodes from the DuckDuckGo Lite table layout."""
    results: List[SearchResult] = []
    soup = _soup(html)
    for anchor in soup.select("a.result-link"):
        row = anchor.find_parent("tr")
        snippet = ""
        if row is not None:
            following = row.find_next_sibling("tr")
            if following is not None:
                node = following.select_one(".result-snippet") or following
                snippet = node.get_text(" ", strip=True)
        results.append(
            SearchResult(
                title=anchor.get_text(" ", strip=True),
                url=unwrap_redirect(anchor.get("href", "")),
                snippet=snippet,
            )
        )
    return results


def parse_mojeek(html: str) -> List[SearchResult]:
    """Extract result nodes from a Mojeek SERP."""
    results: List[SearchResult] = []
    soup = _soup(html)
    for node in soup.select("ul.results-standard li, li.result"):
        anchor = node.select_one("a.title") or node.select_one("h2 a")
        if anchor is None:
            continue
        snippet_node = node.select_one("p.s, .s")
        results.append(
            SearchResult(
                title=anchor.get_text(" ", strip=True),
                url=unwrap_redirect(anchor.get("href", "")),
                snippet=snippet_node.get_text(" ", strip=True) if snippet_node else "",
            )
        )
    return results


def parse_bing(html: str) -> List[SearchResult]:
    """Extract result nodes from a Bing SERP."""
    results: List[SearchResult] = []
    soup = _soup(html)
    for node in soup.select("li.b_algo"):
        anchor = node.select_one("h2 a")
        if anchor is None:
            continue
        snippet_node = node.select_one("p, .b_caption")
        results.append(
            SearchResult(
                title=anchor.get_text(" ", strip=True),
                url=unwrap_redirect(anchor.get("href", "")),
                snippet=snippet_node.get_text(" ", strip=True) if snippet_node else "",
            )
        )
    return results


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #
def _headers() -> dict:
    headers = dict(BASE_HEADERS)
    headers["User-Agent"] = random.choice(USER_AGENTS)
    return headers


def _fetch_duckduckgo(session: requests.Session, query: str, timeout: float) -> str:
    response = session.post(
        DUCKDUCKGO_ENDPOINT,
        data={"q": query, "kl": "wt-wt"},
        headers={**_headers(), "Referer": "https://duckduckgo.com/"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.text


def _fetch_bing(session: requests.Session, query: str, timeout: float) -> str:
    response = session.get(
        BING_ENDPOINT,
        params={"q": query, "count": "30", "setlang": "en"},
        headers={**_headers(), "Referer": "https://www.bing.com/"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.text


def _fetch_ddg_lite(session: requests.Session, query: str, timeout: float) -> str:
    response = session.post(
        DDG_LITE_ENDPOINT,
        data={"q": query},
        headers={**_headers(), "Referer": "https://lite.duckduckgo.com/"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.text


def _fetch_mojeek(session: requests.Session, query: str, timeout: float) -> str:
    response = session.get(
        MOJEEK_ENDPOINT,
        params={"q": query},
        headers=_headers(),
        timeout=timeout,
    )
    response.raise_for_status()
    return response.text


# --------------------------------------------------------------------------- #
# Block / challenge detection
# --------------------------------------------------------------------------- #
_BLOCK_MARKERS = (
    "unusual traffic",
    "verify you are human",
    "are you a robot",
    "captcha",
    "challenge-platform",
    "/sorry/index",
    "access denied",
    "request blocked",
    "detected unusual activity",
    "enable javascript and cookies",
    "your computer network may be sending automated queries",
)


def looks_blocked(html: str) -> bool:
    """True when a 200 response is really a bot challenge or block page.

    Search front-ends rarely answer an automated client with an HTTP error;
    they answer 200 with a challenge page or an empty shell.  Without this
    check a block is indistinguishable from a genuine zero-result search.
    """
    if not html:
        return True
    lowered = html.lower()
    if any(marker in lowered for marker in _BLOCK_MARKERS):
        return True
    # A real SERP is tens of kilobytes; a stub this small never carries results.
    return len(html) < 1500


# --------------------------------------------------------------------------- #
# API-backed providers
# --------------------------------------------------------------------------- #
# Scraping HTML SERPs from a shared datacenter IP (Streamlit Community Cloud,
# CI runners, most VPS hosts) is blocked far more aggressively than from a
# residential connection.  When an API key is configured it is used first,
# because it is the only path that works reliably from such hosts.
_API_KEYS: Dict[str, str] = {}


def _clean_key(value: str) -> str:
    """Strip whitespace and stray quote characters from a pasted credential.

    Keys copied out of a chat or a TOML snippet often arrive wrapped in
    quotes — including the curly variants a phone keyboard inserts — which
    otherwise reach the API verbatim and are rejected.
    """
    return (value or "").strip().strip("\"'‘’“”").strip()


def configure_api_keys(serper: str = "", brave: str = "") -> None:
    """Register search API credentials for this process."""
    if serper:
        _API_KEYS["serper"] = _clean_key(serper)
    if brave:
        _API_KEYS["brave"] = _clean_key(brave)


def api_key(name: str) -> str:
    """Return a configured key, falling back to the environment."""
    return _API_KEYS.get(name) or os.environ.get("{}_API_KEY".format(name.upper()), "")


def _raise_for_api_status(response, provider: str) -> None:
    """Raise with the API's own error body, which explains far more than a code."""
    if response.ok:
        return
    body = (response.text or "").strip()[:200]
    raise SearchError(
        "{} HTTP {}: {}".format(provider, response.status_code, body or "no body")
    )


def _fetch_serper(session: requests.Session, query: str, timeout: float) -> List[SearchResult]:
    key = api_key("serper")
    if not key:
        raise SearchError("no Serper API key configured")
    # Minimal documented body — extra parameters are the usual cause of a 400.
    response = session.post(
        SERPER_ENDPOINT,
        json={"q": query},
        headers={"X-API-KEY": key, "Content-Type": "application/json"},
        timeout=timeout,
    )
    _raise_for_api_status(response, "serper")
    payload = response.json()
    return [
        SearchResult(
            title=item.get("title", ""),
            url=item.get("link", ""),
            snippet=item.get("snippet", ""),
        )
        for item in payload.get("organic", [])
    ]


def _fetch_brave(session: requests.Session, query: str, timeout: float) -> List[SearchResult]:
    key = api_key("brave")
    if not key:
        raise SearchError("no Brave API key configured")
    response = session.get(
        BRAVE_ENDPOINT,
        params={"q": query, "count": 20},
        headers={"X-Subscription-Token": key, "Accept": "application/json"},
        timeout=timeout,
    )
    _raise_for_api_status(response, "brave")
    payload = response.json()
    return [
        SearchResult(
            title=item.get("title", ""),
            url=item.get("url", ""),
            snippet=item.get("description", ""),
        )
        for item in payload.get("web", {}).get("results", [])
    ]


# --------------------------------------------------------------------------- #
# Provider registry and dispatch
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Provider:
    """A search back-end: how to fetch it and how to read its response."""

    name: str
    fetch: Callable
    parse: Optional[Callable] = None
    needs_key: str = ""

    @property
    def available(self) -> bool:
        return not self.needs_key or bool(api_key(self.needs_key))


PROVIDERS: Tuple[Provider, ...] = (
    Provider("serper", _fetch_serper, None, needs_key="serper"),
    Provider("brave", _fetch_brave, None, needs_key="brave"),
    Provider("duckduckgo", _fetch_duckduckgo, parse_duckduckgo),
    Provider("duckduckgo-lite", _fetch_ddg_lite, parse_ddg_lite),
    Provider("bing", _fetch_bing, parse_bing),
    Provider("mojeek", _fetch_mojeek, parse_mojeek),
)


@dataclass
class ProviderOutcome:
    """What one provider did with one query — the raw diagnostic record."""

    name: str
    status: str  # "ok" | "empty" | "blocked" | "error" | "skipped"
    rows: int = 0
    detail: str = ""

    def __str__(self) -> str:  # pragma: no cover - display helper
        base = "{}: {} ({} rows)".format(self.name, self.status, self.rows)
        return base + (" — " + self.detail if self.detail else "")


def search_detailed(
    query: str,
    session: Optional[requests.Session] = None,
    timeout: float = 15.0,
    pause: float = 1.0,
) -> Tuple[List[SearchResult], List[ProviderOutcome]]:
    """Run ``query`` and return both the results and per-provider outcomes.

    Providers are tried in order until one returns usable rows.  A provider
    answering 200 with a challenge page is recorded as ``blocked`` rather than
    ``empty``, so "we were refused" is never reported as "nothing matched".
    """
    session = session or requests.Session()
    outcomes: List[ProviderOutcome] = []
    attempted = 0

    for provider in PROVIDERS:
        if not provider.available:
            outcomes.append(
                ProviderOutcome(provider.name, "skipped", detail="no API key configured")
            )
            continue

        if attempted and pause:
            time.sleep(pause)
        attempted += 1

        try:
            payload = provider.fetch(session, query, timeout)
            if provider.parse is None:
                results = list(payload)
            elif looks_blocked(payload):
                outcomes.append(
                    ProviderOutcome(
                        provider.name,
                        "blocked",
                        detail="200 response was a challenge or empty page "
                               "({} bytes)".format(len(payload)),
                    )
                )
                continue
            else:
                results = provider.parse(payload)
        except Exception as exc:  # network, HTTP status, or malformed payload
            outcomes.append(
                ProviderOutcome(provider.name, "error", detail="{}".format(exc)[:300])
            )
            continue

        if results:
            outcomes.append(ProviderOutcome(provider.name, "ok", rows=len(results)))
            return results, outcomes

        outcomes.append(
            ProviderOutcome(
                provider.name, "empty", detail="responded normally with no results"
            )
        )

    if not any(o.status in ("ok", "empty") for o in outcomes):
        error = SearchError(
            "no search provider returned results -> "
            + "; ".join(str(o) for o in outcomes)
        )
        # Carry the per-provider records on the exception so a caller that
        # recovers from one failed category still keeps its diagnostics.
        error.outcomes = outcomes
        raise error
    return [], outcomes


def search(
    query: str,
    session: Optional[requests.Session] = None,
    timeout: float = 15.0,
    pause: float = 1.0,
) -> List[SearchResult]:
    """Run ``query`` against the configured providers and return result nodes."""
    return search_detailed(query, session=session, timeout=timeout, pause=pause)[0]
