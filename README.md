# 🎯 Executive Contact Finder

A Streamlit application for recruitment leads, executive talent scouts and sales
professionals. Given a target company and region it discovers executive names,
exact job titles, predicted corporate email addresses and direct LinkedIn
profile links.

## Quick start

```bash
pip install -r requirements.txt
streamlit run app.py
```

The app opens on <http://localhost:8501>.

## Using it

The top control panel takes three inputs:

| Input | Required | Notes |
| :--- | :--- | :--- |
| **Company Name** | Yes | The target organisation — `Volvo`, `Spotify`, `IKEA` |
| **Company Domain** | No | `volvocars.com`. Defaults to `companyname.com` when blank |
| **Country** | No | `Sweden`, `United Kingdom`, `United States` |

Press **Extract Real Executive Contacts** to render the output matrix:

| Column | Description | Example |
| :--- | :--- | :--- |
| Full Name | Human name parsed from the profile header | `Jim Rowan` |
| Designation | Exact corporate job title | `Chief Executive Officer at Volvo Cars` |
| Email | Real address where one can be found, otherwise a guess | `jrowan@volvocars.com` |
| Email Source | Provenance of that address | `Verified · 97%` |
| Country | Specified region filter | `Sweden` |
| Category | Departmental role classification | `CEO / Executive` |
| LinkedIn Profile | Link to the individual's profile | `Open Profile` |

**Never treat the Email column as uniformly reliable — read Email Source.**
`Verified` and `Found` are addresses Hunter actually observed; `Guess` rows are
derived from a naming pattern and are not confirmed to exist.

Sidebar settings tune the run: which role categories to search, how many results
to keep per category, how strictly to enforce the country, the email lookup key,
and a stricter filter that drops results which never name the company.

Results are cached for one hour per input combination, and the full matrix can
be exported with **Download CSV**.

## Targeted role matrix

Five decision-maker categories, each searched with its own expanded keyword set
(`executive_finder/roles.py`):

| Category | Keywords |
| :--- | :--- |
| CEO / Executive | CEO, Chief Executive Officer, Managing Director, President, Country Head |
| Design Director | Design Director, Head of Design, VP of Design, Vice President Design |
| UX Director | UX Director, Head of UX, Director of User Experience, VP UX |
| Product Design Director | Product Design Director, Head of Product Design, VP Product Design |
| Head of HR / People | Chief People Officer, Head of HR, VP HR, HR Director |

## Architecture

```
app.py                        Streamlit UI: control panel, matrix, CSV export
executive_finder/
├── roles.py                  Role matrix + departmental classification
├── search.py                 X-Ray query building, HTTP fetching, SERP parsing
├── parsing.py                Title & link unpackaging
├── emails.py                 Name sanitisation and email generation
├── enrichment.py             Real email lookup via Hunter.io
├── patterns.py               Free pattern inference from published addresses
├── geo.py                    Country matching (locale, country and city evidence)
└── pipeline.py               Orchestration, filtering, de-duplication
tests/                        Unit tests (pytest)
```

### Search & parsing pipeline

1. **Targeted X-Ray querying** — one query per role category, OR-ing the
   category's keywords so a full sweep costs five requests rather than twenty:

   ```
   site:linkedin.com/in/ "Spotify" ("CEO" OR "Chief Executive Officer" …) "Sweden"
   ```

2. **User-Agent spoofing** — requests carry rotating desktop browser headers
   (`Mozilla/5.0 …`) plus `Accept-Language` and `Sec-Fetch-*`, which keeps search
   front-ends from serving the stripped-down page they give obvious bots.
   Requests are paced with a configurable pause between categories.

3. **Title & link unpackaging** — result nodes are extracted, tracking redirects
   are stripped (`//duckduckgo.com/l/?uddg=…` → the real URL), profile URLs are
   canonicalised (`se.linkedin.com/in/x/?trk=…` → `www.linkedin.com/in/x`), and
   headers are split into name and designation. Listing pages, company pages and
   post excerpts are rejected by a name-plausibility check.

4. **Country filtering** — the country in the query is only a ranking hint, so
   results from other markets leak through. A positive check runs on each
   result: the LinkedIn locale subdomain (`se.linkedin.com` → Sweden), the
   country named in the text (including endonyms like *Sverige*), or a known
   city of that country. Read `geo.py`.

5. **Email resolution** — see below.

Providers are tried in order — Serper and Brave first when an API key is
configured, then the scraped front-ends (DuckDuckGo HTML, DuckDuckGo Lite, Bing,
Mojeek) — and the first one returning usable rows wins.

A provider answering HTTP 200 with a bot-challenge page is recorded as
**blocked**, not **empty**. Without that distinction a block is indistinguishable
from a genuine zero-result search, and the UI reports "nothing matched" when the
truth is "we were refused."

## Tests

```bash
python -m pytest tests -q
```

156 tests cover query construction, redirect unwrapping (including Bing's
base64 `/ck/a` wrapper), SERP parsing, block detection, title unpackaging, name
sanitisation, email patterns, Hunter enrichment, country matching, role
classification, free pattern inference and the end-to-end pipeline (with the
network stubbed).

## Deployment — Streamlit Community Cloud

1. Push this repository to GitHub.
2. On <https://share.streamlit.io> choose **New app**, select the repository and
   branch, and set the main file to `app.py`.
3. Deploy. `requirements.txt` is picked up automatically.
4. **Add a search API key** (see below) — without one, a hosted deployment will
   almost certainly return no results.

### Hosted deployments need an API key

Scraping search-engine HTML works from a home connection but is blocked from
shared datacenter IPs, which is what Streamlit Community Cloud runs on. Every
app on that host shares the same outbound addresses, so search engines answer
with a bot challenge instead of results.

