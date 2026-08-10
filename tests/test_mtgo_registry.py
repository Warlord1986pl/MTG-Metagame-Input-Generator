"""Registry tests for challenge_mtgo_source.build_mtgo_registry.

By default this suite runs entirely OFFLINE against frozen mtgo.com page fixtures
(tests/fixtures/mtgo_decklists_2026-07.html, mtgo_decklists_2026-08.html), captured live on
2026-08-10 via _fetch_month_page_with_content_check() itself (so the fixtures are already proven
non-truncated by the same two guards that protect production). _fetch_text_retry is monkeypatched
to serve these fixtures by URL, and `today` is pinned to 2026-08-10 (the capture date) so the
current-month truncation-recency check stays deterministic regardless of when the suite actually
runs -- freezing "today" is required because build_mtgo_registry's truncation guard is explicitly
time-relative for the current month, by design (see challenge_mtgo_source.py).

Exactly one test hits the real network: test_live_current_month_not_truncated, marked
@pytest.mark.live and excluded from the default run (see pytest.ini: `addopts = -m "not live"`).
Run it explicitly with: pytest -m live tests/test_mtgo_registry.py

Frozen ground truth for the 2026-07-27..2026-08-09 Modern Challenge window (manually reconciled
against mtgo.com on 2026-08-10): 23 challenge events (15xC64, 5xC32, 3xC96), 0 premier events.
Only 12 of those 23 had made it into outputs/challenge_history_modern.csv at the time this bug was
found -- the 11 listed in EXPECTED_MISSING_IDS below never got synced (2026-08-03..08-09, spanning
a hard cutoff at the window end).

Run directly: python tests/test_mtgo_registry.py (runs the 4 offline fixture tests only)
Or via pytest: pytest tests/test_mtgo_registry.py
"""
from __future__ import annotations

import sys
import tempfile
from contextlib import contextmanager
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import challenge_mtgo_source  # noqa: E402
from challenge_mtgo_source import build_mtgo_registry, _fetch_month_page_with_content_check  # noqa: E402

try:
    import pytest
except ImportError:  # pragma: no cover -- pytest is not a hard project dependency (see requirements.txt)
    class _NoOpMark:
        def __getattr__(self, _name):
            def _decorator(func):
                return func
            return _decorator

    class _PytestStub:
        mark = _NoOpMark()

    pytest = _PytestStub()  # type: ignore[assignment]

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
FIXTURE_CAPTURE_DATE = date(2026, 8, 10)  # "today" pinned to when the fixtures below were captured

W32_START = date(2026, 7, 27)
W32_END = date(2026, 8, 9)

EXPECTED_W32_REGISTRY_COUNT = 23
EXPECTED_W32_TIER_COUNTS = {64: 15, 32: 5, 96: 3}
EXPECTED_MISSING_IDS = {
    "12849488", "12849492", "12849507", "12849509", "12850696",
    "12850868", "12850813", "12850822", "12851142", "12851104", "12851108",
}

SAME_DAY_PAIR_DATE = "2026-08-08"
SAME_DAY_PAIR_IDS = {"12851142", "12850822"}

WIDER_JULY_START = date(2026, 7, 13)
WIDER_JULY_END = date(2026, 7, 26)
EXPECTED_MODERN_PREMIER_IDS = {"12847670", "12848159"}

NON_MODERN_WINDOW_START = date(2026, 7, 27)
NON_MODERN_WINDOW_END = date(2026, 8, 9)
NON_MODERN_PREMIER_IDS = {"12849423", "12849424"}  # standard-showcase-qualifier, legacy-showcase-qualifier


@contextmanager
def _fixture_backed_registry():
    """Monkeypatch _fetch_text_retry to serve the frozen mtgo.com fixtures instead of the network."""
    fixture_by_month = {
        "2026/07": (FIXTURES_DIR / "mtgo_decklists_2026-07.html").read_text(encoding="utf-8"),
        "2026/08": (FIXTURES_DIR / "mtgo_decklists_2026-08.html").read_text(encoding="utf-8"),
    }

    def stub_fetch(url: str, timeout: int = 45, attempts: int = 5, log=None) -> str:
        for key, html in fixture_by_month.items():
            if url.endswith(key):
                return html
        raise AssertionError(f"no fixture registered for URL: {url}")

    original = challenge_mtgo_source._fetch_text_retry
    challenge_mtgo_source._fetch_text_retry = stub_fetch
    try:
        yield
    finally:
        challenge_mtgo_source._fetch_text_retry = original


def _fixture_registry(start: date, end: date):
    with _fixture_backed_registry():
        tmp_cache = Path(tempfile.mkdtemp(prefix="mtgo_registry_test_cache_")) / "counts.json"
        return build_mtgo_registry(
            "Modern", start, end, count_cache_path=tmp_cache, today=FIXTURE_CAPTURE_DATE
        )


