"""Executive Contact Finder — Streamlit application.

A company, an optional domain and an optional country produce a table of
executives with their titles, best-available email address and profile link.
"""

from __future__ import annotations

import os

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

# The role sweep always runs at full depth; there is no reason to make the
# user choose a smaller number, and a partial sweep only hides contacts.
MAX_PER_CATEGORY = 25

# The table shows these; the CSV keeps every column, so the provenance of each
# address (verified vs guessed) survives the export even though it is not on
# screen.
DISPLAY_COLUMNS = ["Full Name", "Designation", "Email", "Country",
                   "LinkedIn Profile"]

st.set_page_config(
    page_title="Contact Finder",
    page_icon="◆",
    layout="wide",
    initial_sidebar_state="expanded",
)


# --------------------------------------------------------------------------- #
# Theme
# --------------------------------------------------------------------------- #
LIGHT_TOKENS = {
    "ink": "#1F2430", "muted": "#8A90A0", "line": "#ECEEF2",
    "panel": "#FAFBFC", "surface": "#FFFFFF", "accent_soft": "#EDF2F0",
}
DARK_TOKENS = {
    "ink": "#E6E9F0", "muted": "#99A0AF", "line": "#2C313C",
    "panel": "#161A21", "surface": "#1C2029", "accent_soft": "#24312C",
}


def _theme_css(dark: bool) -> str:
    """Stylesheet for the viewer's chosen theme.

    Every surface colour is a token so the Light/Dark control actually works.
    Hard-coding white here would leave a dark viewer with white cards and
    unreadable text, which is worse than offering no choice at all.
    """
    t = DARK_TOKENS if dark else LIGHT_TOKENS
    return """
<style>
:root {
  --accent:     #6B9080;
  --accent-alt: #D98494;
  --on-accent:  #FFFFFF;
  --accent-soft:%(accent_soft)s;
  --ink:        %(ink)s;
  --muted:      %(muted)s;
  --line:       %(line)s;
  --panel:      %(panel)s;
  --surface:    %(surface)s;
}

/* Keep the header and toolbar in the layout: the toolbar holds both the
   sidebar-expand control and the theme menu. Only the deploy button goes. */
[data-testid="stHeader"] {
  background: transparent !important;
  box-shadow: none !important;
  border: none !important;
}
[data-testid="stAppDeployButton"], footer { display: none !important; }

/* The control that reopens the sidebar sits bottom-left, out of the way. */
[data-testid="stExpandSidebarButton"] {
  display: flex !important;
  visibility: visible !important;
  opacity: 1 !important;
  position: fixed !important;
  top: auto !important;
  bottom: 1.1rem !important;
  left: 1.1rem !important;
  z-index: 1000000 !important;
  background: var(--surface) !important;
  border: 1px solid var(--line) !important;
  border-radius: 999px !important;
  padding: .3rem !important;
}

/* Streamlit Cloud's injected "Manage app" control, hidden by selector where
   possible; a script handles the cases these miss. */
[data-testid="manage-app-button"], #ManageAppButton,
.viewerBadge_container__1QSob, [class*="viewerBadge"] {
  display: none !important;
}

.brand {
  font-family: Georgia, "Times New Roman", serif;
  font-size: 1.9rem;
  letter-spacing: -.015em;
  margin: 0 0 1.4rem 0;
  line-height: 1;
}
.brand .a { color: var(--accent); }
.brand .b { color: var(--accent-alt); }

html, body, [class*="css"] { color: var(--ink); }

[data-testid="stMainBlockContainer"], .block-container {
  padding: 1.25rem 3rem 4rem !important;
  max-width: 1240px;
}

[data-testid="stSidebar"] {
  background: var(--panel);
  border-right: 1px solid var(--line);
}
[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {
  padding-top: 1.5rem;
}
/* Widget labels and placeholders inherit Streamlit's own palette, which is
   tuned for its default surfaces rather than these; set them from the tokens
   so both themes stay legible. */
[data-testid="stWidgetLabel"], [data-testid="stWidgetLabel"] * {
  color: var(--ink) !important;
}
input::placeholder { color: var(--muted) !important; opacity: 1 !important; }
[data-testid="stExpander"] summary,
[data-testid="stExpander"] summary * { color: var(--ink) !important; }
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] * {
  color: var(--muted) !important;
}
[data-testid="stSidebar"] label { font-size: .82rem; color: var(--muted); }
[data-testid="stSidebar"] hr { margin: 1.1rem 0; border-color: var(--line); }

.section-title {
  font-size: 1.4rem; font-weight: 700; letter-spacing: -.02em;
  margin: .4rem 0 .2rem;
}
.section-sub { color: var(--muted); font-size: .86rem; margin-bottom: 1rem; }

/* Inputs share the card's border exactly. Streamlit draws its input outline
   as a box-shadow, which reads as a glow beside a crisp 1px edge. */
[data-testid="stTextInput"] input,
[data-testid="stNumberInput"] input,
[data-testid="stTextInput"] > div,
[data-testid="stTextInput"] div[class*="react-aria"] {
  box-shadow: none !important;
  outline: none !important;
}
[data-testid="stTextInput"] input,
[data-testid="stNumberInput"] input {
  border-radius: 10px !important;
  border: 1px solid var(--line) !important;
  background: var(--surface) !important;
  color: var(--ink) !important;
  padding: .6rem .85rem !important;
}
[data-testid="stTextInput"] input:focus {
  border-color: var(--accent) !important;
  box-shadow: none !important;
}
[data-testid="stForm"] { box-shadow: none !important; }

.stButton > button, [data-testid="stFormSubmitButton"] > button {
  border-radius: 999px !important;
  border: 1px solid var(--line) !important;
  padding: .58rem 1.4rem !important;
  font-weight: 600 !important;
  box-shadow: none !important;
}
[data-testid="stFormSubmitButton"] > button {
  background: var(--accent) !important;
  border-color: var(--accent) !important;
  color: var(--on-accent) !important;
}
[data-testid="stFormSubmitButton"] > button:hover { filter: brightness(.96); }

[data-testid="stForm"],
[data-testid="stExpander"] details,
[data-testid="stDataFrame"] {
  border: 1px solid var(--line) !important;
  border-radius: 14px !important;
  background: var(--surface);
}
[data-testid="stForm"] { padding: 1.35rem 1.5rem 1.15rem !important; }
[data-testid="stExpander"] details { background: var(--panel); }
[data-testid="stExpander"] summary { font-size: .88rem; font-weight: 600; }

[data-testid="stAlert"] { border-radius: 12px; border: 1px solid var(--line); }

/* Role chips: the container clips its contents once every role is selected,
   and a solid accent fill leaves the text short of comfortable contrast. */
[data-testid="stMultiSelect"] * { max-height: none !important; }
[data-testid="stMultiSelect"] span { color: var(--ink) !important; }
[data-testid="stMultiSelect"] span[class*="st-emotion"] {
  background-color: var(--accent-soft) !important;
}

.empty-state {
  color: var(--muted); font-size: .88rem;
  padding: 3.5rem 0; text-align: center;
}

.build-stamp { color: var(--muted); font-size: .74rem; margin-top: .5rem; }
</style>
""" % t


