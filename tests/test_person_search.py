"""Searching for a named person rather than sweeping roles."""

import pytest

from executive_finder import pipeline
from executive_finder.pipeline import matches_person
from executive_finder.search import ProviderOutcome, SearchResult


# --------------------------------------------------------------------------- #
# Name matching
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "wanted,found,expected",
    [
        ("Daniel Ek", "Daniel Ek", True),
        ("daniel ek", "Daniel Ek", True),                  # case
        ("Daniel Ek", "Daniel P. Ek", True),               # middle initial
        ("Daniel Ek", "Daniel Patrick Ek", True),          # middle name
        ("Jorgen Astrom", "Jörgen Åström", True),          # accents folded
        ("Ek", "Daniel Ek", True),                         # surname only
        ("Daniel Ek", "Daniel Berg", False),               # wrong surname
        ("Daniel Ek", "Anna Ek", False),                   # wrong first name
        ("Daniel Ek", "Ek", False),                        # missing a token
        ("", "Daniel Ek", False),                          # nothing asked for
        ("Daniel Ek", "", False),
    ],
)
def test_matches_person(wanted, found, expected):
    assert matches_person(wanted, found) is expected


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #
TARGET = SearchResult(
    "Daniel Ek - Chief Executive Officer - Spotify | LinkedIn",
    "https://se.linkedin.com/in/daniel-ek", "Spotify · Stockholm, Sweden")
NAMESAKE = SearchResult(
    "Anna Ek - Head of Design - Spotify | LinkedIn",
    "https://se.linkedin.com/in/anna-ek", "Spotify · Stockholm, Sweden")
UNTITLED = SearchResult(
    "Daniel Ek - Barista - Local Cafe | LinkedIn",
    "https://se.linkedin.com/in/daniel-ek-2", "Local Cafe · Stockholm, Sweden")
GERMAN = SearchResult(
    "Daniel Ek - Analyst - Spotify | LinkedIn",
    "https://de.linkedin.com/in/daniel-ek-de", "Spotify · Berlin, Germany")


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(pipeline.time, "sleep", lambda *_: None)


def _stub(monkeypatch, rows, capture=None):
    def fake(query, session=None, timeout=15.0, pause=1.0):
        if query.startswith('"@'):
            return [], [ProviderOutcome("stub", "empty")]
        if capture is not None:
            capture.append(query)
        return rows, [ProviderOutcome("stub", "ok", rows=len(rows))]
    monkeypatch.setattr(pipeline, "search_detailed", fake)


def test_a_named_search_runs_one_query_for_the_person(monkeypatch):
    """The role sweep is replaced, not added to — five queries become one."""
    queries = []
    _stub(monkeypatch, [TARGET], queries)

    contacts, _ = pipeline.find_contacts_detailed(
        "Spotify", domain="spotify.com", country="Sweden",
        person="Daniel Ek", pause=0,
    )
    assert queries == ['site:linkedin.com/in/ "Spotify" "Daniel Ek" "Sweden"']
    assert [c.full_name for c in contacts] == ["Daniel Ek"]


def test_other_people_are_dropped(monkeypatch):
    _stub(monkeypatch, [TARGET, NAMESAKE])

    contacts, report = pipeline.find_contacts_detailed(
        "Spotify", domain="spotify.com", person="Daniel Ek", pause=0,
    )
    assert [c.full_name for c in contacts] == ["Daniel Ek"]
    assert report.dropped_wrong_person == 1


def test_the_person_is_kept_whatever_their_title(monkeypatch):
    """A named search must not be gated on the role matrix."""
    _stub(monkeypatch, [UNTITLED])

    contacts, _ = pipeline.find_contacts_detailed(
        "Spotify", domain="spotify.com", person="Daniel Ek", pause=0,
    )
    assert [c.full_name for c in contacts] == ["Daniel Ek"]
    assert contacts[0].category == "Named search"


def test_a_recognisable_title_is_still_classified(monkeypatch):
    _stub(monkeypatch, [TARGET])

    contacts, _ = pipeline.find_contacts_detailed(
        "Spotify", domain="spotify.com", person="Daniel Ek", pause=0,
    )
    assert contacts[0].category == "CEO / Executive"
    assert contacts[0].designation == "Chief Executive Officer at Spotify"


def test_the_country_filter_still_applies_to_a_named_search(monkeypatch):
    _stub(monkeypatch, [TARGET, GERMAN])

    contacts, report = pipeline.find_contacts_detailed(
        "Spotify", domain="spotify.com", country="Sweden",
        person="Daniel Ek", country_filter="strict", pause=0,
    )
    assert [c.linkedin_profile for c in contacts] == [
        "https://www.linkedin.com/in/daniel-ek"
    ]
    assert report.dropped_wrong_country == 1


def test_the_email_is_resolved_for_a_named_search(monkeypatch):
    _stub(monkeypatch, [TARGET])

    contacts, _ = pipeline.find_contacts_detailed(
        "Spotify", domain="spotify.com", person="Daniel Ek", pause=0,
    )
    assert contacts[0].estimated_email == "daniel.ek@spotify.com"


def test_roles_are_ignored_while_a_person_is_given(monkeypatch):
    queries = []
    _stub(monkeypatch, [TARGET], queries)

    pipeline.find_contacts_detailed(
        "Spotify", domain="spotify.com", person="Daniel Ek",
        categories=["CEO / Executive", "Design Director", "UX Director"],
        pause=0,
    )
    assert len(queries) == 1
    assert "Daniel Ek" in queries[0]
    assert "Design Director" not in queries[0]


def test_an_empty_person_leaves_the_role_sweep_untouched(monkeypatch):
    queries = []
    _stub(monkeypatch, [TARGET], queries)

    pipeline.find_contacts_detailed(
        "Spotify", domain="spotify.com", person="   ",
        categories=["CEO / Executive", "Design Director"], pause=0,
    )
    assert len(queries) == 2
    assert all("Daniel Ek" not in q for q in queries)


def test_a_person_search_needs_no_roles(monkeypatch):
    """Clearing every role must not block a search that names someone."""
    _stub(monkeypatch, [TARGET])

    contacts, _ = pipeline.find_contacts_detailed(
        "Spotify", domain="spotify.com", person="Daniel Ek",
        categories=[], pause=0,
    )
    assert [c.full_name for c in contacts] == ["Daniel Ek"]