def test_w32_registry() -> None:
    registry = _fixture_registry(W32_START, W32_END)
    challenge = [r for r in registry if r.kind == "challenge"]

    assert len(challenge) == EXPECTED_W32_REGISTRY_COUNT, (
        f"registry_count={len(challenge)}, expected {EXPECTED_W32_REGISTRY_COUNT}"
    )
    tier_counts: dict = {}
    for r in challenge:
        tier_counts[r.size] = tier_counts.get(r.size, 0) + 1
    assert tier_counts == EXPECTED_W32_TIER_COUNTS, f"tier_counts={tier_counts}, expected {EXPECTED_W32_TIER_COUNTS}"

    found_ids = {r.event_id for r in challenge}
    missing_still_missing = EXPECTED_MISSING_IDS - found_ids
    assert not missing_still_missing, f"registry parser failed to find: {missing_still_missing}"


def test_month_boundary() -> None:
    registry = _fixture_registry(W32_START, W32_END)
    challenge = [r for r in registry if r.kind == "challenge"]
    months = {r.date[:7] for r in challenge}
    assert "2026-07" in months, f"no July events found in a window spanning the month boundary: {months}"
    assert "2026-08" in months, f"no August events found in a window spanning the month boundary: {months}"


def test_same_day_pair() -> None:
    registry = _fixture_registry(W32_START, W32_END)
    challenge = [r for r in registry if r.kind == "challenge"]
    same_day = [r for r in challenge if r.date == SAME_DAY_PAIR_DATE and r.size == 64]
    same_day_ids = {r.event_id for r in same_day}
    assert SAME_DAY_PAIR_IDS.issubset(same_day_ids), (
        f"expected both {SAME_DAY_PAIR_IDS} on {SAME_DAY_PAIR_DATE} as distinct C64 events, got {same_day_ids}"
    )
    assert len(same_day) == len({r.event_id for r in same_day}), "same-day same-tier events were collapsed"


def test_premier_modern_only() -> None:
    # 1) Real Modern premier events inside a window ARE captured.
    wide_registry = _fixture_registry(WIDER_JULY_START, WIDER_JULY_END)
    wide_premier_ids = {r.event_id for r in wide_registry if r.kind == "premier"}
    assert EXPECTED_MODERN_PREMIER_IDS.issubset(wide_premier_ids), (
        f"expected Modern premier events {EXPECTED_MODERN_PREMIER_IDS} to be captured, got {wide_premier_ids}"
    )

    # 2) Non-Modern premier events in the SAME date range never leak into the Modern registry, and
    #    registry_count (challenge events) is unaffected by their presence on mtgo.com.
    registry = _fixture_registry(NON_MODERN_WINDOW_START, NON_MODERN_WINDOW_END)
    all_ids = {r.event_id for r in registry}
    leaked = NON_MODERN_PREMIER_IDS & all_ids
    assert not leaked, f"non-Modern premier event(s) leaked into the Modern registry: {leaked}"

    challenge = [r for r in registry if r.kind == "challenge"]
    assert len(challenge) == EXPECTED_W32_REGISTRY_COUNT, (
        "non-Modern premier events on mtgo.com must never affect the Modern challenge registry_count"
    )


@pytest.mark.live
def test_live_current_month_not_truncated() -> None:
    """The one test allowed to touch the real network: fetches the CURRENT month from mtgo.com and
    asserts the page passes both truncation guards (zero-link check and tail-coverage check) --
    i.e. mtgo.com is currently serving a real, non-truncated page. Does not assert anything about
    which events exist (that would make the test itself non-deterministic); only that the fetch
    layer's own correctness guard is satisfied. A flaky network here must never fail the default
    suite -- run with: pytest -m live tests/test_mtgo_registry.py
    """
    today = date.today()
    url = f"https://www.mtgo.com/decklists/{today.year}/{today.month:02d}"
    tmp_cache = Path(tempfile.mkdtemp(prefix="mtgo_registry_live_test_cache_")) / "counts.json"
    html = _fetch_month_page_with_content_check(url, today.year, today.month, count_cache_path=tmp_cache)
    assert 'href="/decklist/' in html, "expected at least one decklist link on the current month's page"


if __name__ == "__main__":
    test_w32_registry()
    print("OK: test_w32_registry")
    test_month_boundary()
    print("OK: test_month_boundary")
    test_same_day_pair()
    print("OK: test_same_day_pair")
    test_premier_modern_only()
    print("OK: test_premier_modern_only")
    print("All offline mtgo registry tests passed. (test_live_current_month_not_truncated skipped -- run via `pytest -m live`.)")
