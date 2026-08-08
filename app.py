"""Executive Contact Finder — Streamlit application.

Top control panel takes a company (required), an optional domain and an
optional country, then renders the six-column executive contact matrix.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from executive_finder import (
    CATEGORIES,
    COLUMNS,
    EMAIL_PATTERNS,
    SearchError,
    contacts_to_records,
    find_contacts,
    normalise_domain,
)
from executive_finder.emails import DEFAULT_PATTERN

st.set_page_config(
    page_title="Executive Contact Finder",
    page_icon="🎯",
    layout="wide",
)


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_search(
    company: str,
    domain: str,
    country: str,
    categories: tuple,
    email_pattern: str,
    max_per_category: int,
    require_company: bool,
    _progress=None,
):
    """Run the pipeline behind a one-hour cache keyed on the search inputs.

    ``_progress`` is underscore-prefixed so Streamlit leaves it out of the
    cache key — the callback differs on every rerun but the results do not.
    """
    contacts = find_contacts(
        company=company,
        domain=domain,
        country=country,
        categories=list(categories),
        email_pattern=email_pattern,
        max_per_category=max_per_category,
        require_company=require_company,
        progress=_progress,
    )
    return contacts_to_records(contacts)


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
        email_pattern = st.selectbox(
            "Email pattern",
            list(EMAIL_PATTERNS),
            index=list(EMAIL_PATTERNS).index(DEFAULT_PATTERN),
            help="Corporate address syntax used to predict the email.",
        )
        require_company = st.checkbox(
            "Only keep results that name the company",
            value=False,
            help="Stricter filter — fewer rows, less noise.",
        )
        st.divider()
        st.caption(
            "Emails are **predicted** from public naming conventions, not "
            "verified. Confirm before outreach and follow GDPR/CAN-SPAM rules "
            "in your market."
        )

    if not submitted:
        st.info("Enter a company name above and run the extraction to begin.")
        return

    if not company.strip():
        st.error("Company Name is required.")
        return

    if not categories:
        st.error("Select at least one role category in the sidebar.")
        return

    status = st.empty()
    bar = st.progress(0.0)

    def progress(message: str, fraction: float) -> None:
        status.caption(message)
        bar.progress(min(max(fraction, 0.0), 1.0))

    with st.spinner("Running X-Ray queries against public profile indexes…"):
        try:
            records = _cached_search(
                company.strip(),
                domain.strip(),
                country.strip(),
                tuple(categories),
                email_pattern,
                max_per_category,
                require_company,
                _progress=progress,
            )
        except SearchError as exc:
            st.error(
                "Every search provider refused the request — this usually means "
                "rate limiting. Wait a minute and try again."
            )
            st.caption(str(exc))
            return
        except ValueError as exc:
            st.error(str(exc))
            return
        finally:
            bar.empty()
            status.empty()

    if not records:
        st.warning(
            "No executive profiles matched. Try a broader company name, drop the "
            "country filter, or widen the role categories."
        )
        return

    frame = pd.DataFrame(records, columns=COLUMNS)

    resolved_domain = normalise_domain(domain, company)
    top, mid, tail = st.columns(3)
    top.metric("Contacts found", len(frame))
    mid.metric("Categories covered", frame["Category"].nunique())
    tail.metric("Email domain", resolved_domain or "—")

    st.dataframe(
        frame,
        use_container_width=True,
        hide_index=True,
        column_config={
            "LinkedIn Profile": st.column_config.LinkColumn(
                "LinkedIn Profile", display_text="Open Profile"
            ),
            "Designation": st.column_config.TextColumn("Designation", width="large"),
        },
    )

    st.download_button(
        "Download CSV",
        frame.to_csv(index=False).encode("utf-8"),
        file_name="{}_executive_contacts.csv".format(
            company.strip().lower().replace(" ", "_")
        ),
        mime="text/csv",
    )


if __name__ == "__main__":
    main()
