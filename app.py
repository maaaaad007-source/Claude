"""Executive Contact Finder — Streamlit application.

Top control panel takes a company (required), an optional domain and an
optional country, then renders the six-column executive contact matrix.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from executive_finder import (
    __version__,
    CATEGORIES,
    COLUMNS,
    EMAIL_PATTERNS,
    SearchError,
    contacts_to_records,
    normalise_domain,
)
from executive_finder.emails import DEFAULT_PATTERN
from executive_finder.pipeline import find_contacts_detailed, split_company_input
from executive_finder.search import api_key, configure_api_keys

st.set_page_config(
    page_title="Executive Contact Finder",
    page_icon="🎯",
    layout="wide",
)


def _load_api_keys() -> None:
    """Pick up search API credentials from Streamlit secrets, if present.

    Hosted deployments share a datacenter IP that search engines block far
    more aggressively than a home connection, so a key is the only reliable
    path there.  Absent one the app still runs on the scraped providers.
    """
    def _secret(name: str) -> str:
        # st.secrets raises on *lookup*, not on attribute access, and raises
        # when no secrets.toml exists at all — so each read must be guarded.
        try:
            return str(st.secrets[name])
        except Exception:
            return ""

    configure_api_keys(serper=_secret("SERPER_API_KEY"), brave=_secret("BRAVE_API_KEY"))
    if _secret("HUNTER_API_KEY"):
        st.session_state.setdefault("secrets_hunter_key", _secret("HUNTER_API_KEY"))


def _apply_session_key() -> None:
    """Apply a key pasted into the sidebar for this browser session.

    The Secrets editor on Streamlit Cloud is buried behind a menu that is hard
    to reach on a phone, so the app accepts a key directly.  It is held in
    session state only — never written to disk, never logged, and gone when the
    tab closes — so Secrets remains the better home for a permanent key.
    """
    typed = st.session_state.get("session_api_key", "").strip()
    if typed:
        configure_api_keys(serper=typed)


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_search(
    company: str,
    domain: str,
    country: str,
    categories: tuple,
    email_pattern: str,
    max_per_category: int,
    require_company: bool,
    has_api_key: bool,
    country_filter: str,
    hunter_key: str,
    use_email_finder: bool,
    discover_patterns: bool,
    _progress=None,
):
    """Run the pipeline behind a one-hour cache keyed on the search inputs.

    ``_progress`` is underscore-prefixed so Streamlit leaves it out of the
    cache key — the callback differs on every rerun but the results do not.
    ``has_api_key`` is part of the key so that adding a key invalidates a
    cached blocked run instead of replaying it.
    """
    contacts, report = find_contacts_detailed(
        company=company,
        domain=domain,
        country=country,
        categories=list(categories),
        email_pattern=email_pattern,
        max_per_category=max_per_category,
        require_company=require_company,
        country_filter=country_filter,
        hunter_key=hunter_key,
        use_email_finder=use_email_finder,
        discover_patterns=discover_patterns,
        progress=_progress,
    )
    return contacts_to_records(contacts), report


def _render_api_key_help() -> None:
    """Explain the one fix that reliably works on a hosted deployment."""
    if api_key("serper") or api_key("brave"):
        st.caption(
            "A search API key is configured but still did not return results — "
            "check the key is valid and has quota remaining."
        )
        return

    st.markdown(
        """
**Why this happens:** scraping search-engine HTML works from a home
connection but is blocked from shared datacenter IPs — which is exactly what
Streamlit Community Cloud runs on. Every app on that host shares the same
outbound addresses, so search engines challenge them by default.

