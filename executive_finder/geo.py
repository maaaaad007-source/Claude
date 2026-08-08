"""Country matching for search results.

Putting the country in the X-Ray query only biases the ranking — search engines
treat it as a soft term, and a LinkedIn headline rarely contains a country name
at all, so results from every market leak through.  This module supplies the
positive check the query cannot: it reads the locale subdomain LinkedIn puts on
a profile URL (``se.linkedin.com``) and looks for country and city evidence in
the result text.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Dict, FrozenSet, NamedTuple, Sequence, Set
from urllib.parse import urlparse

__all__ = ["CountryVerdict", "locale_of", "country_match", "known_country"]


class CountryVerdict(NamedTuple):
    """Whether a result belongs to the requested country, and on what evidence."""

    matches: bool
    reason: str


# LinkedIn locale subdomains per country, with the aliases people actually type.
_LOCALES: Dict[str, Set[str]] = {
    "sweden": {"se"},
    "norway": {"no"},
    "denmark": {"dk"},
    "finland": {"fi"},
    "iceland": {"is"},
    "united kingdom": {"uk", "gb"},
    "ireland": {"ie"},
    "germany": {"de"},
    "france": {"fr"},
    "netherlands": {"nl"},
    "belgium": {"be"},
    "luxembourg": {"lu"},
    "switzerland": {"ch"},
    "austria": {"at"},
    "spain": {"es"},
    "portugal": {"pt"},
    "italy": {"it"},
    "greece": {"gr"},
    "poland": {"pl"},
    "czechia": {"cz"},
    "hungary": {"hu"},
    "romania": {"ro"},
    "turkey": {"tr"},
    "united states": {"us", "www"},
    "canada": {"ca"},
    "mexico": {"mx"},
    "brazil": {"br"},
    "argentina": {"ar"},
    "chile": {"cl"},
    "colombia": {"co"},
    "australia": {"au"},
    "new zealand": {"nz"},
    "india": {"in"},
    "singapore": {"sg"},
    "malaysia": {"my"},
    "indonesia": {"id"},
    "philippines": {"ph"},
    "thailand": {"th"},
    "vietnam": {"vn"},
    "japan": {"jp"},
    "south korea": {"kr"},
    "china": {"cn"},
    "hong kong": {"hk"},
    "taiwan": {"tw"},
    "united arab emirates": {"ae"},
    "saudi arabia": {"sa"},
    "israel": {"il"},
    "south africa": {"za"},
    "nigeria": {"ng"},
    "kenya": {"ke"},
    "egypt": {"eg"},
}

# Alternative names and endonyms that appear in result snippets.
_ALIASES: Dict[str, str] = {
    "uk": "united kingdom", "u.k.": "united kingdom", "britain": "united kingdom",
    "great britain": "united kingdom", "england": "united kingdom",
    "scotland": "united kingdom", "wales": "united kingdom",
    "northern ireland": "united kingdom",
    "usa": "united states", "u.s.": "united states", "u.s.a.": "united states",
    "us": "united states", "america": "united states",
    "united states of america": "united states",
    "sverige": "sweden", "norge": "norway", "danmark": "denmark",
    "suomi": "finland", "deutschland": "germany", "österreich": "austria",
    "schweiz": "switzerland", "suisse": "switzerland", "svizzera": "switzerland",
    "nederland": "netherlands", "holland": "netherlands",
    "belgië": "belgium", "belgique": "belgium",
    "españa": "spain", "italia": "italy", "polska": "poland",
    "brasil": "brazil", "méxico": "mexico", "czech republic": "czechia",
    "uae": "united arab emirates", "emirates": "united arab emirates",
    "korea": "south korea", "republic of korea": "south korea",
    "eire": "ireland", "roi": "ireland",
}

# City evidence, for snippets that name a city but not the country.
_CITIES: Dict[str, FrozenSet[str]] = {
    "sweden": frozenset({"stockholm", "gothenburg", "göteborg", "malmö", "malmo",
                         "uppsala", "lund", "linköping", "västerås"}),
    "norway": frozenset({"oslo", "bergen", "trondheim", "stavanger"}),
    "denmark": frozenset({"copenhagen", "københavn", "aarhus", "odense"}),
    "finland": frozenset({"helsinki", "espoo", "tampere", "oulu"}),
    "united kingdom": frozenset({"london", "manchester", "birmingham", "leeds",
                                 "glasgow", "edinburgh", "bristol", "cambridge",
                                 "oxford", "liverpool", "sheffield", "cardiff"}),
    "ireland": frozenset({"dublin", "cork", "galway", "limerick"}),
    "germany": frozenset({"berlin", "munich", "münchen", "hamburg", "frankfurt",
                          "cologne", "köln", "stuttgart", "düsseldorf", "leipzig"}),
    "france": frozenset({"paris", "lyon", "marseille", "toulouse", "bordeaux",
                         "lille", "nantes", "nice"}),
    "netherlands": frozenset({"amsterdam", "rotterdam", "utrecht", "eindhoven",
                              "the hague", "den haag"}),
    "belgium": frozenset({"brussels", "bruxelles", "antwerp", "antwerpen", "ghent"}),
    "switzerland": frozenset({"zurich", "zürich", "geneva", "genève", "basel",
                              "lausanne", "bern"}),
    "austria": frozenset({"vienna", "wien", "graz", "linz", "salzburg"}),
    "spain": frozenset({"madrid", "barcelona", "valencia", "seville", "sevilla",
                        "bilbao", "málaga"}),
    "portugal": frozenset({"lisbon", "lisboa", "porto", "braga"}),
    "italy": frozenset({"milan", "milano", "rome", "roma", "turin", "torino",
                        "bologna", "florence", "firenze", "naples"}),
    "poland": frozenset({"warsaw", "warszawa", "krakow", "kraków", "wroclaw",
                         "wrocław", "gdansk", "gdańsk", "poznan"}),
    "united states": frozenset({"new york", "san francisco", "los angeles",
                                "chicago", "boston", "seattle", "austin",
                                "atlanta", "denver", "dallas", "miami",
                                "washington", "san jose", "portland"}),
    "canada": frozenset({"toronto", "vancouver", "montreal", "montréal",
                         "calgary", "ottawa", "waterloo"}),
    "australia": frozenset({"sydney", "melbourne", "brisbane", "perth",
                            "adelaide", "canberra"}),
    "new zealand": frozenset({"auckland", "wellington", "christchurch"}),
    "india": frozenset({"bangalore", "bengaluru", "mumbai", "delhi", "gurgaon",
                        "gurugram", "hyderabad", "pune", "chennai", "noida"}),
    "singapore": frozenset({"singapore"}),
    "united arab emirates": frozenset({"dubai", "abu dhabi", "sharjah"}),
    "japan": frozenset({"tokyo", "osaka", "kyoto", "yokohama"}),
    "brazil": frozenset({"sao paulo", "são paulo", "rio de janeiro", "brasilia"}),
    "mexico": frozenset({"mexico city", "ciudad de méxico", "guadalajara",
                         "monterrey"}),
    "south africa": frozenset({"johannesburg", "cape town", "durban", "pretoria"}),
}

_PROFILE_HOST = re.compile(r"^([a-z0-9\-]+)\.linkedin\.com$", re.IGNORECASE)


def _fold(value: str) -> str:
    """Lowercase and strip accents so 'Göteborg' matches 'goteborg'."""
    decomposed = unicodedata.normalize("NFKD", (value or "").lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _canonical(country: str) -> str:
    """Resolve user input to a canonical country key, or '' if unknown."""
    raw = (country or "").strip().lower()
    if not raw:
        return ""
    raw = _ALIASES.get(raw, raw)
    if raw in _LOCALES:
        return raw
    folded = _fold(raw)
    for name in _LOCALES:
        if _fold(name) == folded:
            return name
    for alias, target in _ALIASES.items():
        if _fold(alias) == folded:
            return target
    return ""


def known_country(country: str) -> bool:
    """True when the country is one this module can positively verify."""
    return bool(_canonical(country))


def locale_of(url: str) -> str:
    """Return the LinkedIn locale subdomain of a profile URL ('se', 'uk', …).

    Must be read before the URL is canonicalised — canonicalisation rewrites
    every host to ``www.linkedin.com`` and destroys this signal.
    """
    if not url:
        return ""
    host = urlparse(url if "://" in url else "https://" + url).netloc.lower()
    host = host.split(":")[0]
    match = _PROFILE_HOST.match(host)
    return match.group(1).lower() if match else ""


def country_match(
    country: str,
    url: str,
    text: str,
    strict: bool = True,
) -> CountryVerdict:
    """Decide whether a result belongs to ``country``.

    ``strict`` requires positive evidence — a matching locale subdomain, the
    country named in the text, or a known city of that country.  Relaxed mode
    keeps anything that does not actively contradict the filter, which only
    removes profiles hosted on another country's LinkedIn locale.
    """
    canonical = _canonical(country)
    if not canonical:
        # No country requested, or one we hold no data for: never filter on a
        # guess — silently dropping rows would be worse than a loose result set.
        return CountryVerdict(True, "no country filter" if not country.strip()
                              else "country not recognised")

    accepted = _LOCALES[canonical]
    locale = locale_of(url)

    if locale and locale in accepted:
        return CountryVerdict(True, "locale {}.linkedin.com".format(locale))

    # A profile served from another country's locale is a positive mismatch.
    if locale and locale not in accepted and locale != "www":
        return CountryVerdict(False, "locale {}.linkedin.com".format(locale))

    folded = _fold(text)
    names = [canonical] + [a for a, t in _ALIASES.items() if t == canonical]
    for name in names:
        if re.search(r"\b{}\b".format(re.escape(_fold(name))), folded):
            return CountryVerdict(True, "names {}".format(name))

    for city in _CITIES.get(canonical, frozenset()):
        if re.search(r"\b{}\b".format(re.escape(_fold(city))), folded):
            return CountryVerdict(True, "city {}".format(city))

    if strict:
        return CountryVerdict(False, "no country evidence")
    return CountryVerdict(True, "no contradiction")
