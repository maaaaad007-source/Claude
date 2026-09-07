import pytest

from executive_finder.roles import ROLE_MATRIX
from executive_finder.search import (
    SearchResult,
    build_query,
    canonical_linkedin_url,
    is_linkedin_profile,
    parse_bing,
    parse_duckduckgo,
    unwrap_redirect,
)


def test_build_query_shape():
    query = build_query("Spotify", ["CEO"], "Sweden")
    assert query == 'site:linkedin.com/in/ "Spotify" "CEO" "Sweden"'


def test_build_query_groups_keywords_with_or():
    query = build_query("Volvo", ROLE_MATRIX["UX Director"], "")
    assert query.startswith('site:linkedin.com/in/ "Volvo" (')
    assert '"UX Director" OR "Head of UX"' in query
    assert query.endswith(")")


def test_build_query_requires_company_and_keywords():
    with pytest.raises(ValueError):
        build_query("", ["CEO"])
    with pytest.raises(ValueError):
        build_query("Spotify", [])


def test_unwrap_redirect_strips_tracking_wrappers():
    ddg = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fse.linkedin.com%2Fin%2Fdaniel-ek&rut=x"
    assert unwrap_redirect(ddg) == "https://se.linkedin.com/in/daniel-ek"

    google = "https://www.google.com/url?q=https%3A%2F%2Fwww.linkedin.com%2Fin%2Fjim"
    assert unwrap_redirect(google) == "https://www.linkedin.com/in/jim"

    direct = "https://www.linkedin.com/in/daniel-ek"
    assert unwrap_redirect(direct) == direct
    assert unwrap_redirect("") == ""


def test_is_linkedin_profile():
    assert is_linkedin_profile("https://www.linkedin.com/in/daniel-ek")
    assert is_linkedin_profile("https://se.linkedin.com/in/daniel-ek/")
    assert not is_linkedin_profile("https://www.linkedin.com/company/spotify")
    assert not is_linkedin_profile("https://example.com/in/daniel-ek")
    assert not is_linkedin_profile("https://linkedin.com.evil.com/in/x")
    assert not is_linkedin_profile("")


def test_canonical_linkedin_url_normalises_locale_and_query():
    assert (
        canonical_linkedin_url("https://se.linkedin.com/in/daniel-ek/?trk=abc")
        == "https://www.linkedin.com/in/daniel-ek"
    )


DDG_HTML = """
<div class="result results_links">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fse.linkedin.com%2Fin%2Fdaniel-ek">
    Daniel Ek - CEO - Spotify | LinkedIn
  </a>
  <a class="result__snippet">Spotify, Stockholm, Sweden</a>
</div>
<div class="result results_links">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.linkedin.com%2Fcompany%2Fspotify">
    Spotify | LinkedIn
  </a>
</div>
"""

BING_HTML = """
<li class="b_algo">
  <h2><a href="https://se.linkedin.com/in/daniel-ek">Daniel Ek - CEO - Spotify | LinkedIn</a></h2>
  <p>Chief Executive Officer at Spotify</p>
</li>
<li class="b_algo"><h2>no anchor here</h2></li>
"""


def test_parse_duckduckgo_unpacks_nodes():
    results = parse_duckduckgo(DDG_HTML)
    assert len(results) == 2
    assert results[0].title == "Daniel Ek - CEO - Spotify | LinkedIn"
    assert results[0].url == "https://se.linkedin.com/in/daniel-ek"
    assert results[0].snippet == "Spotify, Stockholm, Sweden"


def test_parse_bing_unpacks_nodes_and_skips_broken_ones():
    results = parse_bing(BING_HTML)
    assert len(results) == 1
    assert results[0].url == "https://se.linkedin.com/in/daniel-ek"
    assert results[0].snippet == "Chief Executive Officer at Spotify"


def test_parsers_tolerate_empty_html():
    assert parse_duckduckgo("") == []
    assert parse_bing("<html><body>blocked</body></html>") == []


# --------------------------------------------------------------------------- #
# Country-scoped querying
# --------------------------------------------------------------------------- #
def test_a_scoped_query_puts_the_country_in_the_host_not_the_terms():
    from executive_finder.search import build_query

    assert build_query("Arrise", ["CEO"], "Netherlands", country_scoped=True) == (
        'site:nl.linkedin.com/in/ "Arrise" "CEO"'
    )


def test_an_unscoped_query_is_unchanged():
    from executive_finder.search import build_query

    assert build_query("Arrise", ["CEO"], "Netherlands") == (
        'site:linkedin.com/in/ "Arrise" "CEO" "Netherlands"'
    )


def test_scoping_an_unrecognised_country_keeps_it_as_a_search_term():
    """Silently dropping it would search the whole world and say nothing."""
    from executive_finder.search import build_query

    query = build_query("Arrise", ["CEO"], "Atlantis", country_scoped=True)
    assert query == 'site:linkedin.com/in/ "Arrise" "CEO" "Atlantis"'


def test_the_uk_scopes_to_uk_not_gb():
    """LinkedIn serves British profiles from uk.linkedin.com; gb is a synonym."""
    from executive_finder.search import linkedin_site

    assert linkedin_site("United Kingdom") == "uk.linkedin.com/in/"
    assert linkedin_site("United States") == "us.linkedin.com/in/"
    assert linkedin_site("") == "linkedin.com/in/"
    assert linkedin_site("Atlantis") == "linkedin.com/in/"


