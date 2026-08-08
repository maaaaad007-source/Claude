import pytest

from executive_finder.roles import ROLE_MATRIX
from executive_finder.search import (
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
