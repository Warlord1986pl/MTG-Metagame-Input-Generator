"""Offline tests: an event mis-dated on the mtgo.com month listing never reaches the wrong window.

Regression for 12855474 (C64, 2026-10-03): on 2026-10-07 the listing showed it as 2026-10-05, so
the 2026-10-05 window wrote all 32 of its rows into challenge_history_modern.csv a second time
(dated 2026-10-05), and league-rebuild failed its per-event points invariant (62 != 31) from then
on. The event's own JSON (site_name/starttime) still said 2026-10-03.

Run directly: python tests/test_misdated_event.py
"""
from __future__ import annotations

import sys
import tempfile
from contextlib import ExitStack, contextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import challenge_mtgo_source  # noqa: E402
from challenge_history_engine import HISTORY_COLS, sync_challenge_history_window  # noqa: E402
from challenge_mtgo_source import MtgoRegistryEvent, build_challenge_dataset  # noqa: E402

EVENT_ID = "12855474"
ROSTER = [("2397690", "416FrowningTable"), ("2522150", "Slasher21"), ("3082695", "SureThing"), ("3155916", "reidq7")]


@contextmanager
def _patched(obj, name, replacement):
    original = getattr(obj, name)
    setattr(obj, name, replacement)
    try:
        yield
    finally:
        setattr(obj, name, original)


def _blob(real_date: str) -> dict:
    return {
        "event_id": EVENT_ID,
        "site_name": f"modern-challenge-64-{real_date}{EVENT_ID}",
        "starttime": f"{real_date} 01:00:00.0",
        "decklists": [
            {"loginid": lid, "player": name, "main_deck": [{"qty": 4, "card_attributes": {"card_name": f"Card {i}"}}]}
            for i, (lid, name) in enumerate(ROSTER)
        ],
        "final_rank": [{"loginid": lid, "rank": str(i + 1)} for i, (lid, _n) in enumerate(ROSTER)],
    }


def _history_row(event_id: str, day: str, place: int, pilot: str, loginid: str) -> dict:
    return {
        "EventDate": day, "Format": "Modern", "Tier": "64", "PlayerCount": "72",
        "EventSlug": "modern-challenge-64", "EventID": event_id, "Place": str(place), "Deck": "Tron",
        "DeckGuess": "", "Archetype": "Ramp", "Pilot": pilot, "LoginID": loginid,
    }


def _write_history(rows) -> Path:
    path = Path(tempfile.mkdtemp()) / "challenge_history_modern.csv"
    pd.DataFrame(rows, columns=HISTORY_COLS).to_csv(path, index=False, encoding="utf-8-sig")
    return path


def _run(listed_date: str, real_date: str, window, history_csv=None):
    event = MtgoRegistryEvent(
        event_id=EVENT_ID, date=listed_date, slug="modern-challenge-64", kind="challenge", size=64,
        url=f"https://www.mtgo.com/decklist/modern-challenge-64-{listed_date}{EVENT_ID}",
    )
    mg_df = pd.DataFrame({
        "Place": list(range(1, len(ROSTER) + 1)),
        "Pilot": [name for _lid, name in ROSTER],
        "Deck": ["Prowess"] * len(ROSTER),
    })
    logs = []
    with ExitStack() as stack:
        stack.enter_context(_patched(challenge_mtgo_source, "build_mtgo_registry", lambda *a, **k: [event]))
        stack.enter_context(_patched(challenge_mtgo_source, "fetch_mtgo_event_json", lambda *a, **k: _blob(real_date)))
        stack.enter_context(_patched(challenge_mtgo_source, "_parse_single_challenge", lambda *a, **k: ({}, mg_df)))
        dataset = build_challenge_dataset(
            "Modern", window[0], window[1], aliases=[], rules=[], challenge_mappings=[],
            cache_dir=Path(tempfile.mkdtemp()), log=logs.append, challenge_history_csv=history_csv,
        )
    return dataset, logs