def _inject_theme() -> None:
    """Apply the stylesheet matching the viewer's System/Light/Dark choice."""
    try:
        dark = str(getattr(st.context.theme, "type", "light")).lower() == "dark"
    except Exception:
        dark = False
    st.markdown(_theme_css(dark), unsafe_allow_html=True)


def _hide_host_chrome() -> None:
    """Hide Streamlit Cloud's injected "Manage app" control.

    It is added to the page by the host after the app renders and carries no
    stable id, so a stylesheet alone cannot reliably reach it. This runs in a
    zero-height component frame and hides the control in the parent document,
    re-checking when the host re-adds it. Everything is wrapped defensively:
    the control does not exist off Streamlit Cloud, and failing to find it
    must never disturb the page.
    """
    st.components.v1.html(
        """
<script>
(function () {
  var doc;
  try { doc = window.parent.document; } catch (e) { return; }
  if (!doc) return;

  function hide() {
    var nodes = doc.querySelectorAll('button, a, div, span');
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (el.childElementCount > 3) continue;
      if ((el.textContent || '').trim() !== 'Manage app') continue;
      var n = el;
      for (var d = 0; d < 4 && n && n !== doc.body; d++) {
        var pos = window.parent.getComputedStyle(n).position;
        if (pos === 'fixed' || pos === 'absolute') break;
        n = n.parentElement;
      }
      (n || el).style.setProperty('display', 'none', 'important');
    }
  }

  hide();
  var pending = null;
  new MutationObserver(function () {
    if (pending) return;                       // debounce: the host mutates often
    pending = setTimeout(function () { pending = null; hide(); }, 300);
  }).observe(doc.body, { childList: true, subtree: true });
})();
</script>
""",
        height=0,
    )


