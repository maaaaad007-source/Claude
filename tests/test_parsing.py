import pytest

from executive_finder.parsing import is_plausible_name, parse_title


@pytest.mark.parametrize(
    "title,expected_name,expected_designation",
    [
        (
            "Daniel Ek - Chief Executive Officer at Spotify - Spotify | LinkedIn",
            "Daniel Ek",
            "Chief Executive Officer at Spotify",
        ),
        (
            "Daniel Ek – CEO – Spotify | LinkedIn",
            "Daniel Ek",
            "CEO at Spotify",
        ),
        (
            "Jim Rowan - Chief Executive Officer - Volvo Cars - LinkedIn",
            "Jim Rowan",
            "Chief Executive Officer at Volvo Cars",
        ),
        (
            "Anna Svensson, MBA | Head of Design | IKEA | LinkedIn",
            "Anna Svensson",
            "Head of Design at IKEA",
        ),
    ],
)
def test_parse_title_variants(title, expected_name, expected_designation):
    parsed = parse_title(title, company="Spotify")
    assert parsed is not None
    assert parsed.full_name == expected_name
    assert parsed.designation == expected_designation


def test_parse_title_falls_back_to_company_when_employer_absent():
    parsed = parse_title("Daniel Ek - CEO | LinkedIn", company="Spotify")
    assert parsed.designation == "CEO at Spotify"


def test_parse_title_strips_feed_suffix():
    parsed = parse_title("Daniel Ek on LinkedIn: excited to share our results")
    assert parsed is not None
    assert parsed.full_name == "Daniel Ek"


@pytest.mark.parametrize(
    "title",
    [
        "",
        "Top 10 CEOs in Sweden | LinkedIn",
        "Spotify | LinkedIn",
        "Jobs at Volvo Cars | LinkedIn",
        "Chief Executive Officer - Spotify | LinkedIn",
        "IKEA Employees, Location, Careers | LinkedIn",
    ],
)
def test_parse_title_rejects_non_profile_headers(title):
    assert parse_title(title, company="Spotify") is None


def test_is_plausible_name():
    assert is_plausible_name("Daniel Ek")
    assert is_plausible_name("Kjell-Åke Öberg")
    assert not is_plausible_name("Daniel")          # single token
    assert not is_plausible_name("Head of Design")  # reads as a job title
    assert not is_plausible_name("Top 50 Leaders")  # contains digits
    assert not is_plausible_name("a b c d e f")     # too many tokens
