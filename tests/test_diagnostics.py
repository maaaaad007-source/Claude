"""Tests for block detection, provider fallback and run diagnostics."""

import pytest

from executive_finder import pipeline, search as search_mod
from executive_finder.pipeline import SearchReport, split_company_input
from executive_finder.search import (
    Provider,
    ProviderOutcome,
    SearchError,
    SearchResult,
    looks_blocked,
    parse_ddg_lite,
    parse_mojeek,
    search_detailed,
)

REAL_SERP = "<html>" + ("<div class='result'>padding</div>" * 200) + "</html>"


# --------------------------------------------------------------------------- #
# Block detection — the bug behind "No executive profiles matched"
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "html",
    [
        "",
        "<html><body>short</body></html>",
        "<html>" + "x" * 4000 + "Please verify you are human" + "</html>",
        "<html>" + "x" * 4000 + "Our systems have detected unusual traffic" + "</html>",
        "<html>" + "x" * 4000 + "<div class='challenge-platform'></div></html>",
    ],
)
def test_looks_blocked_flags_challenge_and_stub_pages(html):
    assert looks_blocked(html)


def test_looks_blocked_passes_a_real_serp():
    assert not looks_blocked(REAL_SERP)


def test_blocked_provider_is_reported_as_blocked_not_empty(monkeypatch):
    """A 200 challenge page must not masquerade as a genuine zero-result search."""
    blocked = Provider(
        "blocked-one", lambda s, q, t: "<html>captcha</html>", lambda html: []
    )
    monkeypatch.setattr(search_mod, "PROVIDERS", (blocked,))

    with pytest.raises(SearchError) as excinfo:
        search_detailed("anything", pause=0)
    assert "blocked" in str(excinfo.value)


def test_genuinely_empty_serp_returns_no_error(monkeypatch):
    empty = Provider("empty-one", lambda s, q, t: REAL_SERP, lambda html: [])
    monkeypatch.setattr(search_mod, "PROVIDERS", (empty,))

    results, outcomes = search_detailed("anything", pause=0)
    assert results == []
    assert [o.status for o in outcomes] == ["empty"]


def test_search_falls_through_to_the_next_provider(monkeypatch):
    hit = SearchResult("Daniel Ek - CEO - Spotify | LinkedIn",
                       "https://www.linkedin.com/in/daniel-ek")

    def boom(session, query, timeout):
        raise OSError("connection reset")

    providers = (
        Provider("dead", boom, lambda html: []),
        Provider("blocked", lambda s, q, t: "<html>captcha</html>", lambda h: []),
        Provider("good", lambda s, q, t: REAL_SERP, lambda html: [hit]),
    )
    monkeypatch.setattr(search_mod, "PROVIDERS", providers)

    results, outcomes = search_detailed("anything", pause=0)
    assert results == [hit]
    assert [o.status for o in outcomes] == ["error", "blocked", "ok"]


def test_keyed_providers_are_skipped_without_credentials(monkeypatch):
    monkeypatch.setattr(search_mod, "_API_KEYS", {})
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    keyed = Provider("serper", lambda s, q, t: [], None, needs_key="serper")
    assert not keyed.available


def test_configure_api_keys_enables_a_provider(monkeypatch):
    monkeypatch.setattr(search_mod, "_API_KEYS", {})
    search_mod.configure_api_keys(serper="abc123")
    keyed = Provider("serper", lambda s, q, t: [], None, needs_key="serper")
    assert keyed.available
    assert search_mod.api_key("serper") == "abc123"


# --------------------------------------------------------------------------- #
# Company field holding a domain
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "typed,expected",
    [
        ("Spotify.com", ("Spotify", "spotify.com")),
        ("volvocars.com", ("volvocars", "volvocars.com")),
        ("https://www.ikea.com/", ("ikea", "ikea.com")),
        ("Spotify", ("Spotify", "")),
        ("Volvo Cars", ("Volvo Cars", "")),
    ],
)
def test_split_company_input_recovers_a_name_from_a_domain(typed, expected):
    assert split_company_input(typed) == expected


def test_split_company_input_keeps_an_explicit_domain():
    assert split_company_input("Spotify.com", "spotify.se") == ("Spotify", "spotify.se")


