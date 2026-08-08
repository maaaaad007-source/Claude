from executive_finder.emails import build_email, normalise_domain, sanitize_name


def test_sanitize_folds_accents_and_special_characters():
    assert sanitize_name("Daniel Ek") == ["daniel", "ek"]
    assert sanitize_name("Jörgen Åström") == ["jorgen", "astrom"]
    assert sanitize_name("Kjell-Åke Ødegård") == ["kjell", "ake", "odegard"]
    assert sanitize_name("Zoë O'Brien") == ["zoe", "obrien"]
    assert sanitize_name("  ") == []


def test_build_email_uses_first_last_by_default():
    assert build_email("Daniel Ek", "spotify.com") == "daniel.ek@spotify.com"


def test_build_email_patterns():
    assert build_email("Daniel Ek", "spotify.com", "flast") == "dek@spotify.com"
    assert build_email("Daniel Ek", "spotify.com", "f.last") == "d.ek@spotify.com"
    assert build_email("Daniel Ek", "spotify.com", "firstlast") == "danielek@spotify.com"
    assert build_email("Daniel Ek", "spotify.com", "last.first") == "ek.daniel@spotify.com"


def test_build_email_drops_middle_names_and_particles():
    assert build_email("Jan van der Berg", "volvocars.com") == "jan.berg@volvocars.com"
    assert build_email("Anna Maria Svensson", "ikea.com") == "anna.svensson@ikea.com"


def test_build_email_handles_single_token_and_missing_domain():
    assert build_email("Madonna", "example.com") == "madonna@example.com"
    assert build_email("Daniel Ek", "") is None
    assert build_email("", "spotify.com") is None


def test_normalise_domain_defaults_to_company_slug():
    assert normalise_domain("", "Spotify") == "spotify.com"
    assert normalise_domain("", "Volvo Cars") == "volvocars.com"
    assert normalise_domain("", "") == ""


def test_normalise_domain_strips_scheme_path_and_www():
    assert normalise_domain("https://www.volvocars.com/en/", "Volvo") == "volvocars.com"
    assert normalise_domain("  Spotify.COM  ", "Spotify") == "spotify.com"
    assert normalise_domain("careers@ikea.com", "IKEA") == "ikea.com"
    # Unusable input falls back to the company slug.
    assert normalise_domain("not-a-domain", "IKEA") == "ikea.com"
