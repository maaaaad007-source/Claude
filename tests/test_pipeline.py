import pytest

from executive_finder import pipeline
from executive_finder.search import ProviderOutcome, SearchError, SearchResult

CEO_RESULT = SearchResult(
    title="Daniel Ek - Chief Executive Officer - Spotify | LinkedIn",
    url="https://se.linkedin.com/in/daniel-ek",
    snippet="Spotify · Stockholm, Sweden",
)
DESIGN_RESULT = SearchResult(
    title="Anna Svensson - Head of Design - Spotify | LinkedIn",
    url="https://www.linkedin.com/in/anna-svensson?trk=x",
    snippet="Spotify · Stockholm",
)
COMPANY_PAGE = SearchResult(
    title="Spotify | LinkedIn", url="https://www.linkedin.com/company/spotify"
)
UNRELATED = SearchResult(
    title="Erik Larsson - Software Engineer - Klarna | LinkedIn",
    url="https://www.linkedin.com/in/erik-larsson",
    snippet="Klarna · Stockholm",
)


def _stub_search(results_by_call):
    calls = {"queries": []}

    def fake_search(query, session=None, timeout=15.0, pause=1.0):
        calls["queries"].append(query)
        outcome = ProviderOutcome("stub", "ok", rows=len(results_by_call))
        return results_by_call, [outcome]

    return fake_search, calls


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(pipeline.time, "sleep", lambda *_: None)


def test_find_contacts_builds_the_six_column_matrix(monkeypatch):
    fake_search, calls = _stub_search([CEO_RESULT, DESIGN_RESULT, COMPANY_PAGE, UNRELATED])
    monkeypatch.setattr(pipeline, "search_detailed", fake_search)

    contacts = pipeline.find_contacts(
        "Spotify", domain="spotify.com", country="Sweden",
        categories=["CEO / Executive"], pause=0,
    )

    rows = pipeline.contacts_to_records(contacts)
    # Rows are grouped by the role matrix order, so the CEO sorts above design.
    assert [row["Full Name"] for row in rows] == ["Daniel Ek", "Anna Svensson"]
    assert list(rows[0]) == pipeline.COLUMNS

    ceo = next(row for row in rows if row["Full Name"] == "Daniel Ek")
    assert ceo["Designation"] == "Chief Executive Officer at Spotify"
    assert ceo["Email"] == "daniel.ek@spotify.com"
    assert ceo["Country"] == "Sweden"
    assert ceo["Category"] == "CEO / Executive"
    assert ceo["LinkedIn Profile"] == "https://www.linkedin.com/in/daniel-ek"

    # Company pages and off-target roles are filtered out.
    assert all(row["Full Name"] != "Erik Larsson" for row in rows)
    assert len(calls["queries"]) == 1


def test_designation_is_classified_independently_of_the_query_category(monkeypatch):
    fake_search, _ = _stub_search([DESIGN_RESULT])
    monkeypatch.setattr(pipeline, "search_detailed", fake_search)

    contacts = pipeline.find_contacts(
        "Spotify", categories=["CEO / Executive"], pause=0
    )
    assert contacts[0].category == "Design Director"


def test_results_are_deduplicated_across_categories(monkeypatch):
    fake_search, calls = _stub_search([CEO_RESULT])
    monkeypatch.setattr(pipeline, "search_detailed", fake_search)

    contacts = pipeline.find_contacts(
        "Spotify", categories=["CEO / Executive", "Design Director"], pause=0
    )
    assert len(calls["queries"]) == 2
    assert len(contacts) == 1


def test_domain_defaults_to_company_slug(monkeypatch):
    fake_search, _ = _stub_search([CEO_RESULT])
    monkeypatch.setattr(pipeline, "search_detailed", fake_search)

    contacts = pipeline.find_contacts("Spotify", categories=["CEO / Executive"], pause=0)
    assert contacts[0].estimated_email == "daniel.ek@spotify.com"


def test_max_per_category_is_respected(monkeypatch):
    fake_search, _ = _stub_search([CEO_RESULT, DESIGN_RESULT])
    monkeypatch.setattr(pipeline, "search_detailed", fake_search)

    contacts = pipeline.find_contacts(
        "Spotify", categories=["CEO / Executive"], max_per_category=1, pause=0
    )
    assert len(contacts) == 1


def test_require_company_filters_unmatched_rows(monkeypatch):
    stray = SearchResult(
        title="Nils Berg - Managing Director - Northvolt | LinkedIn",
        url="https://www.linkedin.com/in/nils-berg",
        snippet="Northvolt · Stockholm",
    )
    fake_search, _ = _stub_search([CEO_RESULT, stray])
    monkeypatch.setattr(pipeline, "search_detailed", fake_search)

    contacts = pipeline.find_contacts(
        "Spotify", categories=["CEO / Executive"], require_company=True, pause=0
    )
    assert [c.full_name for c in contacts] == ["Daniel Ek"]


def test_progress_hook_is_called(monkeypatch):
    fake_search, _ = _stub_search([])
    monkeypatch.setattr(pipeline, "search_detailed", fake_search)

    seen = []
    pipeline.find_contacts(
        "Spotify", categories=["CEO / Executive"], pause=0,
        progress=lambda message, fraction: seen.append((message, fraction)),
    )
    assert seen[0][1] == 0.0
    assert seen[-1] == ("Done", 1.0)


def test_provider_failure_surfaces_when_nothing_was_found(monkeypatch):
    def failing_search(*_args, **_kwargs):
        raise SearchError("all search providers failed")

    monkeypatch.setattr(pipeline, "search_detailed", failing_search)
    with pytest.raises(SearchError):
        pipeline.find_contacts("Spotify", categories=["CEO / Executive"], pause=0)


def test_company_is_required():
    with pytest.raises(ValueError):
        pipeline.find_contacts("   ")