def test_a_scoped_result_passes_the_country_filter_by_construction():
    """The point of scoping: the host is the evidence the filter looks for."""
    from executive_finder.geo import country_match
    from executive_finder.search import linkedin_site

    host = linkedin_site("Netherlands").split("/")[0]
    verdict = country_match(
        "Netherlands", "https://{}/in/jan-de-vries".format(host), "Arrise")
    assert verdict.matches


def test_the_region_bias_is_set_and_cleared():
    from executive_finder.search import configure_region, region_code

    configure_region("Netherlands")
    assert region_code() == "nl"
    configure_region("Atlantis")
    assert region_code() == ""
    configure_region("United States")
    assert region_code() == "us"
    configure_region("")
    assert region_code() == ""


def test_serper_sends_the_region_and_omits_it_when_unset(monkeypatch):
    from executive_finder import search as search_mod
    from executive_finder.search import SERPER_RESULTS

    sent = []

    class FakeResponse:
        ok = True
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"organic": []}

    class FakeSession:
        def post(self, url, json=None, headers=None, timeout=None):
            sent.append(json)
            return FakeResponse()

    monkeypatch.setattr(search_mod, "api_key", lambda name: "key")
    search_mod.configure_region("Netherlands")
    search_mod._fetch_serper(FakeSession(), "q", 5.0)
    search_mod.configure_region("")
    search_mod._fetch_serper(FakeSession(), "q", 5.0)

    assert sent[0] == {"q": "q", "num": SERPER_RESULTS, "gl": "nl"}
    assert sent[1] == {"q": "q", "num": SERPER_RESULTS}


# --------------------------------------------------------------------------- #
# Providers that silently drop the site: filter
# --------------------------------------------------------------------------- #
JUNK = [
    SearchResult("Court Case Finder", "https://courtcasefinder.com/", ""),
    SearchResult("UniCourt", "https://unicourt.com/courts/federal", ""),
    SearchResult("RecordsFinder", "https://recordsfinder.com/court/", ""),
]
PROFILE = SearchResult(
    "Jim Rowan - CEO - Volvo | LinkedIn",
    "https://se.linkedin.com/in/jim-rowan", "Volvo")

XRAY = 'site:se.linkedin.com/in/ "Volvo" "CEO"'


def _providers(monkeypatch, *responses):
    """Install one fake provider per response, in order."""
    from executive_finder import search as search_mod

    made = []
    for index, rows in enumerate(responses):
        def fetch(session, query, timeout, rows=rows):
            return list(rows)
        made.append(search_mod.Provider("p{}".format(index), fetch, None))
    monkeypatch.setattr(search_mod, "PROVIDERS", tuple(made))


def test_off_site_padding_does_not_win_the_provider_chain(monkeypatch):
    """Junk from one provider must not stop us asking the next."""
    from executive_finder.search import search_detailed

    _providers(monkeypatch, JUNK, [PROFILE])
    results, outcomes = search_detailed(XRAY, pause=0)

    assert results == [PROFILE]
    assert [(o.name, o.status) for o in outcomes] == [("p0", "ignored"), ("p1", "ok")]


def test_off_site_padding_is_reported_as_ignored_not_empty(monkeypatch):
    """'Ignored' and 'empty' look identical in the counts but are not."""
    from executive_finder.search import search_detailed

    _providers(monkeypatch, JUNK)
    results, outcomes = search_detailed(XRAY, pause=0)

    assert results == []
    assert outcomes[0].status == "ignored"
    assert "none on linkedin.com" in outcomes[0].detail


def test_a_provider_ignoring_the_filter_is_not_reported_as_a_refusal(monkeypatch):
    """Every provider padding means nothing matched, not that we were blocked."""
    from executive_finder.search import search_detailed

    _providers(monkeypatch, JUNK, JUNK)
    results, outcomes = search_detailed(XRAY, pause=0)  # must not raise

    assert results == []
    assert all(o.status == "ignored" for o in outcomes)


def test_a_partial_page_of_profiles_is_still_accepted(monkeypatch):
    """One real profile among padding is a result; only all-junk is ignored."""
    from executive_finder.search import search_detailed

    _providers(monkeypatch, JUNK + [PROFILE])
    results, outcomes = search_detailed(XRAY, pause=0)

    assert PROFILE in results
    assert outcomes[0].status == "ok"


def test_a_non_profile_query_is_never_judged_on_linkedin_urls(monkeypatch):
    """Pattern discovery searches for "@domain" and must keep off-site rows."""
    from executive_finder.search import search_detailed

    _providers(monkeypatch, JUNK)
    results, outcomes = search_detailed('"@volvocars.com"', pause=0)

    assert results == JUNK
    assert outcomes[0].status == "ok"


def test_every_provider_asks_for_more_than_a_single_page():
    """Serper defaults to ten results, and ten was the app's real ceiling.

    Five roles at ten rows each gave fifty candidates for a whole search,
    from which the profile, role and country filters each took a cut — so a
    company with real staff in a country routinely came back empty. A Serper
    call costs one credit whatever num is, so a small ask is pure loss.
    """
    from executive_finder.search import (
        BING_RESULTS, BRAVE_RESULTS, SERPER_RESULTS,
    )

    assert SERPER_RESULTS == 100          # Serper's documented maximum
    assert BRAVE_RESULTS == 20            # Brave's documented maximum
    assert int(BING_RESULTS) >= 50
