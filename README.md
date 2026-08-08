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
| Full Name | Human name parsed from the profile header | `Daniel Ek` |
| Designation | Exact corporate job title | `Chief Executive Officer at Spotify` |
| Estimated Email | Formatted corporate email | `daniel.ek@spotify.com` |
| Country | Specified region filter | `Sweden` |
| Category | Departmental role classification | `CEO / Executive` |
| LinkedIn Profile | Link to the individual's profile | `Open Profile` |

Sidebar settings tune the run: which role categories to search, how many results
to keep per category, the email pattern (`first.last`, `flast`, `f.last`, …) and
a stricter filter that drops results which never name the company.

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

4. **Email generation** — names are folded to ASCII (`Ödegård` → `odegard`,
   `Kjell-Åke` → `kjell ake`), nobiliary particles are dropped
   (`Jan van der Berg` → `jan.berg`), and the address is formatted with the
   selected corporate pattern.

Two providers are tried in order (DuckDuckGo's HTML endpoint, then Bing); the
first one returning usable rows wins. A `SearchError` surfaces in the UI only
when every provider fails.

## Tests

```bash
python -m pytest tests -q
```

51 tests cover query construction, redirect unwrapping, SERP parsing, title
unpackaging, name sanitisation, email patterns, role classification and the
end-to-end pipeline (with the network stubbed).

## Deployment — Streamlit Community Cloud

1. Push this repository to GitHub.
2. On <https://share.streamlit.io> choose **New app**, select the repository and
   branch, and set the main file to `app.py`.
3. Deploy. `requirements.txt` is picked up automatically; no secrets are needed.

Shared cloud IPs are rate-limited by search engines more aggressively than a
home connection, so expect emptier result sets there than when running locally.

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
