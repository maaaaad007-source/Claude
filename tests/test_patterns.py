"""Free email-pattern discovery from publicly published addresses."""

import pytest

from executive_finder import pipeline
from executive_finder.patterns import (
    discover_pattern,
    extract_addresses,
    infer_pattern,
)
from executive_finder.search import ProviderOutcome, SearchResult


# --------------------------------------------------------------------------- #
# Address harvesting
# --------------------------------------------------------------------------- #
def test_extract_addresses_finds_every_address_for_the_domain():
    text = ("Contact jim.rowan@volvocars.com or press@volvocars.com — "
            "not someone@example.com")
    assert extract_addresses(text, "volvocars.com") == ["jim.rowan", "press"]


def test_extract_addresses_is_case_insensitive_and_deduplicates():
    text = "Jim.Rowan@VolvoCars.com and jim.rowan@volvocars.com"
    assert extract_addresses(text, "volvocars.com") == ["jim.rowan"]


def test_extract_addresses_handles_empty_input():
    assert extract_addresses("", "volvocars.com") == []
    assert extract_addresses("text", "") == []


# --------------------------------------------------------------------------- #
# Inference
# --------------------------------------------------------------------------- #
def test_role_accounts_are_ignored():
    found = infer_pattern(["info", "press", "careers", "no-reply"])
    assert not found.usable
    assert found.addresses_found == 0


def test_suffixed_role_accounts_are_ignored():
    assert not infer_pattern(["info-uk", "press.se", "jobs_de"]).usable


def test_structure_distinguishes_first_last_from_f_last():
    assert infer_pattern(["jim.rowan", "anna.berg"]).template == "{first}.{last}"
    assert infer_pattern(["j.rowan", "a.berg"]).template == "{f}.{last}"


def test_underscore_and_hyphen_shapes():
    assert infer_pattern(["jim_rowan", "anna_berg"]).template == "{first}_{last}"
    assert infer_pattern(["jim-rowan", "anna-berg"]).template == "{first}-{last}"


def test_known_names_resolve_the_ambiguous_concatenated_shapes():
    """Structure alone cannot tell 'jrowan' from 'jimr' — a known name can."""
    found = infer_pattern(["jrowan", "aberg"], known_names=["Jim Rowan", "Anna Berg"])
    assert found.template == "{f}{last}"
    assert "matched a known executive" in found.evidence
    assert found.confidence >= 75


def test_name_evidence_outranks_structure():
    # 'jim.rowan' looks like {first}.{last} structurally, and a known name
    # confirms it rather than merely suggesting it.
    found = infer_pattern(["jim.rowan"], known_names=["Jim Rowan"])
    assert found.template == "{first}.{last}"
    assert "matched a known executive" in found.evidence


def test_firstlast_is_distinguished_from_flast_by_name():
    found = infer_pattern(["jimrowan"], known_names=["Jim Rowan"])
    assert found.template == "{first}{last}"


def test_single_structural_sample_is_accepted_but_low_confidence():
    found = infer_pattern(["jim.rowan"])
    assert found.template == "{first}.{last}"
    assert found.confidence < 80


def test_unusable_addresses_report_honestly():
    found = infer_pattern(["x1y2z3"])
    assert not found.usable
    assert found.addresses_found == 1
    assert "none revealed a usable shape" in found.evidence


def test_no_addresses_at_all():
    found = infer_pattern([])
    assert not found.usable
    assert "No pattern" in found.describe()


def test_majority_wins_across_mixed_samples():
    found = infer_pattern(["jim.rowan", "anna.berg", "k.lund"])
    assert found.template == "{first}.{last}"


# --------------------------------------------------------------------------- #
# End-to-end discovery
# --------------------------------------------------------------------------- #
def _search_returning(snippets):
    def fake(query, session=None, pause=0):
        return (
            [SearchResult("Contact us", "https://volvocars.com/contact", s)
             for s in snippets],
            [ProviderOutcome("stub", "ok", rows=len(snippets))],
        )
    return fake