def test_pipeline_recovers_from_a_domain_in_the_company_field(monkeypatch):
    captured = {}

    def fake_search(query, session=None, timeout=15.0, pause=1.0):
        captured["query"] = query
        return [
            SearchResult(
                "Daniel Ek - Chief Executive Officer - Spotify | LinkedIn",
                "https://se.linkedin.com/in/daniel-ek",
                "Spotify",
            )
        ], [ProviderOutcome("stub", "ok", rows=1)]

    monkeypatch.setattr(pipeline, "search_detailed", fake_search)
    monkeypatch.setattr(pipeline.time, "sleep", lambda *_: None)

    contacts = pipeline.find_contacts(
        "Spotify.com", categories=["CEO / Executive"], pause=0
    )
    # The query searches the company, not the domain string.
    assert '"Spotify"' in captured["query"]
    assert "Spotify.com" not in captured["query"]
    assert contacts[0].estimated_email == "daniel.ek@spotify.com"


# --------------------------------------------------------------------------- #
# Run report
# --------------------------------------------------------------------------- #
def test_report_counts_every_drop_reason(monkeypatch):
    rows = [
        SearchResult("Daniel Ek - CEO - Spotify | LinkedIn",
                     "https://se.linkedin.com/in/daniel-ek", "Spotify"),
        SearchResult("Spotify | LinkedIn",
                     "https://www.linkedin.com/company/spotify"),          # not_profile
        SearchResult("Top 10 CEOs | LinkedIn",
                     "https://www.linkedin.com/in/listicle"),              # unparsed
        SearchResult("Erik Larsson - Software Engineer - Klarna | LinkedIn",
                     "https://www.linkedin.com/in/erik-larsson", "Klarna"),  # off_target
        SearchResult("Daniel Ek - CEO - Spotify | LinkedIn",
                     "https://www.linkedin.com/in/daniel-ek", "Spotify"),  # duplicate
    ]

    def fake_search(query, session=None, timeout=15.0, pause=1.0):
        return rows, [ProviderOutcome("stub", "ok", rows=len(rows))]

    monkeypatch.setattr(pipeline, "search_detailed", fake_search)
    monkeypatch.setattr(pipeline.time, "sleep", lambda *_: None)

    contacts, report = pipeline.find_contacts_detailed(
        "Spotify", categories=["CEO / Executive"], pause=0
    )

    assert len(contacts) == 1
    assert report.raw_results == 5
    assert report.dropped_not_profile == 1
    assert report.dropped_unparsed_title == 1
    assert report.dropped_off_target == 1
    assert report.dropped_duplicate == 1
    assert report.kept == 1
    assert report.queries and report.usable_provider
    assert not report.blocked


def test_report_flags_a_blocked_run():
    report = SearchReport(outcomes=[ProviderOutcome("bing", "blocked")])
    assert report.blocked
    assert not report.usable_provider


def test_report_does_not_flag_blocked_when_rows_came_back():
    report = SearchReport(
        raw_results=4, outcomes=[ProviderOutcome("bing", "blocked"),
                                 ProviderOutcome("mojeek", "ok", rows=4)]
    )
    assert not report.blocked


# --------------------------------------------------------------------------- #
# Additional SERP parsers
# --------------------------------------------------------------------------- #
DDG_LITE_HTML = """
<table>
  <tr><td><a class="result-link" href="https://se.linkedin.com/in/daniel-ek">
    Daniel Ek - CEO - Spotify | LinkedIn</a></td></tr>
  <tr><td class="result-snippet">Chief Executive Officer at Spotify</td></tr>
</table>
"""

MOJEEK_HTML = """
<ul class="results-standard">
  <li><a class="title" href="https://se.linkedin.com/in/daniel-ek">
    Daniel Ek - CEO - Spotify | LinkedIn</a><p class="s">Spotify, Stockholm</p></li>
</ul>
"""


def test_parse_ddg_lite():
    results = parse_ddg_lite(DDG_LITE_HTML)
    assert len(results) == 1
    assert results[0].url == "https://se.linkedin.com/in/daniel-ek"
    assert results[0].snippet == "Chief Executive Officer at Spotify"


def test_parse_mojeek():
    results = parse_mojeek(MOJEEK_HTML)
    assert len(results) == 1
    assert results[0].title == "Daniel Ek - CEO - Spotify | LinkedIn"
    assert results[0].snippet == "Spotify, Stockholm"


