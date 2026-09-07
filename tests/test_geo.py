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
    """Answer the first X-Ray query with ``rows``, later ones empty.

    With a country set each category is searched twice (its LinkedIn locale,
    then the global host to top up); handing both calls the same rows would
    double every drop counter here without testing anything.
    """
    calls = []

    def fake(query, session=None, timeout=15.0, pause=1.0):
        calls.append(query)
        if len(calls) > 1:
            return [], [ProviderOutcome("stub", "empty")]
        return rows, [ProviderOutcome("stub", "ok", rows=len(rows))]
    monkeypatch.setattr(pipeline, "search_detailed", fake)


def test_strict_country_filter_keeps_only_the_target_market(monkeypatch):
    _stub(monkeypatch, [SWEDISH, GERMAN, AMERICAN])

    contacts, report = pipeline.find_contacts_detailed(
        "Volvo", domain="volvocars.com", country="Sweden",
        categories=["CEO / Executive"], country_filter="strict", pause=0,
    )
    assert [c.full_name for c in contacts] == ["Jim Rowan"]
    assert report.dropped_country == 2
    # Klaus is on de.linkedin.com — proof he is elsewhere. Pat is on the
    # generic host and names a US city, which is only evidence against
    # Sweden in strict mode.
    assert report.dropped_wrong_country == 1
    assert report.dropped_country_unknown == 1


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


# --------------------------------------------------------------------------- #
# Country suggestions offered by the UI
# --------------------------------------------------------------------------- #
def test_every_suggestion_is_one_the_filter_can_verify():
    """The list exists so a picked country always gets real filtering."""
    from executive_finder.geo import SUPPORTED_COUNTRIES

    assert SUPPORTED_COUNTRIES
    unverifiable = [c for c in SUPPORTED_COUNTRIES if not known_country(c)]
    assert unverifiable == []


def test_suggestions_are_sorted_and_display_cased():
    from executive_finder.geo import SUPPORTED_COUNTRIES

    assert SUPPORTED_COUNTRIES == sorted(SUPPORTED_COUNTRIES)
    assert "United Kingdom" in SUPPORTED_COUNTRIES
    assert "United Arab Emirates" in SUPPORTED_COUNTRIES
    assert "New Zealand" in SUPPORTED_COUNTRIES
    # No lowercase keys leaking through from the internal table.
    assert all(c == c.title() for c in SUPPORTED_COUNTRIES)


def test_suggestions_have_no_duplicates():
    from executive_finder.geo import SUPPORTED_COUNTRIES

    assert len(SUPPORTED_COUNTRIES) == len(set(SUPPORTED_COUNTRIES))


def test_a_picked_suggestion_filters_end_to_end(monkeypatch):
    """Picking a suggestion must actually engage the filter, not just look tidy."""
    from executive_finder.geo import SUPPORTED_COUNTRIES

    _stub(monkeypatch, [SWEDISH, GERMAN])
    for picked in ("Sweden", "United Kingdom", "United States"):
        assert picked in SUPPORTED_COUNTRIES

    contacts, report = pipeline.find_contacts_detailed(
        "Volvo", domain="volvocars.com", country="Sweden",
        categories=["CEO / Executive"], country_filter="strict", pause=0,
    )
    assert [c.full_name for c in contacts] == ["Jim Rowan"]
    assert report.dropped_wrong_country == 1


def test_a_typed_country_outside_the_list_still_runs(monkeypatch):
    _stub(monkeypatch, [SWEDISH, GERMAN])
    contacts, _ = pipeline.find_contacts_detailed(
        "Volvo", country="Atlantis", categories=["CEO / Executive"],
        country_filter="strict", pause=0,
    )
    assert len(contacts) == 2


def test_locale_hint_names_the_host_that_proves_a_country():
    from executive_finder.geo import locale_hint

    assert locale_hint("Netherlands") == "nl.linkedin.com"
    assert locale_hint("United Kingdom") in ("uk.linkedin.com", "gb.linkedin.com")
    assert locale_hint("Atlantis") == ""
    assert locale_hint("") == ""


def test_every_supported_country_has_a_locale_hint():
    """The hint appears in user-facing advice; a blank one would read as a bug."""
    from executive_finder.geo import SUPPORTED_COUNTRIES, locale_hint

    assert all(locale_hint(name) for name in SUPPORTED_COUNTRIES)


# --------------------------------------------------------------------------- #
# Country-scoped search strategy
# --------------------------------------------------------------------------- #
DUTCH = SearchResult(
    "Jan de Vries - Chief Executive Officer - Volvo | LinkedIn",
    "https://nl.linkedin.com/in/jan-de-vries", "Volvo · Amsterdam")


