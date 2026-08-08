"""Targeted X-Ray querying against public LinkedIn profile indexes.

The module builds ``site:linkedin.com/in/`` queries, issues them against HTML
search endpoints with desktop browser headers, and unpacks the raw result nodes
into ``SearchResult`` records with tracking redirects stripped.
"""

from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence
from urllib.parse import parse_qs, unquote, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

__all__ = [
    "SearchResult",
    "SearchError",
    "build_query",
    "canonical_linkedin_url",
    "is_linkedin_profile",
    "parse_duckduckgo",
    "parse_bing",
    "search",
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
BING_ENDPOINT = "https://www.bing.com/search"

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


PROVIDERS = (
    ("duckduckgo", _fetch_duckduckgo, parse_duckduckgo),
    ("bing", _fetch_bing, parse_bing),
)


def search(
    query: str,
    session: Optional[requests.Session] = None,
    timeout: float = 15.0,
    pause: float = 1.0,
) -> List[SearchResult]:
    """Run ``query`` against the configured providers and return result nodes.

    Providers are tried in order until one returns usable rows; a short pause
    between provider attempts keeps request rates polite.  Raises
    :class:`SearchError` only when every provider raised a transport error.
    """
    session = session or requests.Session()
    errors: List[str] = []

    for index, (name, fetch, parse) in enumerate(PROVIDERS):
        if index and pause:
            time.sleep(pause)
        try:
            results = parse(fetch(session, query, timeout))
        except Exception as exc:  # network, HTTP status, or malformed markup
            errors.append("{}: {}".format(name, exc))
            continue
        if results:
            return results

    if errors and len(errors) == len(PROVIDERS):
        raise SearchError("all search providers failed -> " + "; ".join(errors))
    return []
