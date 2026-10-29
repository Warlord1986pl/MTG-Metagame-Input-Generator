"""Offline tests: a MTGGoldfish-labeled Challenge row always carries mtgo.com's LoginID.

Regression for 12854500 (C16, 2026-09-19): MTGGoldfish still showed "Jetpool" for the account
mtgo.com lists as "Overman220" (LoginID 2111039), the old name-keyed join found nothing, and the
row reached history, the league table and the results export with an empty LoginID.

Run directly: python tests/test_labeled_event_loginid.py
"""
from __future__ import annotations

import sys
import tempfile
from contextlib import ExitStack, contextmanager
from datetime import date
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import challenge_mtgo_source  # noqa: E402
from challenge_mtgo_source import MtgoRegistryEvent, build_challenge_dataset  # noqa: E402

EVENT_ID = "12854500"
DAY = "2026-09-19"
MTGO_ROSTER = [("3298193", "bubblywubblytubbler"), ("2045296", "JV_7777"), ("2569009", "TrueHero"), ("2111039", "Overman220")]


@contextmanager
def _patched(obj, name, replacement):
    original = getattr(obj, name)
    setattr(obj, name, replacement)
    try:
        yield
    finally:
        setattr(obj, name, original)


def _blob(roster):
    return {
        "event_id": EVENT_ID,
        "decklists": [
            {"loginid": lid, "player": name, "main_deck": [{"qty": 4, "card_attributes": {"card_name": f"Card {i}"}}]}
            for i, (lid, name) in enumerate(roster)
        ],
        "final_rank": [{"loginid": lid, "rank": str(i + 1)} for i, (lid, _n) in enumerate(roster)],
    }


def _run(goldfish_pilots, roster=MTGO_ROSTER):
    event = MtgoRegistryEvent(
        event_id=EVENT_ID, date=DAY, slug="modern-challenge-16", kind="challenge", size=16,
        url=f"https://www.mtgo.com/decklist/modern-challenge-16-{DAY}{EVENT_ID}",
    )
    mg_df = pd.DataFrame({
        "Place": list(range(1, len(goldfish_pilots) + 1)),
        "Pilot": goldfish_pilots,
        "Deck": ["Prowess"] * len(goldfish_pilots),
    })
    logs = []
    with ExitStack() as stack:
        stack.enter_context(_patched(challenge_mtgo_source, "build_mtgo_registry", lambda *a, **k: [event]))
        stack.enter_context(_patched(challenge_mtgo_source, "fetch_mtgo_event_json", lambda *a, **k: _blob(roster)))
        stack.enter_context(_patched(challenge_mtgo_source, "_parse_single_challenge", lambda *a, **k: ({}, mg_df)))
        dataset = build_challenge_dataset(
            "Modern", date(2026, 9, 19), date(2026, 9, 19), aliases=[], rules=[], challenge_mappings=[],
            cache_dir=Path(tempfile.mkdtemp()), log=logs.append,
        )
    return dataset, logs


def test_renamed_account_keeps_mtgo_loginid() -> None:
    dataset, logs = _run(["bubblywubblytubbler", "JV_7777", "TrueHero", "Jetpool"])
    rows = {r.place: r for r in dataset.event_rows[EVENT_ID]}
    assert rows[4].loginid == "2111039", rows[4]
    assert rows[4].pilot == "Overman220", rows[4]
    assert all(r.loginid for r in rows.values())
    assert [s.event_id for s in dataset.skipped_events] == []
    assert any("Jetpool" in line and "Overman220" in line for line in logs), logs


def test_matching_names_unchanged() -> None:
    dataset, _logs = _run([name for _lid, name in MTGO_ROSTER])
    got = [(r.place, r.pilot, r.loginid) for r in dataset.event_rows[EVENT_ID]]
    assert got == [(i + 1, name, lid) for i, (lid, name) in enumerate(MTGO_ROSTER)], got


def test_place_missing_from_mtgo_is_quarantined() -> None:
    dataset, _logs = _run(["bubblywubblytubbler", "JV_7777", "TrueHero", "Overman220", "Ghost"])
    assert EVENT_ID not in dataset.event_rows
    assert [(s.event_id, s.status) for s in dataset.skipped_events] == [(EVENT_ID, "ERROR")]
    assert dataset.challenge_events == []


def test_misaligned_rosters_are_quarantined() -> None:
    dataset, _logs = _run(["a", "b", "c", "d"])
    assert EVENT_ID not in dataset.event_rows
    assert [(s.event_id, s.status) for s in dataset.skipped_events] == [(EVENT_ID, "ERROR")]


if __name__ == "__main__":
    test_renamed_account_keeps_mtgo_loginid()
    test_matching_names_unchanged()
    test_place_missing_from_mtgo_is_quarantined()
    test_misaligned_rosters_are_quarantined()
    print("All labeled-event LoginID tests passed.")