def _recording_stub(monkeypatch, by_query):
    """Answer each X-Ray query from ``by_query``, recording the order they arrive.

    Only ``site:`` queries are recorded — the email-pattern discovery pass
    issues its own ``"@domain"`` search through the same function, and counting
    it would make every assertion about request count wrong by one.
    """
    seen = []

    def fake(query, session=None, timeout=15.0, pause=1.0):
        if not query.startswith("site:"):
            return [], [ProviderOutcome("stub", "empty")]
        seen.append(query)
        rows = by_query(query)
        return rows, [ProviderOutcome("stub", "ok" if rows else "empty",
                                      rows=len(rows))]

    monkeypatch.setattr(pipeline, "search_detailed", fake)
    return seen


def test_the_country_search_asks_that_countrys_linkedin_first(monkeypatch):
    seen = _recording_stub(monkeypatch, lambda q: [DUTCH])

    contacts, _ = pipeline.find_contacts_detailed(
        "Volvo", country="Netherlands", categories=["CEO / Executive"],
        country_filter="strict", pause=0,
    )
    assert seen[0] == ('site:nl.linkedin.com/in/ "Volvo" ("CEO" OR '
                       '"Chief Executive Officer" OR "Managing Director" OR '
                       '"President" OR "Country Head")')
    # The same person came back from both queries; de-duplication keeps one.
    assert [c.full_name for c in contacts] == ["Jan de Vries"]


def test_an_empty_locale_corpus_falls_back_to_the_global_host(monkeypatch):
    """Not every profile is indexed under its country's host; don't give up."""
    seen = _recording_stub(
        monkeypatch, lambda q: [] if "nl.linkedin.com" in q else [DUTCH])

    contacts, _ = pipeline.find_contacts_detailed(
        "Volvo", country="Netherlands", categories=["CEO / Executive"],
        country_filter="strict", pause=0,
    )
    assert len(seen) == 2
    assert "nl.linkedin.com" in seen[0]
    assert seen[1].startswith("site:linkedin.com/in/")
    assert '"Netherlands"' in seen[1]
    assert [c.full_name for c in contacts] == ["Jan de Vries"]


def test_the_global_query_tops_up_a_thin_locale_result(monkeypatch):
    """The locale corpus is partial, so a few hits from it are not enough."""
    other = SearchResult(
        "Sanne Bakker - Managing Director - Volvo | LinkedIn",
        "https://nl.linkedin.com/in/sanne-bakker", "Volvo · Rotterdam")
    seen = _recording_stub(
        monkeypatch, lambda q: [DUTCH] if "nl.linkedin.com" in q else [other])

    contacts, _ = pipeline.find_contacts_detailed(
        "Volvo", country="Netherlands", categories=["CEO / Executive"],
        country_filter="strict", pause=0,
    )
    assert len(seen) == 2
    assert sorted(c.full_name for c in contacts) == ["Jan de Vries", "Sanne Bakker"]


def test_no_second_query_is_spent_once_the_quota_is_full(monkeypatch):
    """Topping up is worth a request; searching past the cap is not."""
    seen = _recording_stub(monkeypatch, lambda q: [DUTCH])

    pipeline.find_contacts_detailed(
        "Volvo", country="Netherlands", categories=["CEO / Executive"],
        country_filter="strict", pause=0, max_per_category=1,
    )
    assert len(seen) == 1


def test_a_scoped_query_that_returns_only_junk_still_falls_back(monkeypatch):
    """Rows that all get filtered out are as good as none — keep looking."""
    junk = SearchResult("Top 10 CEOs to watch", "https://example.com/list", "")
    seen = _recording_stub(
        monkeypatch, lambda q: [junk] if "nl.linkedin.com" in q else [DUTCH])

    contacts, _ = pipeline.find_contacts_detailed(
        "Volvo", country="Netherlands", categories=["CEO / Executive"],
        country_filter="strict", pause=0,
    )
    assert len(seen) == 2
    assert [c.full_name for c in contacts] == ["Jan de Vries"]


def test_no_country_means_no_scoping_and_no_fallback(monkeypatch):
    seen = _recording_stub(monkeypatch, lambda q: [SWEDISH])

    pipeline.find_contacts_detailed(
        "Volvo", categories=["CEO / Executive"], pause=0,
    )
    assert len(seen) == 1
    assert seen[0].startswith("site:linkedin.com/in/")


def test_an_unrecognised_country_is_not_scoped(monkeypatch):
    """There is no atlantis.linkedin.com to search."""
    seen = _recording_stub(monkeypatch, lambda q: [SWEDISH])

    pipeline.find_contacts_detailed(
        "Volvo", country="Atlantis", categories=["CEO / Executive"], pause=0,
    )
    assert len(seen) == 1
    assert seen[0].startswith("site:linkedin.com/in/")
    assert '"Atlantis"' in seen[0]


def test_the_provider_region_bias_follows_the_country(monkeypatch):
    from executive_finder.search import region_code

    _recording_stub(monkeypatch, lambda q: [DUTCH])
    pipeline.find_contacts_detailed(
        "Volvo", country="Netherlands", categories=["CEO / Executive"], pause=0,
    )
    assert region_code() == "nl"

    pipeline.find_contacts_detailed(
        "Volvo", categories=["CEO / Executive"], pause=0,
    )
    assert region_code() == ""