def test_discover_pattern_mines_addresses_from_search_results():
    search = _search_returning([
        "Media enquiries: press@volvocars.com",
        "Reach Jim at jrowan@volvocars.com for interviews",
        "Design lead aberg@volvocars.com",
    ])
    found = discover_pattern(
        "volvocars.com", search, known_names=["Jim Rowan", "Anna Berg"]
    )
    assert found.template == "{f}{last}"
    assert found.addresses_found == 2      # press@ excluded
    assert "volvocars.com" not in found.describe() or found.usable


def test_discover_pattern_survives_a_failing_search():
    def boom(query, session=None, pause=0):
        raise RuntimeError("providers down")

    found = discover_pattern("volvocars.com", boom)
    assert not found.usable
    assert found.addresses_found == 0


def test_discover_pattern_needs_a_domain():
    assert not discover_pattern("", _search_returning([])).usable


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


def test_discovered_pattern_is_applied_without_any_hunter_key(monkeypatch):
    """The whole point: a free pattern beats an assumed one for every row."""
    def fake(query, session=None, timeout=15.0, pause=1.0):
        if query.startswith('"@'):
            return (
                [SearchResult("Press", "https://volvocars.com/press",
                              "jrowan@volvocars.com and aberg@volvocars.com")],
                [ProviderOutcome("stub", "ok", rows=1)],
            )
        return [ROW, ROW2], [ProviderOutcome("stub", "ok", rows=2)]

    monkeypatch.setattr(pipeline, "search_detailed", fake)

    contacts, report = pipeline.find_contacts_detailed(
        "Volvo Cars", domain="volvocars.com", country="Sweden",
        categories=["CEO / Executive", "Design Director"], pause=0,
    )

    rows = {c.full_name: c for c in contacts}
    assert rows["Jim Rowan"].estimated_email == "jrowan@volvocars.com"
    assert rows["Anna Berg"].estimated_email == "aberg@volvocars.com"
    for contact in contacts:
        assert "inferred from public addresses" in contact.email_source
    assert report.discovered_pattern == "{f}{last}"


def test_default_pattern_used_when_discovery_finds_nothing(monkeypatch):
    def fake(query, session=None, timeout=15.0, pause=1.0):
        if query.startswith('"@'):
            return [], [ProviderOutcome("stub", "empty")]
        return [ROW], [ProviderOutcome("stub", "ok", rows=1)]

    monkeypatch.setattr(pipeline, "search_detailed", fake)

    contacts, report = pipeline.find_contacts_detailed(
        "Volvo Cars", domain="volvocars.com", country="Sweden",
        categories=["CEO / Executive"], pause=0,
    )
    assert contacts[0].estimated_email == "jim.rowan@volvocars.com"
    assert "default pattern" in contacts[0].email_source
    assert report.discovered_pattern == ""


def test_hunter_pattern_outranks_a_discovered_one(monkeypatch):
    from executive_finder.enrichment import DomainProfile

    def fake(query, session=None, timeout=15.0, pause=1.0):
        if query.startswith('"@'):
            return (
                [SearchResult("Press", "https://x/p", "jim.rowan@volvocars.com")],
                [ProviderOutcome("stub", "ok", rows=1)],
            )
        return [ROW], [ProviderOutcome("stub", "ok", rows=1)]

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def domain_search(self, domain, limit=100):
            return DomainProfile(domain=domain, pattern="{f}{last}")

    monkeypatch.setattr(pipeline, "search_detailed", fake)
    monkeypatch.setattr(pipeline, "HunterClient", FakeClient)

    contacts, _ = pipeline.find_contacts_detailed(
        "Volvo Cars", domain="volvocars.com", country="Sweden",
        categories=["CEO / Executive"], hunter_key="k", pause=0,
    )
    # Hunter's observed pattern wins over the inferred one.
    assert contacts[0].estimated_email == "jrowan@volvocars.com"
    assert "company's own pattern" in contacts[0].email_source


def test_discovery_can_be_switched_off(monkeypatch):
    calls = []

    def fake(query, session=None, timeout=15.0, pause=1.0):
        calls.append(query)
        return [ROW], [ProviderOutcome("stub", "ok", rows=1)]

    monkeypatch.setattr(pipeline, "search_detailed", fake)

    pipeline.find_contacts_detailed(
        "Volvo Cars", domain="volvocars.com", categories=["CEO / Executive"],
        pause=0, discover_patterns=False,
    )
    assert not any(q.startswith('"@') for q in calls)