def test_already_persisted_event_listed_later_is_dropped() -> None:
    history = _write_history([_history_row(EVENT_ID, "2026-10-03", 1, "416FrowningTable", "2397690")])
    dataset, logs = _run("2026-10-05", "2026-10-03", (date(2026, 10, 5), date(2026, 10, 5)), history)
    assert dataset.challenge_events == []
    assert EVENT_ID not in dataset.event_rows
    assert dataset.skipped_events == [], dataset.skipped_events
    assert any("listing says 2026-10-05" in line and "2026-10-03" in line for line in logs), logs


def test_unpersisted_event_listed_later_is_quarantined() -> None:
    history = _write_history([_history_row("99999999", "2026-10-03", 1, "x", "1")])
    dataset, _logs = _run("2026-10-05", "2026-10-03", (date(2026, 10, 5), date(2026, 10, 5)), history)
    assert dataset.challenge_events == []
    assert [(s.event_id, s.status) for s in dataset.skipped_events] == [(EVENT_ID, "ERROR")]


def test_event_listed_earlier_waits_for_its_own_window() -> None:
    # 12854940: listed as 2026-09-27, really 2026-09-28 -- not this window's, and not lost either.
    dataset, _logs = _run("2026-10-05", "2026-10-06", (date(2026, 10, 5), date(2026, 10, 5)))
    assert dataset.challenge_events == []
    assert dataset.skipped_events == [], dataset.skipped_events


def test_misdated_event_inside_window_gets_json_date() -> None:
    dataset, _logs = _run("2026-10-05", "2026-10-06", (date(2026, 10, 5), date(2026, 10, 6)))
    assert [(c.event_id, c.date) for c in dataset.challenge_events] == [(EVENT_ID, "2026-10-06")]


def test_correctly_dated_event_unchanged() -> None:
    dataset, logs = _run("2026-10-03", "2026-10-03", (date(2026, 10, 3), date(2026, 10, 3)))
    assert [(c.event_id, c.date) for c in dataset.challenge_events] == [(EVENT_ID, "2026-10-03")]
    assert not any("listing says" in line for line in logs), logs


def _sync_dataset(day: str):
    event = SimpleNamespace(event_id=EVENT_ID, date=day, size=64, slug="modern-challenge-64", player_count=72)
    rows = [
        SimpleNamespace(place=i + 1, deck="Tron", archetype="Ramp", pilot=name, loginid=lid, deck_guess="")
        for i, (lid, name) in enumerate(ROSTER)
    ]
    return SimpleNamespace(challenge_events=[event], event_rows={EVENT_ID: rows})


def test_history_sync_refuses_event_persisted_under_another_date() -> None:
    history = _write_history([_history_row(EVENT_ID, "2026-10-03", i + 1, n, lid) for i, (lid, n) in enumerate(ROSTER)])
    before = history.read_bytes()
    try:
        sync_challenge_history_window(history, "Modern", date(2026, 10, 5), date(2026, 10, 5), _sync_dataset("2026-10-05"))
    except ValueError as exc:
        assert EVENT_ID in str(exc) and "2026-10-03" in str(exc), exc
    else:
        raise AssertionError("expected ValueError for an EventID already persisted under another date")
    assert history.read_bytes() == before


def test_history_resync_of_same_window_still_allowed() -> None:
    history = _write_history([_history_row(EVENT_ID, "2026-10-03", i + 1, n, lid) for i, (lid, n) in enumerate(ROSTER)])
    written = sync_challenge_history_window(history, "Modern", date(2026, 10, 3), date(2026, 10, 3), _sync_dataset("2026-10-03"))
    assert written == len(ROSTER)
    out = pd.read_csv(history, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    assert (out["EventID"] == EVENT_ID).sum() == len(ROSTER), out


if __name__ == "__main__":
    test_already_persisted_event_listed_later_is_dropped()
    test_unpersisted_event_listed_later_is_quarantined()
    test_event_listed_earlier_waits_for_its_own_window()
    test_misdated_event_inside_window_gets_json_date()
    test_correctly_dated_event_unchanged()
    test_history_sync_refuses_event_persisted_under_another_date()
    test_history_resync_of_same_window_still_allowed()
    print("All misdated-event tests passed.")
