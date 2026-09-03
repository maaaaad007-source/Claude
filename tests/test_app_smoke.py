"""End-to-end smoke test of the Streamlit script itself.

The rest of the suite exercises the pipeline; nothing ran ``app.py``, so a
whole class of failure was invisible to it — most recently a progress callback
drawing to the page from inside a cached function, which Streamlit cannot
replay on a cache hit. That surfaced only in the deployed app.

These tests run the real script through Streamlit's own test harness, twice, so
the second run exercises the cache-hit path where such errors appear.
"""

import pathlib

import pytest

pytest.importorskip("streamlit.testing.v1")

from streamlit.testing.v1 import AppTest  # noqa: E402

from executive_finder import pipeline  # noqa: E402
from executive_finder.search import ProviderOutcome, SearchResult  # noqa: E402

APP = str(pathlib.Path(__file__).resolve().parent.parent / "app.py")
TIMEOUT = 60

ROWS = [
    SearchResult(
        "Daniel Ek - Chief Executive Officer - Spotify | LinkedIn",
        "https://se.linkedin.com/in/daniel-ek", "Spotify · Stockholm, Sweden"),
    SearchResult(
        "Anna Berg - Head of Design - Spotify | LinkedIn",
        "https://se.linkedin.com/in/anna-berg", "Spotify · Stockholm, Sweden"),
]


@pytest.fixture(autouse=True)
def stub_network(monkeypatch):
    """No test may reach the network; the app must still run end to end."""
    def fake(query, session=None, timeout=15.0, pause=1.0):
        if query.startswith('"@'):
            return [], [ProviderOutcome("stub", "empty")]
        return ROWS, [ProviderOutcome("stub", "ok", rows=len(ROWS))]

    monkeypatch.setattr(pipeline, "search_detailed", fake)
    monkeypatch.setattr(pipeline.time, "sleep", lambda *_: None)


def _fill_and_submit(at, company="Spotify"):
    at.text_input[0].set_value(company)          # Company
    at.text_input[1].set_value("spotify.com")    # Domain
    at.button[0].click()
    return at.run(timeout=TIMEOUT)


def test_the_app_renders_without_exceptions():
    at = AppTest.from_file(APP, default_timeout=TIMEOUT).run()
    assert not at.exception, [str(e) for e in at.exception]


def test_a_role_sweep_runs_and_reruns_without_exceptions():
    """The second run is the cache-hit path, where replay errors surface."""
    at = AppTest.from_file(APP, default_timeout=TIMEOUT).run()
    at = _fill_and_submit(at)
    assert not at.exception, [str(e) for e in at.exception]

    at = _fill_and_submit(at)
    assert not at.exception, [str(e) for e in at.exception]


def test_the_cached_search_draws_nothing_to_the_page():
    """A cached function that renders cannot be replayed; keep it pure.

    Checked structurally rather than by running it, so the rule is enforced
    even for branches this suite does not reach.
    """
    import ast
    import inspect
    import textwrap

    import app

    source = textwrap.dedent(inspect.getsource(app._cached_search.__wrapped__))
    fn = ast.parse(source).body[0]
    fn.decorator_list = []          # @st.cache_data is not a draw call
    drawn = [
        node.func.value.id + "." + node.func.attr
        for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "st"
    ]
    assert drawn == [], "cached function draws to the page: {}".format(drawn)