# --------------------------------------------------------------------------- #
# Credentials
# --------------------------------------------------------------------------- #
def _secret(name: str) -> str:
    """Read one Streamlit secret, tolerating the absence of any secrets file.

    ``st.secrets`` raises on *lookup* — and raises outright when no
    secrets.toml exists — so every read must be guarded individually.
    """
    try:
        return str(st.secrets[name])
    except Exception:
        return ""


def _configured_key(name: str) -> str:
    """A key supplied by the deployment: Streamlit secrets, else environment."""
    return _secret(name) or os.environ.get(name, "")


def _resolve_api_keys() -> str:
    """Settle which search key is in force this run and report its origin.

    Two sources are layered, deployment-wide first and a per-session override
    second, and the whole registry is rewritten every run.  Rewriting matters:
    without it, emptying the sidebar box would leave the previously typed key
    in place instead of falling back to the deployment's own key.
    """
    deployment = _configured_key("SERPER_API_KEY")
    typed = st.session_state.get("session_api_key", "").strip()

    configure_api_keys(
        serper=typed or deployment,
        brave=_configured_key("BRAVE_API_KEY"),
        replace=True,
    )

    if typed:
        return "session" if not deployment else "session override"
    return "deployment" if deployment else ""


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #
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
Search engines block the shared IPs that hosted deployments run on, so scraped
providers get a bot challenge instead of results.

