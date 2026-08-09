"""Real email lookup: Hunter directory hits, finder fallback, pattern guesses."""

import pytest

from executive_finder import pipeline
from executive_finder.emails import build_email
from executive_finder.enrichment import (
    SOURCE_COMPANY_PATTERN,
    SOURCE_DIRECTORY,
    SOURCE_FINDER,
    SOURCE_GUESS,
    DomainProfile,
    EmailResult,
    HunterClient,
    HunterError,
    resolve_email,
)
from executive_finder.search import ProviderOutcome, SearchResult

DOMAIN_SEARCH_PAYLOAD = {
    "data": {
        "domain": "volvocars.com",
        "pattern": "{f}{last}",
        "emails": [
            {
                "value": "jrowan@volvocars.com",
                "first_name": "Jim",
                "last_name": "Rowan",
                "confidence": 97,
                "verification": {"status": "valid"},
            },
            {
                "value": "noname@volvocars.com",
                "first_name": None,
                "last_name": None,
                "confidence": 50,
            },
        ],
    }
}


class FakeResponse:
    def __init__(self, payload, ok=True, status_code=200, text=""):
        self._payload = payload
        self.ok = ok
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return self.response


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
def test_domain_search_reads_pattern_and_addresses():
    session = FakeSession(FakeResponse(DOMAIN_SEARCH_PAYLOAD))
    profile = HunterClient("k", session=session).domain_search("volvocars.com")

    assert profile.pattern == "{f}{last}"
    assert profile.total == 1  # the entry without a name is unusable
    hit = profile.lookup("Jim Rowan")
    assert hit.address == "jrowan@volvocars.com"
    assert hit.confidence == 97
    assert hit.status == "valid"
    assert hit.is_observed


def test_domain_search_lookup_is_name_normalised():
    session = FakeSession(FakeResponse(DOMAIN_SEARCH_PAYLOAD))
    profile = HunterClient("k", session=session).domain_search("volvocars.com")
    # Middle names and case must not break the match.
    assert profile.lookup("jim rowan") is not None
    assert profile.lookup("Jim Patrick Rowan") is not None
    assert profile.lookup("Someone Else") is None


def test_email_finder_returns_a_scored_address():
    payload = {"data": {"email": "a.berg@volvocars.com", "score": 88,
                        "verification": {"status": "accept_all"}}}
    session = FakeSession(FakeResponse(payload))
    found = HunterClient("k", session=session).email_finder(
        "volvocars.com", "Anna Berg"
    )
    assert found.address == "a.berg@volvocars.com"
    assert found.source == SOURCE_FINDER
    assert found.confidence == 88


def test_hunter_error_carries_the_api_message():
    session = FakeSession(
        FakeResponse({"errors": [{"details": "Your plan is out of requests"}]},
                     ok=False, status_code=429)
    )
    with pytest.raises(HunterError) as excinfo:
        HunterClient("k", session=session).domain_search("volvocars.com")
    assert "out of requests" in str(excinfo.value)
    assert "429" in str(excinfo.value)


def test_missing_key_is_an_error_not_a_silent_call():
    with pytest.raises(HunterError):
        HunterClient("").domain_search("volvocars.com")


def test_key_is_stripped_of_quotes():
    assert HunterClient(' "abc123" ').api_key == "abc123"


# --------------------------------------------------------------------------- #
# resolve_email precedence
# --------------------------------------------------------------------------- #
def _profile():
    return DomainProfile(
        domain="volvocars.com",
        pattern="{f}{last}",
        by_name={("jim", "rowan"): EmailResult("jrowan@volvocars.com",
                                               SOURCE_DIRECTORY, 97, "valid")},
    )


def test_directory_hit_wins():
    result = resolve_email("Jim Rowan", "volvocars.com", profile=_profile())
    assert result.address == "jrowan@volvocars.com"
    assert result.source == SOURCE_DIRECTORY


def test_company_pattern_used_when_person_is_unknown():
    """The key win: guesses follow the company's real pattern, not ours."""
    result = resolve_email("Anna Berg", "volvocars.com", profile=_profile())
    assert result.address == "aberg@volvocars.com"   # {f}{last}, not first.last
    assert result.source == SOURCE_COMPANY_PATTERN
    assert not result.is_observed


def test_default_pattern_when_hunter_is_unavailable():
    result = resolve_email("Anna Berg", "volvocars.com", profile=None)
    assert result.address == "anna.berg@volvocars.com"
    assert result.source == SOURCE_GUESS


def test_finder_is_consulted_only_when_enabled():
    calls = []

    class Client:
        def email_finder(self, domain, name):
            calls.append(name)
            return EmailResult("found@volvocars.com", SOURCE_FINDER, 80)

    off = resolve_email("Anna Berg", "volvocars.com", profile=_profile(),
                        client=Client(), use_finder=False)
    assert off.source == SOURCE_COMPANY_PATTERN and not calls

    on = resolve_email("Anna Berg", "volvocars.com", profile=_profile(),
                       client=Client(), use_finder=True)
    assert on.source == SOURCE_FINDER and calls == ["Anna Berg"]


