"""Executive Contact Finder — discovery pipeline package."""

from .emails import EMAIL_PATTERNS, build_email, normalise_domain
from .parsing import parse_title
from .pipeline import COLUMNS, Contact, contacts_to_records, find_contacts
from .roles import CATEGORIES, ROLE_MATRIX, classify
from .search import SearchError, build_query

__version__ = "1.8.1"

__all__ = [
    "CATEGORIES",
    "COLUMNS",
    "Contact",
    "EMAIL_PATTERNS",
    "ROLE_MATRIX",
    "SearchError",
    "build_email",
    "build_query",
    "classify",
    "contacts_to_records",
    "find_contacts",
    "normalise_domain",
    "parse_title",
]