**Fastest fix:** open the sidebar → **API keys** → paste a
[serper.dev](https://serper.dev) key (free, 2,500 queries) → press Enter.

**Permanent fix:** [share.streamlit.io](https://share.streamlit.io) → the **⋮**
beside your app → **Settings** → **Secrets** →

```toml
SERPER_API_KEY = "your-key-here"
```
"""
    )


def _render_diagnostics(report, expanded: bool = False) -> None:
    """Show what each provider did and where the rows went."""
    with st.expander("Diagnostics", expanded=expanded):
        st.caption(report.summary())
        if report.enrichment_note:
            st.caption("**Email lookup:** " + report.enrichment_note)
        if report.pattern_note:
            st.caption("**Pattern discovery:** " + report.pattern_note)

        if report.outcomes:
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
            st.code("\n".join(report.queries), language="text")


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
def _render_sidebar() -> dict:
    """Draw the settings rail and return the chosen options.

    Everything optional lives inside a collapsed expander so the resting state
    is four controls, not twenty.
    """
    with st.sidebar:
        categories = st.multiselect(
            "Roles",
            CATEGORIES,
            default=CATEGORIES,
            accept_new_options=True,
            placeholder="Pick or type a role…",
            help="Not limited to these five — type any role and press Enter, "
                 "e.g. CTO, Head of Marketing, Country Manager. Each role is "
                 "searched as its own query.",
        )
        country_filter = st.radio(
            "Country match",
            options=["strict", "relaxed", "off"],
            index=0,
            horizontal=True,
            format_func=str.capitalize,
            help="Strict requires country evidence on each result. Relaxed drops "
                 "only clear mismatches. Off keeps every market.",
        )

        st.divider()

        with st.expander("Email lookup"):
            st.text_input(
                "Hunter.io key",
                key="hunter_api_key",
                type="password",
                placeholder="optional",
                help="Returns real, observed addresses instead of guesses.",
            )
            hunter_key = (
                st.session_state.get("hunter_api_key", "").strip()
                or _configured_key("HUNTER_API_KEY")
            )
            discover_patterns = st.checkbox(
                "Infer pattern from published addresses", value=True,
                help="Free. Derives the company's real address shape, which "
                     "fixes every guessed row at once.",
            )
            use_email_finder = st.checkbox(
                "Look up each person individually", value=False,
                disabled=not hunter_key,
                help="Spends one Hunter credit per person.",
            )
            if hunter_key:
                email_pattern = DEFAULT_PATTERN
                st.caption("Hunter key active.")
            else:
                email_pattern = st.selectbox(
                    "Fallback pattern",
                    list(EMAIL_PATTERNS),
                    index=list(EMAIL_PATTERNS).index(DEFAULT_PATTERN),
                )

        with st.expander("API keys"):
            st.text_input(
                "Serper key",
                key="session_api_key",
                type="password",
                placeholder="optional if set in Secrets",
                help="Needed on hosted deployments, whose shared IP search "
                     "engines block. Press Enter to apply.",
            )
            key_source = _resolve_api_keys()
            if key_source == "deployment":
                st.caption("✅ Active — loaded from app configuration.")
            elif key_source:
                st.caption("✅ Active — entered this session.")
            else:
                st.caption("⚠️ None set. Hosted runs will likely be blocked.")

        with st.expander("Advanced"):
            require_company = st.checkbox(
                "Only keep results naming the company", value=False
            )

        st.markdown(
            '<div class="build-stamp">Addresses are predicted unless marked '
            "verified · v{}</div>".format(__version__),
            unsafe_allow_html=True,
        )

    return {
        "categories": categories,
        "country_filter": country_filter,
        "hunter_key": hunter_key,
        "discover_patterns": discover_patterns,
        "use_email_finder": use_email_finder,
        "email_pattern": email_pattern,
        "require_company": require_company,
    }


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #
def main() -> None:
    _inject_theme()
    _hide_host_chrome()
    options = _render_sidebar()

    st.markdown(
        '<div class="brand"><span class="a">contact</span>'
        '<span class="b">finder</span></div>',
        unsafe_allow_html=True,
    )

    with st.form("search_panel"):
        left, middle, right = st.columns(3)
        company = left.text_input("Company", placeholder="i.e. Spotify, Volvo…")
        domain = middle.text_input("Domain", placeholder="i.e. spotify.com")
        country = right.text_input("Country", placeholder="i.e. Sweden, United Kingdom…")
        submitted = st.form_submit_button("Find contacts", type="primary")

    if not submitted:
        st.markdown(
            '<div class="empty-state">Enter a company to find its executives.</div>',
            unsafe_allow_html=True,
        )
        return

    if not company.strip():
        st.error("Company is required.")
        return

    if not options["categories"]:
        st.error("Select at least one role in the sidebar.")
        return

    # A domain typed into the company field poisons every query, so recover it.
    resolved_company, resolved_input_domain = split_company_input(company, domain)
    if resolved_company.lower() != company.strip().lower():
        st.info("Searching for **{}** — that looked like a domain.".format(
            resolved_company
        ))

    status = st.empty()
    bar = st.progress(0.0)

    def progress(message: str, fraction: float) -> None:
        status.caption(message)
        bar.progress(min(max(fraction, 0.0), 1.0))

    try:
        records, report = _cached_search(
            company.strip(),
            domain.strip(),
            country.strip(),
            tuple(options["categories"]),
            options["email_pattern"],
            MAX_PER_CATEGORY,
            options["require_company"],
            bool(api_key("serper") or api_key("brave")),
            options["country_filter"],
            options["hunter_key"],
            options["use_email_finder"],
            options["discover_patterns"],
            _progress=progress,
        )
    except SearchError as exc:
        st.error("Blocked by the search providers — none returned results.")
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
            st.error("Blocked by the search providers — they answered with a "
                     "bot challenge rather than results.")
            _render_api_key_help()
        elif not report.raw_results:
            st.warning("No results for these queries. Try a broader company "
                       "name or drop the country.")
        else:
            st.warning(
                "Found {} results, but none survived filtering.".format(
                    report.raw_results
                )
            )
        _render_diagnostics(report, expanded=True)
        return

    frame = pd.DataFrame(records, columns=COLUMNS)
    resolved_domain = normalise_domain(resolved_input_domain, resolved_company)

    summary = "{} contacts · {} verified · {}".format(
        len(frame), report.emails_observed, resolved_domain or "no domain"
    )
    if report.dropped_wrong_country:
        summary += " · {} filtered by country".format(report.dropped_wrong_country)

    st.markdown('<div class="section-title">Results</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="section-sub">{}</div>'.format(summary), unsafe_allow_html=True
    )

    st.dataframe(
        frame[DISPLAY_COLUMNS],
        use_container_width=True,
        hide_index=True,
        column_config={
            "LinkedIn Profile": st.column_config.LinkColumn(
                "LinkedIn Profile", display_text="Open"
            ),
            "Designation": st.column_config.TextColumn("Title", width="large"),
            "Email": st.column_config.TextColumn(
                "Email",
                width="medium",
                help="Addresses are predicted unless the summary above counts "
                     "them as verified. The CSV records the source of each one.",
            ),
        },
    )

    st.download_button(
        "Download CSV",
        frame.to_csv(index=False).encode("utf-8"),
        file_name="{}_contacts.csv".format(
            resolved_company.lower().replace(" ", "_")
        ),
        mime="text/csv",
    )

    _render_diagnostics(report)


if __name__ == "__main__":
    main()
