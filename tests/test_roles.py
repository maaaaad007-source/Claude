import pytest

from executive_finder.roles import CATEGORIES, ROLE_MATRIX, classify, matches_any_role


def test_role_matrix_covers_the_five_target_categories():
    assert CATEGORIES == [
        "CEO / Executive",
        "Design Director",
        "UX Director",
        "Product Design Director",
        "Head of HR / People",
    ]
    assert all(ROLE_MATRIX[category] for category in CATEGORIES)


@pytest.mark.parametrize(
    "designation,expected",
    [
        ("Chief Executive Officer at Spotify", "CEO / Executive"),
        ("Managing Director, Volvo Cars Sweden", "CEO / Executive"),
        ("Country Head - IKEA UK", "CEO / Executive"),
        ("Head of Design at IKEA", "Design Director"),
        ("VP of Design", "Design Director"),
        ("Head of UX at Klarna", "UX Director"),
        ("Director of User Experience", "UX Director"),
        ("Head of Product Design at Spotify", "Product Design Director"),
        ("Chief People Officer", "Head of HR / People"),
        ("HR Director, Nordics", "Head of HR / People"),
    ],
)
def test_classify(designation, expected):
    assert classify(designation) == expected


def test_longest_keyword_wins_over_shorter_overlap():
    assert classify("Head of Product Design") == "Product Design Director"
    assert classify("Head of Design") == "Design Director"


def test_classify_rejects_unrelated_titles():
    assert classify("Software Engineer at Spotify") is None
    assert classify("") is None
    assert not matches_any_role("Barista")
