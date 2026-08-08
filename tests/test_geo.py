"""Country filtering: locale subdomain, country names and city evidence."""

import pytest

from executive_finder import pipeline
from executive_finder.geo import country_match, known_country, locale_of
from executive_finder.search import ProviderOutcome, SearchResult


# --------------------------------------------------------------------------- #
# Locale extraction
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://se.linkedin.com/in/x", "se"),
        ("https://uk.linkedin.com/in/x", "uk"),
        ("https://www.linkedin.com/in/x", "www"),
        ("https://linkedin.com/in/x", ""),
        ("", ""),
    ],
)
def test_locale_of(url, expected):
    assert locale_of(url) == expected


def test_known_country_accepts_aliases():
    assert known_country("Sweden")
    assert known_country("UK")
    assert known_country("united states of america")
    assert not known_country("Atlantis")
    assert not known_country("")


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #
def test_matching_locale_is_accepted():
    verdict = country_match("Sweden", "https://se.linkedin.com/in/x", "")
    assert verdict.matches and "locale" in verdict.reason


def test_foreign_locale_is_rejected_even_in_relaxed_mode():
    """A profile on another country's LinkedIn locale is a positive mismatch."""
    for strict in (True, False):
        verdict = country_match(
            "Sweden", "https://de.linkedin.com/in/x", "Berlin", strict=strict
        )
        assert not verdict.matches


def test_country_named_in_text_is_accepted():
    verdict = country_match("Sweden", "https://www.linkedin.com/in/x",
                            "Volvo Cars · Gothenburg, Sweden")
    assert verdict.matches and "names" in verdict.reason


def test_endonym_is_accepted():
    assert country_match("Germany", "https://www.linkedin.com/in/x",
                         "Siemens · Deutschland").matches


def test_city_evidence_is_accepted():
    verdict = country_match("Sweden", "https://www.linkedin.com/in/x",
                            "Volvo Cars · Göteborg")
    assert verdict.matches and "city" in verdict.reason


def test_accent_folded_city_matches():
    assert country_match("Sweden", "https://www.linkedin.com/in/x",
                         "Volvo · Goteborg").matches


def test_strict_mode_rejects_absent_evidence():
    verdict = country_match("Sweden", "https://www.linkedin.com/in/x",
                            "Volvo Cars", strict=True)
    assert not verdict.matches and verdict.reason == "no country evidence"


def test_relaxed_mode_keeps_unevidenced_rows():
    assert country_match("Sweden", "https://www.linkedin.com/in/x",
                         "Volvo Cars", strict=False).matches


def test_uk_aliases_share_a_locale():
    for name in ("United Kingdom", "UK", "England", "Scotland"):
        assert country_match(name, "https://uk.linkedin.com/in/x", "").matches


def test_unknown_country_never_filters():
    """Filtering on a country we hold no data for would drop everything."""
    verdict = country_match("Atlantis", "https://se.linkedin.com/in/x", "")
    assert verdict.matches and "not recognised" in verdict.reason


def test_no_country_never_filters():
    assert country_match("", "https://de.linkedin.com/in/x", "").matches


# --------------------------------------------------------------------------- #
# Pipeline integration — the reported "pulls everyone" behaviour
# --------------------------------------------------------------------------- #
SWEDISH = SearchResult(
    "Jim Rowan - Chief Executive Officer - Volvo Cars | LinkedIn",
    "https://se.linkedin.com/in/jim-rowan", "Volvo Cars · Gothenburg, Sweden")
GERMAN = SearchResult(
    "Klaus Bauer - Managing Director - Volvo | LinkedIn",
    "https://de.linkedin.com/in/klaus-bauer", "Volvo · Munich, Germany")
AMERICAN = SearchResult(
    "Pat Miller - President - Volvo | LinkedIn",
    "https://www.linkedin.com/in/pat-miller", "Volvo · Greensboro, North Carolina")


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(pipeline.time, "sleep", lambda *_: None)


def _stub(monkeypatch, rows):
    def fake(query, session=None, timeout=15.0, pause=1.0):
        return rows, [ProviderOutcome("stub", "ok", rows=len(rows))]
    monkeypatch.setattr(pipeline, "search_detailed", fake)


def test_strict_country_filter_keeps_only_the_target_market(monkeypatch):
    _stub(monkeypatch, [SWEDISH, GERMAN, AMERICAN])

    contacts, report = pipeline.find_contacts_detailed(
        "Volvo", domain="volvocars.com", country="Sweden",
        categories=["CEO / Executive"], country_filter="strict", pause=0,
    )
    assert [c.full_name for c in contacts] == ["Jim Rowan"]
    assert report.dropped_wrong_country == 2


def test_country_filter_off_keeps_everyone(monkeypatch):
    _stub(monkeypatch, [SWEDISH, GERMAN, AMERICAN])

    contacts, report = pipeline.find_contacts_detailed(
        "Volvo", domain="volvocars.com", country="Sweden",
        categories=["CEO / Executive"], country_filter="off", pause=0,
    )
    assert len(contacts) == 3
    assert report.dropped_wrong_country == 0


def test_relaxed_filter_drops_only_foreign_locales(monkeypatch):
    _stub(monkeypatch, [SWEDISH, GERMAN, AMERICAN])

    contacts, _ = pipeline.find_contacts_detailed(
        "Volvo", domain="volvocars.com", country="Sweden",
        categories=["CEO / Executive"], country_filter="relaxed", pause=0,
    )
    # The German locale is a contradiction; the US row carries no locale signal.
    names = [c.full_name for c in contacts]
    assert "Jim Rowan" in names and "Pat Miller" in names
    assert "Klaus Bauer" not in names


def test_filter_disengages_for_an_unrecognised_country(monkeypatch):
    _stub(monkeypatch, [SWEDISH, GERMAN])

    contacts, _ = pipeline.find_contacts_detailed(
        "Volvo", country="Atlantis", categories=["CEO / Executive"],
        country_filter="strict", pause=0,
    )
    assert len(contacts) == 2


def test_no_country_means_no_filtering(monkeypatch):
    _stub(monkeypatch, [SWEDISH, GERMAN, AMERICAN])

    contacts, _ = pipeline.find_contacts_detailed(
        "Volvo", categories=["CEO / Executive"], country_filter="strict", pause=0,
    )
    assert len(contacts) == 3