def test_finder_failure_falls_back_to_a_pattern():
    class Client:
        def email_finder(self, domain, name):
            raise HunterError("rate limited")

    result = resolve_email("Anna Berg", "volvocars.com", profile=_profile(),
                           client=Client(), use_finder=True)
    assert result.source == SOURCE_COMPANY_PATTERN


def test_no_domain_yields_nothing():
    assert resolve_email("Jim Rowan", "") is None


def test_labels_distinguish_observed_from_guessed():
    assert "Verified" in EmailResult("a@b.com", SOURCE_DIRECTORY, 97, "valid").label()
    assert "Found" in EmailResult("a@b.com", SOURCE_FINDER, 80).label()
    assert "Guess" in EmailResult("a@b.com", SOURCE_COMPANY_PATTERN).label()
    assert "Guess" in EmailResult("a@b.com", SOURCE_GUESS).label()


# --------------------------------------------------------------------------- #
# Raw templates in build_email
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "template,expected",
    [
        ("{first}.{last}", "jim.rowan@volvocars.com"),
        ("{f}{last}", "jrowan@volvocars.com"),
        ("{first}{l}", "jimr@volvocars.com"),
        ("{last}", "rowan@volvocars.com"),
    ],
)
def test_build_email_accepts_a_raw_hunter_template(template, expected):
    assert build_email("Jim Rowan", "volvocars.com", template) == expected


def test_unknown_placeholder_falls_back_instead_of_crashing():
    assert build_email("Jim Rowan", "volvocars.com", "{middle}.{last}") == \
        "jim.rowan@volvocars.com"


# --------------------------------------------------------------------------- #
# Pipeline integration
# --------------------------------------------------------------------------- #
ROW = SearchResult(
    "Jim Rowan - Chief Executive Officer - Volvo Cars | LinkedIn",
    "https://se.linkedin.com/in/jim-rowan", "Volvo Cars · Gothenburg, Sweden")
ROW2 = SearchResult(
    "Anna Berg - Head of Design - Volvo Cars | LinkedIn",
    "https://se.linkedin.com/in/anna-berg", "Volvo Cars · Gothenburg, Sweden")


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(pipeline.time, "sleep", lambda *_: None)


def _stub_search(monkeypatch, rows):
    def fake(query, session=None, timeout=15.0, pause=1.0):
        return rows, [ProviderOutcome("stub", "ok", rows=len(rows))]
    monkeypatch.setattr(pipeline, "search_detailed", fake)


def test_pipeline_uses_hunter_addresses_and_reports_provenance(monkeypatch):
    _stub_search(monkeypatch, [ROW, ROW2])

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def domain_search(self, domain, limit=100):
            return _profile()

    monkeypatch.setattr(pipeline, "HunterClient", FakeClient)

    contacts, report = pipeline.find_contacts_detailed(
        "Volvo Cars", domain="volvocars.com", country="Sweden",
        categories=["CEO / Executive", "Design Director"],
        hunter_key="k", pause=0,
    )

    rows = {c.full_name: c for c in contacts}
    assert rows["Jim Rowan"].estimated_email == "jrowan@volvocars.com"
    assert "Verified" in rows["Jim Rowan"].email_source
    # Unknown person still benefits from the company's real pattern.
    assert rows["Anna Berg"].estimated_email == "aberg@volvocars.com"
    assert "company pattern" in rows["Anna Berg"].email_source

    assert report.emails_observed == 1
    assert report.emails_guessed == 1
    assert report.company_pattern == "{f}{last}"


def test_pipeline_without_a_hunter_key_labels_everything_a_guess(monkeypatch):
    _stub_search(monkeypatch, [ROW])

    contacts, report = pipeline.find_contacts_detailed(
        "Volvo Cars", domain="volvocars.com", country="Sweden",
        categories=["CEO / Executive"], pause=0,
    )
    assert contacts[0].estimated_email == "jim.rowan@volvocars.com"
    assert "Guess" in contacts[0].email_source
    assert report.emails_observed == 0
    assert "No Hunter API key" in report.enrichment_note


def test_hunter_failure_degrades_to_guesses(monkeypatch):
    _stub_search(monkeypatch, [ROW])

    class FailingClient:
        def __init__(self, *a, **k):
            pass

        def domain_search(self, domain, limit=100):
            raise HunterError("Hunter HTTP 401: invalid key")

    monkeypatch.setattr(pipeline, "HunterClient", FailingClient)

    contacts, report = pipeline.find_contacts_detailed(
        "Volvo Cars", domain="volvocars.com", country="Sweden",
        categories=["CEO / Executive"], hunter_key="bad", pause=0,
    )
    assert contacts[0].estimated_email == "jim.rowan@volvocars.com"
    assert "401" in report.enrichment_note