**Fastest fix — paste a key in the sidebar.** Open the sidebar (the **»**
arrow, top-left), scroll to **Search API key**, paste, and search again. Get a
free key at [serper.dev](https://serper.dev) — 2,500 queries, no card needed.
The key lasts for this browser session.

**To make it permanent**, add it to your app's Secrets instead:
[share.streamlit.io](https://share.streamlit.io) → the **⋮** menu beside your
app → **Settings** → **Secrets** →

```toml
SERPER_API_KEY = "your-key-here"
```

Running locally with `streamlit run app.py` needs no key at all — your home IP
is not blocked.
"""
    )


def _render_diagnostics(report, expanded: bool = False) -> None:
    """Show what each provider did and where the rows went."""
    with st.expander("Run diagnostics", expanded=expanded):
        st.caption(report.summary())
        if report.enrichment_note:
            st.caption("**Email lookup:** " + report.enrichment_note)
        if report.pattern_note:
            st.caption("**Pattern discovery:** " + report.pattern_note)

        if report.outcomes:
            st.write("**Search providers**")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Provider": o.name,
                            "Status": o.status,
                            "Rows": o.rows,
                            "Detail": o.detail,
                        }
                        for o in report.outcomes
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )

        if report.queries:
            st.write("**Queries issued**")
            st.code("\n".join(report.queries), language="text")


def main() -> None:
    st.title("🎯 Executive Contact Finder")
    st.caption(
        "Discover executive names, exact job titles, predicted corporate email "
        "addresses and direct LinkedIn profile links for a target company and region."
    )

    with st.form("search_panel"):
        left, middle, right = st.columns(3)
        company = left.text_input(
            "Company Name *",
            placeholder="Volvo, Spotify, IKEA…",
            help="Required. The target organisation.",
        )
        domain = middle.text_input(
            "Company Domain",
            placeholder="spotify.com",
            help="Optional. Defaults to companyname.com when left blank.",
        )
        country = right.text_input(
            "Country",
            placeholder="Sweden, United Kingdom, United States…",
            help="Optional. Target market or regional headquarters.",
        )
        submitted = st.form_submit_button(
            "Extract Real Executive Contacts",
            type="primary",
            use_container_width=True,
        )

    with st.sidebar:
        st.header("Search settings")
        categories = st.multiselect(
            "Role categories", CATEGORIES, default=CATEGORIES,
        )
        max_per_category = st.slider("Max results per category", 1, 25, 10)
        require_company = st.checkbox(
            "Only keep results that name the company",
            value=False,
            help="Stricter filter — fewer rows, less noise.",
        )

        st.divider()
        st.subheader("Country filter")
        country_filter = st.radio(
            "How strictly to enforce the country",
            options=["strict", "relaxed", "off"],
            index=0,
            format_func=lambda mode: {
                "strict": "Strict — must show country evidence",
                "relaxed": "Relaxed — drop only clear mismatches",
                "off": "Off — keep every market",
            }[mode],
            help="The country in the query is only a ranking hint, so results "
                 "from other markets leak through. This filters on the LinkedIn "
                 "locale subdomain plus country and city mentions.",
        )

        st.divider()
        st.subheader("Email lookup")
        st.text_input(
            "Hunter.io API key",
            key="hunter_api_key",
            type="password",
            placeholder="paste key here",
            help="Optional. Returns real, verified addresses instead of guesses. "
                 "Held for this browser session only.",
        )
        hunter_key = (
            st.session_state.get("hunter_api_key", "").strip()
            or st.session_state.get("secrets_hunter_key", "")
        )
        discover_patterns = st.checkbox(
            "Infer the pattern from published addresses",
            value=True,
            help="Free. Searches for addresses the company has published and "
                 "derives its real pattern, which fixes every guessed row at "
                 "once. Costs one extra search query per run.",
        )
        use_email_finder = st.checkbox(
            "Look up each person individually",
            value=False,
            disabled=not hunter_key,
            help="Slower and spends one Hunter credit per person, but resolves "
                 "executives the bulk domain search did not already cover.",
        )
        if hunter_key:
            st.success("Hunter key active — real addresses where available.",
                       icon="✅")
        else:
            st.caption(
                "Without a Hunter key addresses are **guesses**. Pattern "
                "discovery below makes those guesses follow the company's own "
                "convention rather than an assumed one."
            )
            email_pattern = st.selectbox(
                "Guess pattern",
                list(EMAIL_PATTERNS),
                index=list(EMAIL_PATTERNS).index(DEFAULT_PATTERN),
                help="Address shape assumed when no real address can be found.",
            )

        if hunter_key:
            email_pattern = DEFAULT_PATTERN

        st.divider()
        st.subheader("Search API key")
        st.text_input(
            "Serper API key",
            key="session_api_key",
            type="password",
            placeholder="paste key here",
            help="Needed on hosted deployments, where search engines block the "
                 "shared server IP. Held for this browser session only.",
        )
        _apply_session_key()
        if api_key("serper") or api_key("brave"):
            st.success("Search API key active.", icon="✅")
        else:
            st.warning("No API key — hosted runs will likely be blocked.", icon="⚠️")
            st.caption(
                "After pasting, press **Enter** (or tap outside the box) — "
                "Streamlit only applies the value once the field is committed."
            )

        st.divider()
        st.caption(
            "Emails are **predicted** from public naming conventions, not "
            "verified. Confirm before outreach and follow GDPR/CAN-SPAM rules "
            "in your market."
        )
        # Build stamp: makes a stale deployment obvious at a glance instead of
        # leaving "my fix isn't there" as a guess.
        st.caption("Build v{}".format(__version__))

    if not submitted:
        st.info("Enter a company name above and run the extraction to begin.")
        return

    if not company.strip():
        st.error("Company Name is required.")
        return

    if not categories:
        st.error("Select at least one role category in the sidebar.")
        return

    # A domain typed into the company field poisons every query, so recover it.
    resolved_company, resolved_input_domain = split_company_input(company, domain)
    if resolved_company.lower() != company.strip().lower():
        st.info(
            "Searching for **{}** — the Company Name field looked like a domain, "
            "so it was split into a company name and a mail domain.".format(
                resolved_company
            )
        )

    status = st.empty()
    bar = st.progress(0.0)

    def progress(message: str, fraction: float) -> None:
        status.caption(message)
        bar.progress(min(max(fraction, 0.0), 1.0))

    with st.spinner("Running X-Ray queries against public profile indexes…"):
        try:
            records, report = _cached_search(
                company.strip(),
                domain.strip(),
                country.strip(),
                tuple(categories),
                email_pattern,
                max_per_category,
                require_company,
                bool(api_key("serper") or api_key("brave")),
                country_filter,
                hunter_key,
                use_email_finder,
                discover_patterns,
                _progress=progress,
            )
        except SearchError as exc:
            st.error(
                "**Blocked by the search providers** — no provider returned "
                "results. This is the usual outcome on shared cloud IPs; see "
                "the fix below."
            )
            _render_api_key_help()
            with st.expander("Provider detail"):
                st.code(str(exc), language="text")
            return
        except ValueError as exc:
            st.error(str(exc))
            return
        finally:
            bar.empty()
            status.empty()

    if not records:
        if report.blocked:
            st.error(
                "**Blocked by the search providers.** They answered, but with a "
                "bot-challenge page rather than results — so this is not "
                "'nothing matched', it's 'we were refused'."
            )
            _render_api_key_help()
        elif not report.raw_results:
            st.warning(
                "The search providers returned no results at all for these "
                "queries. Try a broader company name or drop the country filter."
            )
        else:
            st.warning(
                "Found {} search results, but none survived filtering. {}".format(
                    report.raw_results, report.summary()
                )
            )
            st.caption(
                "If everything was dropped as off-target, the company name may not "
                "appear in profile headlines — try the company's common short name."
            )
        _render_diagnostics(report, expanded=True)
        return

    frame = pd.DataFrame(records, columns=COLUMNS)

    resolved_domain = normalise_domain(resolved_input_domain, resolved_company)
    first, second, third, fourth = st.columns(4)
    first.metric("Contacts found", len(frame))
    second.metric("Categories covered", frame["Category"].nunique())
    third.metric(
        "Real addresses",
        "{} of {}".format(report.emails_observed, len(frame)),
        help="Addresses Hunter actually observed, as opposed to pattern guesses.",
    )
    fourth.metric("Email domain", resolved_domain or "—")

    if report.dropped_wrong_country:
        st.caption(
            "Country filter removed {} result(s) from other markets. "
            "Relax it in the sidebar if you expected more.".format(
                report.dropped_wrong_country
            )
        )

    st.dataframe(
        frame,
        use_container_width=True,
        hide_index=True,
        column_config={
            "LinkedIn Profile": st.column_config.LinkColumn(
                "LinkedIn Profile", display_text="Open Profile"
            ),
            "Designation": st.column_config.TextColumn("Designation", width="large"),
            "Email Source": st.column_config.TextColumn(
                "Email Source",
                width="medium",
                help="Verified/Found = observed by Hunter. Guess = derived from "
                     "a naming pattern and not confirmed to exist.",
            ),
        },
    )

    st.download_button(
        "Download CSV",
        frame.to_csv(index=False).encode("utf-8"),
        file_name="{}_executive_contacts.csv".format(
            resolved_company.lower().replace(" ", "_")
        ),
        mime="text/csv",
    )

    _render_diagnostics(report)


if __name__ == "__main__":
    main()
