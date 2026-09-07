#!/usr/bin/env python3
"""Report which search providers actually answer from *this* machine.

The app's results are only ever as good as the providers that will talk to it,
and that depends entirely on where it runs.  From a home connection the scraped
front-ends (DuckDuckGo, Bing, Mojeek) generally answer normally and no API key
is needed at all.  From a shared datacenter address — which is what Streamlit
Community Cloud runs on — the same requests get a bot challenge, and only the
keyed APIs work.

Nothing in the app can tell you which situation you are in, because a challenge
page arrives as a perfectly ordinary HTTP 200.  This runs one real query
against every provider and prints what came back.

    python scripts/check_providers.py
    python scripts/check_providers.py --company Spotify --country Sweden

Exit status is 0 when at least one provider returned usable rows, 1 otherwise,
so it can be used as a check in a script.
"""

from __future__ import annotations

import argparse
import sys
import time

import requests

# Allow running from a clone without installing the package.
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from executive_finder.search import (  # noqa: E402
    PROVIDERS,
    api_key,
    build_query,
    is_linkedin_profile,
    looks_blocked,
)


def _describe(provider, query: str, timeout: float) -> tuple:
    """Run one provider and return (status, rows, profiles, detail)."""
    if not provider.available:
        return "no key", 0, 0, "set {}_API_KEY to enable".format(
            provider.needs_key.upper()
        )

    session = requests.Session()
    try:
        payload = provider.fetch(session, query, timeout)
    except Exception as exc:                      # network, HTTP, bad payload
        return "error", 0, 0, "{}".format(exc)[:90]

    if provider.parse is None:
        rows = list(payload)
    elif looks_blocked(payload):
        return "BLOCKED", 0, 0, "answered 200 with a challenge or stub page"
    else:
        rows = provider.parse(payload)

    profiles = sum(1 for r in rows if is_linkedin_profile(r.url))
    if rows and not profiles:
        return "ignored", len(rows), 0, "dropped the site: filter, all off-site"
    if not rows:
        return "empty", 0, 0, "responded normally with no results"
    return "OK", len(rows), profiles, rows[0].title[:60]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company", default="Spotify")
    parser.add_argument("--country", default="Sweden")
    parser.add_argument("--role", default="CEO")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--pause", type=float, default=1.0,
                        help="seconds between providers; keep it polite")
    args = parser.parse_args()

    query = build_query(args.company, [args.role], args.country)
    print("query : {}\n".format(query))
    print("{:<17} {:<9} {:>5} {:>9}  {}".format(
        "PROVIDER", "STATUS", "ROWS", "PROFILES", "DETAIL"))
    print("-" * 100)

    usable = 0
    for index, provider in enumerate(PROVIDERS):
        if index and args.pause:
            time.sleep(args.pause)
        status, rows, profiles, detail = _describe(provider, query, args.timeout)
        if status == "OK":
            usable += 1
        print("{:<17} {:<9} {:>5} {:>9}  {}".format(
            provider.name, status, rows, profiles, detail))

    print()
    if usable:
        print("{} provider(s) answered from this machine — the app will "
              "work here.".format(usable))
        if not (api_key("serper") or api_key("brave")):
            print("No API key is configured, and none is needed: the scraped "
                  "providers are answering.")
        return 0

    print("No provider returned usable rows from this machine.")
    print("If they are BLOCKED, this address is being challenged — run the app "
          "on a home connection, or configure SERPER_API_KEY.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
