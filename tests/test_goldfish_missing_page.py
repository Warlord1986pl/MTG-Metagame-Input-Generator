"""Offline tests: an event MTGGoldfish never published no longer crashes the whole window.

Regression for 12856761 (C32, 2026-10-16): mtgo.com had the event, MTGGoldfish had no page for it
(its two 10-17 C32 pages were 12856762 and 12856594), and the 404 aborted every hourly run.

Run directly: python tests/test_goldfish_missing_page.py
"""
from __future__ import annotations

import sys
import tempfile
from contextlib import ExitStack, contextmanager
from datetime import date, timedelta
from pathlib import Path
from urllib.error import HTTPError

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import challenge_mtgo_source  # noqa: E402
from challenge_mtgo_source import MtgoRegistryEvent, build_challenge_dataset  # noqa: E402

EVENT_ID = "12856761"
ROSTER = [("101", "levunga21"), ("102", "_Elfkid_"), ("103", "sioutis_kk"), ("104", "Bosseidon")]


@contextmanager
def _patched(obj, name, replacement):
    original = getattr(obj, name)
    setattr(obj, name, replacement)
    try:
        yield
    finally:
        setattr(obj, name, original)


def _blob():
    return {
        "event_id": EVENT_ID,
        "decklists": [
            {"loginid": lid, "player": name, "main_deck": [{"qty": 4, "card_attributes": {"card_name": f"Card {i}"}}]}
            for i, (lid, name) in enumerate(ROSTER)
        ],
        "final_rank": [{"loginid": lid, "rank": str(i + 1)} for i, (lid, _n) in enumerate(ROSTER)],
    }


def _run(event_day: date, error_code: int = 404):
    day = event_day.isoformat()
    event = MtgoRegistryEvent(
        event_id=EVENT_ID, date=day, slug="modern-challenge-32", kind="challenge", size=32,
        url=f"https://www.mtgo.com/decklist/modern-challenge-32-{day}{EVENT_ID}",
    )

    def missing_page(*_a, **_k):
        raise HTTPError(f"https://www.mtggoldfish.com/tournament/modern-challenge-32-{day}", error_code, "Not Found", {}, None)

    logs = []
    with ExitStack() as stack:
        stack.enter_context(_patched(challenge_mtgo_source, "build_mtgo_registry", lambda *a, **k: [event]))
        stack.enter_context(_patched(challenge_mtgo_source, "fetch_mtgo_event_json", lambda *a, **k: _blob()))
        stack.enter_context(_patched(challenge_mtgo_source, "_parse_single_challenge", missing_page))
        dataset = build_challenge_dataset(
            "Modern", event_day, event_day, aliases=[], rules=[], challenge_mappings=[],
            cache_dir=Path(tempfile.mkdtemp()), log=logs.append,
        )
    return dataset, logs


def test_young_event_without_page_is_pending() -> None:
    dataset, _logs = _run(date.today())
    assert [(s.event_id, s.status) for s in dataset.skipped_events] == [(EVENT_ID, "PENDING")]
    assert EVENT_ID not in dataset.event_rows
    assert dataset.challenge_events == []


def test_old_event_without_page_is_classified_from_mtgo() -> None:
    dataset, logs = _run(date.today() - timedelta(days=3))
    assert dataset.skipped_events == []
    assert [c.event_id for c in dataset.challenge_events] == [EVENT_ID]
    got = [(r.place, r.pilot, r.loginid) for r in dataset.event_rows[EVENT_ID]]
    assert got == [(i + 1, name, lid) for i, (lid, name) in enumerate(ROSTER)], got
    assert any("no MTGGoldfish page" in line for line in logs), logs


def test_other_http_errors_still_raise() -> None:
    try:
        _run(date.today() - timedelta(days=3), error_code=503)
    except HTTPError as exc:
        assert exc.code == 503
    else:
        raise AssertionError("a non-404 MTGGoldfish error must not be swallowed")


if __name__ == "__main__":
    test_young_event_without_page_is_pending()
    test_old_event_without_page_is_classified_from_mtgo()
    test_other_http_errors_still_raise()
    print("All missing-MTGGoldfish-page tests passed.")
