#!/usr/bin/env python3
"""Write STATUS.md: what was verified, when, and against which versions.

Run weekly by CI. The file carries real information — a green suite on a
given date against named dependency versions — so the commit that lands it
says something, rather than being an empty ping.
"""

from __future__ import annotations

import datetime as dt
import importlib.metadata as md
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TRACKED = ("streamlit", "pandas", "requests", "beautifulsoup4", "lxml", "pytest")


def _version(package: str) -> str:
    try:
        return md.version(package)
    except Exception:
        return "not installed"


def _run_tests() -> tuple:
    """Return ``(passed, summary)`` for the suite."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q"],
        cwd=ROOT, capture_output=True, text=True,
    )
    tail = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    summary = tail[-1].strip() if tail else "no output"
    # Drop pytest's timing so an unchanged result produces an unchanged line.
    summary = re.sub(r"\s+in\s+[\d.]+s\b", "", summary)
    return proc.returncode == 0, summary


def build_report() -> str:
    passed, summary = _run_tests()
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    versions = "\n".join(
        "| {} | {} |".format(name, _version(name)) for name in TRACKED
    )
    return """# Status

Written automatically by the weekly CI run. It records that the suite was
exercised against the dependency versions resolved on that date — useful for a
project whose failures arrive from upstream releases rather than from commits
here.

- **Last verified:** {today}
- **Test suite:** {icon} {summary}
- **Python:** {python}

| Dependency | Version resolved |
| :--- | :--- |
{versions}
""".format(
        today=today,
        icon="passing —" if passed else "FAILING —",
        summary=summary,
        python=".".join(str(p) for p in sys.version_info[:3]),
        versions=versions,
    )


def main() -> int:
    report = build_report()
    (ROOT / "STATUS.md").write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