def test_search_error_carries_provider_outcomes(monkeypatch):
    blocked = Provider("b1", lambda s, q, t: "<html>captcha</html>", lambda h: [])
    monkeypatch.setattr(search_mod, "PROVIDERS", (blocked,))

    with pytest.raises(SearchError) as excinfo:
        search_detailed("anything", pause=0)
    outcomes = getattr(excinfo.value, "outcomes", [])
    assert [o.name for o in outcomes] == ["b1"]
    assert outcomes[0].status == "blocked"


def test_keyed_provider_is_attempted_before_scrapers(monkeypatch):
    order = []

    def record(name):
        def fetch(session, query, timeout):
            order.append(name)
            raise OSError("offline")
        return fetch

    monkeypatch.setattr(search_mod, "_API_KEYS", {"serper": "k"})
    providers = (
        Provider("serper", record("serper"), None, needs_key="serper"),
        Provider("brave", record("brave"), None, needs_key="brave"),
        Provider("duckduckgo", record("duckduckgo"), lambda h: []),
    )
    monkeypatch.setattr(search_mod, "PROVIDERS", providers)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)

    with pytest.raises(SearchError):
        search_detailed("anything", pause=0)
    # Brave has no key, so it is skipped without a network attempt.
    assert order == ["serper", "duckduckgo"]


# --------------------------------------------------------------------------- #
# Bing base64 redirects — the cause of "dropped N non-profile"
# --------------------------------------------------------------------------- #
import base64  # noqa: E402

from executive_finder.search import is_linkedin_profile, unwrap_redirect  # noqa: E402


def _bing_ck(target: str, prefix: str = "a1") -> str:
    encoded = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    return "https://www.bing.com/ck/a?!&&p=deadbeef&u={}{}&ntb=1".format(prefix, encoded)


@pytest.mark.parametrize("prefix", ["a1", "a2"])
def test_bing_redirect_is_decoded(prefix):
    target = "https://se.linkedin.com/in/jim-rowan"
    assert unwrap_redirect(_bing_ck(target, prefix)) == target


def test_decoded_bing_redirect_is_recognised_as_a_profile():
    """The exact failure seen in production: every row dropped as non-profile."""
    wrapped = _bing_ck("https://se.linkedin.com/in/jim-rowan")
    assert not is_linkedin_profile(wrapped)
    assert is_linkedin_profile(unwrap_redirect(wrapped))


def test_bing_redirect_with_undecodable_payload_is_left_alone():
    href = "https://www.bing.com/ck/a?u=a1!!!not-base64!!!"
    assert unwrap_redirect(href) == href


def test_bing_direct_urls_still_pass_through():
    direct = "https://se.linkedin.com/in/jim-rowan"
    assert unwrap_redirect(direct) == direct


def test_bing_serp_with_redirects_yields_usable_profiles():
    from executive_finder.search import parse_bing

    html = """
    <li class="b_algo"><h2><a href="{}">
      Jim Rowan - Chief Executive Officer - Volvo Cars | LinkedIn</a></h2>
      <p>Volvo Cars, Gothenburg, Sweden</p></li>
    """.format(_bing_ck("https://se.linkedin.com/in/jim-rowan"))

    results = parse_bing(html)
    assert results[0].url == "https://se.linkedin.com/in/jim-rowan"
    assert is_linkedin_profile(results[0].url)


# --------------------------------------------------------------------------- #
# API error reporting and key hygiene
# --------------------------------------------------------------------------- #
def test_api_error_includes_the_response_body(monkeypatch):
    class FakeResponse:
        ok = False
        status_code = 400
        text = '{"message":"Not enough credits"}'

    monkeypatch.setattr(search_mod, "_API_KEYS", {"serper": "k"})
    monkeypatch.setattr(
        search_mod.requests.Session, "post", lambda *a, **k: FakeResponse()
    )

    with pytest.raises(SearchError) as excinfo:
        search_mod._fetch_serper(search_mod.requests.Session(), "q", 5.0)
    assert "Not enough credits" in str(excinfo.value)
    assert "400" in str(excinfo.value)


@pytest.mark.parametrize(
    "pasted",
    ['"abc123"', "'abc123'", "“abc123”", "  abc123  ", "‘abc123’"],
)
def test_pasted_keys_are_stripped_of_quotes(monkeypatch, pasted):
    monkeypatch.setattr(search_mod, "_API_KEYS", {})
    search_mod.configure_api_keys(serper=pasted)
    assert search_mod.api_key("serper") == "abc123"
