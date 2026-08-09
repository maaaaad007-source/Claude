"""Roles typed by the user, outside the built-in five."""

import pytest

from executive_finder import pipeline, roles
from executive_finder.search import ProviderOutcome, SearchResult


# --------------------------------------------------------------------------- #
# Keyword expansion
# --------------------------------------------------------------------------- #
def test_builtin_category_expands_to_its_whole_keyword_set():
    assert roles.keywords_for("UX Director") == roles.ROLE_MATRIX["UX Director"]


def test_custom_role_stands_as_its_own_keyword():
    assert roles.keywords_for("Chief Technology Officer") == \
        ["Chief Technology Officer"]
    assert roles.keywords_for("  CTO  ") == ["CTO"]


def test_blank_role_expands_to_nothing():
    assert roles.keywords_for("") == []
    assert roles.keywords_for("   ") == []


def test_is_custom():
    assert not roles.is_custom("CEO / Executive")
    assert roles.is_custom("Head of Marketing")
    assert not roles.is_custom("")


# --------------------------------------------------------------------------- #
# Free-text role matching
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "role,text,expected",
    [
        ("CTO", "CTO at Spotify", True),
        ("cto", "CTO at Spotify", True),                      # case-insensitive
        ("Head of Marketing", "Head of Marketing, Nordics", True),
        ("Head of Marketing", "Head-of-Marketing", True),     # separator tolerant
        ("CTO", "Director of Octopus care", False),           # not a word match
        ("CTO", "", False),
        ("", "CTO at Spotify", False),
    ],
)
def test_mentions_role(role, text, expected):
    assert roles.mentions_role(role, text) is expected


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #
CTO = SearchResult(
    "Gustav Söderström - Chief Technology Officer - Spotify | LinkedIn",
    "https://se.linkedin.com/in/gustav", "Spotify · Stockholm, Sweden")
MARKETING = SearchResult(
    "Lena Holm - Head of Marketing - Spotify | LinkedIn",
    "https://se.linkedin.com/in/lena-holm", "Spotify · Stockholm")
UNRELATED = SearchResult(
    "Nils Berg - Barista - Local Cafe | LinkedIn",
    "https://se.linkedin.com/in/nils-berg", "Local Cafe · Malmö")


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(pipeline.time, "sleep", lambda *_: None)


def _stub(monkeypatch, rows, capture=None):
    def fake(query, session=None, timeout=15.0, pause=1.0):
        if capture is not None and not query.startswith('"@'):
            capture.append(query)
        if query.startswith('"@'):
            return [], [ProviderOutcome("stub", "empty")]
        return rows, [ProviderOutcome("stub", "ok", rows=len(rows))]
    monkeypatch.setattr(pipeline, "search_detailed", fake)


def test_a_typed_role_is_searched_as_itself(monkeypatch):
    queries = []
    _stub(monkeypatch, [CTO], queries)

    contacts, _ = pipeline.find_contacts_detailed(
        "Spotify", domain="spotify.com", country="Sweden",
        categories=["Head of Marketing"], pause=0,
    )
    assert queries == ['site:linkedin.com/in/ "Spotify" "Head of Marketing" "Sweden"']
    # The CTO row still survives because the snippet names the company.
    assert contacts and contacts[0].full_name == "Gustav Söderström"


def test_typed_role_becomes_the_category(monkeypatch):
    _stub(monkeypatch, [MARKETING])

    contacts, _ = pipeline.find_contacts_detailed(
        "Spotify", domain="spotify.com", categories=["Head of Marketing"], pause=0,
    )
    assert contacts[0].category == "Head of Marketing"
    assert contacts[0].designation == "Head of Marketing at Spotify"


def test_typed_role_is_kept_on_role_evidence_without_the_company(monkeypatch):
    """A role the matrix cannot classify is kept when the role text matches."""
    stray = SearchResult(
        "Ada Lin - Head of Marketing - Acme | LinkedIn",
        "https://uk.linkedin.com/in/ada-lin", "Acme Ltd · London")
    _stub(monkeypatch, [stray])

    contacts, _ = pipeline.find_contacts_detailed(
        "Spotify", domain="spotify.com", categories=["Head of Marketing"],
        pause=0,
    )
    assert [c.full_name for c in contacts] == ["Ada Lin"]


def test_irrelevant_rows_are_still_dropped_for_a_typed_role(monkeypatch):
    _stub(monkeypatch, [UNRELATED])

    contacts, report = pipeline.find_contacts_detailed(
        "Spotify", domain="spotify.com", categories=["Head of Marketing"],
        pause=0,
    )
    assert contacts == []
    assert report.dropped_off_target == 1


def test_builtin_and_typed_roles_mix(monkeypatch):
    queries = []
    _stub(monkeypatch, [CTO, MARKETING], queries)

    contacts, _ = pipeline.find_contacts_detailed(
        "Spotify", domain="spotify.com",
        categories=["CEO / Executive", "Head of Marketing"], pause=0,
    )
    assert len(queries) == 2
    # The built-in category still ORs its full keyword set.
    assert '"CEO" OR "Chief Executive Officer"' in queries[0]
    assert queries[1].endswith('"Head of Marketing"')
    assert {c.full_name for c in contacts} == {"Gustav Söderström", "Lena Holm"}


def test_builtin_classification_still_wins_over_the_query_role(monkeypatch):
    """A recognisable title is classified by the matrix, not by the query."""
    _stub(monkeypatch, [SearchResult(
        "Daniel Ek - Chief Executive Officer - Spotify | LinkedIn",
        "https://se.linkedin.com/in/daniel-ek", "Spotify")])

    contacts, _ = pipeline.find_contacts_detailed(
        "Spotify", domain="spotify.com", categories=["Head of Marketing"], pause=0,
    )
    assert contacts[0].category == "CEO / Executive"


def test_whitespace_only_roles_are_rejected():
    with pytest.raises(ValueError):
        pipeline.find_contacts_detailed("Spotify", categories=["  ", ""], pause=0)