Keys are resolved from two layers on every run:

1. **The deployment** — Streamlit Secrets, falling back to the process
   environment. Set once, applies to every visitor and every session.
2. **The sidebar** — a per-session override, useful for trying a key without
   redeploying. Held in session state only: never written to disk, never
   logged, gone when the tab closes.

The sidebar wins while it has a value; clear it and the deployment key resumes.
The sidebar states which layer is in force, so "am I using the stored key?" is
never a guess.

### Storing the key in the deployment (recommended)

On Streamlit Cloud, from **the app list at
[share.streamlit.io](https://share.streamlit.io)** — not from inside the running
app:

1. Sign in and find your app in the list.
2. Click the **⋮** to the right of the app's name.
3. **Settings** → **Secrets**.
4. Paste and **Save**. The app reboots itself; no redeploy needed.

```toml
SERPER_API_KEY = "your-key-here"
# optional alternatives / additions
BRAVE_API_KEY = "your-key-here"
HUNTER_API_KEY = "your-key-here"
```

The sidebar should then read *"loaded from this app's configuration"* with the
box left empty.

Running elsewhere, the same names are read from environment variables:

```bash
SERPER_API_KEY=... streamlit run app.py
```

For local runs, copy `.streamlit/secrets.toml.example` to
`.streamlit/secrets.toml` and fill it in — that path is gitignored. **Never
commit a real key**; this repository is on GitHub and a committed key stays in
the history even after deletion.

Both [serper.dev](https://serper.dev) and the
[Brave Search API](https://brave.com/search/api/) have free tiers. When a key is
present it is used first; the scraped providers stay as a fallback. Locally,
`streamlit run app.py` works without a key at all.

## Real email addresses

Addresses are resolved in four tiers, best first. The **Email Source** column
always states which tier produced a given address.

| Tier | Source | Needs |
| :--- | :--- | :--- |
| 1 | `Verified` — Hunter has this exact address | Hunter key |
| 2 | `Found` — per-person resolution | Hunter key, opt-in |
| 3 | `Guess · company pattern` — Hunter reported the company's shape | Hunter key |
| 4 | `Guess · inferred pattern` — mined from public addresses, free | nothing |
| 5 | `Guess · default pattern` — nothing better was available | nothing |

### Free pattern discovery (no API key)

The single most valuable fact is the company's *shape*: knowing Volvo Cars uses
`{f}{last}` fixes every row at once, whereas assuming `first.last` gets every
row wrong. That can usually be recovered without paying anyone.

One extra search per run (`"@volvocars.com"`) harvests addresses the company has
published — press pages, job ads, `mailto:` links — and votes on the shape:

* **Direct evidence.** If a harvested address is reproduced by applying a
  candidate template to an executive we already found, that is proof. It is the
  only way to tell `jrowan` (`{f}{last}`) from `jimr` (`{first}{l}`).
* **Structural evidence.** Failing that, separators still distinguish
  `jim.rowan` from `j.rowan`.
* Shared mailboxes (`info@`, `press@`, `careers@`, and suffixed variants like
  `info-uk@`) are excluded — they carry no naming information.

This is inference, not verification: a company with several patterns or
addresses on acquired domains will defeat it. The row stays labelled a guess.

Toggle it in the sidebar under **Email lookup**.

### Hunter.io (paid tiers give observed addresses)

Without a Hunter.io key every address is a **pattern guess**, and says nothing
about whether the mailbox exists.

Add a [Hunter.io](https://hunter.io) key (sidebar → **Email lookup**, or
`HUNTER_API_KEY` in Secrets) and the app resolves addresses instead:

1. **One `domain-search` call per run** returns the company's *observed* pattern
   plus every address Hunter already holds for that domain. Executives in that
   set get their real address outright, tagged `Verified`.
2. **Everyone else is guessed with the company's own pattern** rather than an
   assumed one — for Volvo Cars that is `{f}{last}`, so `aberg@volvocars.com`,
   not `anna.berg@volvocars.com`.
3. **Optional per-person lookup** (`Look up each person individually`) resolves
   the remainder via `email-finder`. It costs one Hunter credit per person, so
   it is off by default.

Hunter's free tier is small (check their pricing page for the current figure)
and step 1 spends one search per run, so free pattern discovery above is what
keeps the app useful without a subscription.

## Troubleshooting

The **Run diagnostics** panel under every result set shows exactly what each
provider did and where rows were dropped. The app distinguishes four outcomes:

| Message | Meaning |
| :--- | :--- |
| Blocked by the search providers | Providers answered with a challenge page. Add an API key. |
| Providers returned no results at all | The queries genuinely matched nothing. Broaden the company name or drop the country filter. |
| Found N results, none survived filtering | Results came back but were company pages, listicles, or off-target roles. |
| Contacts found | Working normally. |

Putting a **domain in the Company Name field** (`Spotify.com` rather than
`Spotify`) breaks every query, since no LinkedIn headline contains that string.
The app detects this, splits it into a name and a mail domain, and tells you it
did so — but typing the plain company name is better.

## Accuracy and responsible use

- **Emails are predictions, not verified addresses.** They are derived from
  public naming conventions and should be confirmed before outreach.
- Results depend on what search engines have indexed; a company with little
  public LinkedIn presence in a region will return few or no rows.
- The data returned is personal data. Using it for recruitment or sales outreach
  puts you under GDPR (in the EU/UK), CAN-SPAM and equivalent local rules —
  honour opt-outs and keep a lawful basis for processing.
- Respect the terms of service of the sites involved, and keep request volume
  modest; the pacing defaults exist for that reason.
