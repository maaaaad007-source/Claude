"""Targeted role matrix and departmental classification.

The five decision-maker categories the application targets, together with the
keyword sets used both to build search queries and to classify a designation
string that came back from a search result.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

# Ordered mapping of category -> search keywords.  Order matters: it is the
# order categories are searched in and the order rows are grouped in the output.
ROLE_MATRIX: Dict[str, List[str]] = {
    "CEO / Executive": [
        "CEO",
        "Chief Executive Officer",
        "Managing Director",
        "President",
        "Country Head",
    ],
    "Design Director": [
        "Design Director",
        "Head of Design",
        "VP of Design",
        "Vice President Design",
    ],
    "UX Director": [
        "UX Director",
        "Head of UX",
        "Director of User Experience",
        "VP UX",
    ],
    "Product Design Director": [
        "Product Design Director",
        "Head of Product Design",
        "VP Product Design",
    ],
    "Head of HR / People": [
        "Chief People Officer",
        "Head of HR",
        "VP HR",
        "HR Director",
    ],
}

CATEGORIES: List[str] = list(ROLE_MATRIX)

# Extra keywords that should classify into a category but are too noisy to
# spend a dedicated search query on (they match far too many false positives
# when used as a query term, but are reliable signals inside a job title).
_CLASSIFY_ONLY: Dict[str, List[str]] = {
    "CEO / Executive": ["Chief Executive", "Founder", "General Manager"],
    "Design Director": ["Director of Design", "Design Lead", "Creative Director"],
    "UX Director": ["Head of User Experience", "UX Lead", "Director UX"],
    "Product Design Director": ["Director of Product Design", "Product Design Lead"],
    "Head of HR / People": [
        "Head of People",
        "People Director",
        "Director of HR",
        "Chief Human Resources Officer",
        "Human Resources Director",
        "Talent Director",
    ],
}


def _keyword_pattern(keyword: str) -> re.Pattern:
    """Build a whitespace-tolerant, word-bounded pattern for a keyword."""
    parts = [re.escape(token) for token in keyword.split()]
    return re.compile(r"\b" + r"[\s\-/]+".join(parts) + r"\b", re.IGNORECASE)


# Pre-compiled (category, keyword, pattern) triples, longest keyword first so
# that "Head of Product Design" wins over "Head of Design".
_PATTERNS: List[tuple] = sorted(
    [
        (category, keyword, _keyword_pattern(keyword))
        for source in (ROLE_MATRIX, _CLASSIFY_ONLY)
        for category, keywords in source.items()
        for keyword in keywords
    ],
    key=lambda item: len(item[1]),
    reverse=True,
)


def classify(designation: str) -> Optional[str]:
    """Return the departmental category for a job title, or ``None``.

    The longest matching keyword wins, so "Head of Product Design" is
    classified as *Product Design Director* rather than *Design Director*.
    """
    if not designation:
        return None
    for category, _keyword, pattern in _PATTERNS:
        if pattern.search(designation):
            return category
    return None


def matches_any_role(designation: str) -> bool:
    """True when the designation contains at least one targeted role keyword."""
    return classify(designation) is not None


def keywords_for(category: str) -> List[str]:
    """Search keywords for a category.

    Built-in categories expand to their whole keyword set; anything else is a
    role the user typed themselves and stands as its own single keyword.
    """
    if category in ROLE_MATRIX:
        return ROLE_MATRIX[category]
    category = (category or "").strip()
    return [category] if category else []


def is_custom(category: str) -> bool:
    """True when the category is a user-supplied role, not a built-in one."""
    return bool(category) and category not in ROLE_MATRIX


def mentions_role(role: str, text: str) -> bool:
    """True when a free-text role appears in the text.

    Used to keep results for a role the matrix knows nothing about, where
    ``classify`` can offer no opinion at all.
    """
    if not role or not text:
        return False
    return bool(_keyword_pattern(role).search(text))
