"""Offline, deterministic tests for the fail-closed completeness/status.json contract in
challenge_history_engine.run_challenge_statistics() and challenge_mtgo_source.CompletenessSummary.

No live network access required -- pytest is not a project dependency (see requirements.txt), so
this uses plain functions + manual monkeypatching, runnable directly like
tests/test_challenge_stats_regression.py.

Run directly: python tests/test_challenge_completeness.py
Or via pytest (if installed): pytest tests/test_challenge_completeness.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from contextlib import contextmanager
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

import challenge_mtgo_source  # noqa: E402
from challenge_mtgo_source import CompletenessSummary, MtgoRegistryEvent, SkippedEvent  # noqa: E402
from challenge_history_engine import run_challenge_statistics  # noqa: E402
import verify_challenge_registry  # noqa: E402

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "challenge_history_ground_truth_2026-07-13_to_2026-07-26.csv"
WEEK_START = date(2026, 7, 13)
WEEK_END = date(2026, 7, 26)


@contextmanager
def _patched(obj, name, replacement):
    original = getattr(obj, name)
    setattr(obj, name, replacement)
    try:
        yield
    finally:
        setattr(obj, name, original)


def _complete_completeness() -> CompletenessSummary:
    return CompletenessSummary(
        registry_count=24, fetched_count=24, complete=True, missing=[],
        tier_counts_registry={64: 15, 32: 5, 96: 4}, tier_counts_fetched={64: 15, 32: 5, 96: 4},
        premier_checked=True, premier_count=2,
        premier_events=[("12847670", "RC Super Qualifier", "2026-07-19"), ("12848159", "Showcase Qualifier", "2026-07-25")],
        premier_note="2026-07-19 RC Super Qualifier (12847670); 2026-07-25 Showcase Qualifier (12848159)",
    )


def test_fail_closed() -> None:
    tmp_dir = Path(tempfile.mkdtemp(prefix="challenge_fail_closed_"))
    try:
        history_csv = tmp_dir / "challenge_history_modern.csv"
        shutil.copyfile(FIXTURE, history_csv)

        # "a fetch stub that drops one event": registry says 25 events existed, only 24 made it in.
        dropped = SkippedEvent(
            event_id="99999999", date="2026-07-20", size=64, kind="challenge",
            url="https://www.mtgo.com/decklist/modern-challenge-64-2026-07-2099999999",
            status="ERROR", reason="stubbed fetch failure",
        )
        incomplete = CompletenessSummary(
            registry_count=25, fetched_count=24, complete=False, missing=[dropped],
            tier_counts_registry={64: 16, 32: 5, 96: 4}, tier_counts_fetched={64: 15, 32: 5, 96: 4},
            premier_checked=True, premier_count=0, premier_events=[],
            premier_note="No Modern premier events in window 2026-07-13..2026-07-26 (registry checked)",
        )

        # 1) run_challenge_statistics: complete=False -> xlsx NOT written by default.
        result = run_challenge_statistics(
            history_csv=history_csv, output_dir=tmp_dir / "stats_out", format_name="Modern",
            week_start=WEEK_START, week_end=WEEK_END, completeness=incomplete,
        )
        assert result.complete is False, "expected complete=False when one registry event is missing"
        assert not result.excel_path.exists(), "xlsx must NOT be written when incomplete and allow_partial=False"

        status_path = tmp_dir / "stats_out" / "challenge_statistics.status.json"
        assert status_path.exists(), "status.json must always be written, even when the xlsx is not"
        payload = json.loads(status_path.read_text(encoding="utf-8"))
        assert payload["complete"] is False
        assert payload["registry_count"] == 25
        assert payload["fetched_count"] == 24
        assert payload["missing"][0]["EventID"] == "99999999"

        # 2) allow_partial=True: xlsx IS written, stamped PARTIAL in both filename and STATUS sheet.
        result2 = run_challenge_statistics(
            history_csv=history_csv, output_dir=tmp_dir / "stats_out2", format_name="Modern",
            week_start=WEEK_START, week_end=WEEK_END, completeness=incomplete, allow_partial=True,
        )
        assert result2.complete is False
        assert result2.excel_path.name == "challenge_statistics_PARTIAL.xlsx"
        assert result2.excel_path.exists()

        import pandas as pd
        status_sheet = pd.read_excel(result2.excel_path, sheet_name="STATUS")
        assert bool(status_sheet.iloc[0]["Partial"]) is True
        assert bool(status_sheet.iloc[0]["Complete"]) is False

        # 3) CLI-level: a fetch stub that drops one event makes the process exit non-zero.
        def stub_registry_drops_one(format_name, start_date, end_date, log=None):
            return [
                MtgoRegistryEvent(event_id="1", date="2026-08-03", slug="modern-challenge-64", kind="challenge", size=64, url="u1"),
                MtgoRegistryEvent(event_id="2", date="2026-08-04", slug="modern-challenge-32", kind="challenge", size=32, url="u2"),
            ]

        cli_history = tmp_dir / "cli_outputs" / "challenge_history_modern.csv"
        cli_history.parent.mkdir(parents=True, exist_ok=True)
        cli_history.write_text(
            "EventDate,Format,ChallengeSize,EventSlug,EventID,Place,Deck,Archetype,Pilot\n"
            "2026-08-03,Modern,64,modern-challenge-64,1,1,DeckA,ArchA,PilotA\n",
            encoding="utf-8",
        )
        with _patched(challenge_mtgo_source, "build_mtgo_registry", stub_registry_drops_one):
            rc = verify_challenge_registry.main([
                "--format", "Modern", "--window-start", "2026-08-01", "--window-end", "2026-08-09",
                "--verify-only", "--outputs-base", str(tmp_dir / "cli_outputs"),
            ])
        assert rc == 1, f"expected non-zero exit for an incomplete window, got {rc}"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_status_never_null() -> None:
    tmp_dir = Path(tempfile.mkdtemp(prefix="challenge_status_never_null_"))
    try:
        history_csv = tmp_dir / "challenge_history_modern.csv"
        shutil.copyfile(FIXTURE, history_csv)

        # Refusing to write complete=true with a null registry_count is a hard error, not a
        # silently-accepted state -- this IS the bug this whole gate exists to prevent.
        bad = CompletenessSummary(registry_count=None, fetched_count=None, complete=True, missing=[])
        raised = False
        try:
            run_challenge_statistics(
                history_csv=history_csv, output_dir=tmp_dir / "stats_bad", format_name="Modern",
                week_start=WEEK_START, week_end=WEEK_END, completeness=bad,
            )
        except AssertionError as exc:
            raised = True
            assert "registry_count" in str(exc)
        assert raised, "expected an AssertionError for complete=true with a null registry_count"

        # A genuinely successful run's status.json always carries real integers.
        result = run_challenge_statistics(
            history_csv=history_csv, output_dir=tmp_dir / "stats_good", format_name="Modern",
            week_start=WEEK_START, week_end=WEEK_END, completeness=_complete_completeness(),
        )
        assert result.complete is True
        payload = json.loads((tmp_dir / "stats_good" / "challenge_statistics.status.json").read_text(encoding="utf-8"))
        assert isinstance(payload["registry_count"], int) and payload["registry_count"] > 0
        assert isinstance(payload["fetched_count"], int) and payload["fetched_count"] > 0

        # No completeness object at all -- must NOT silently default to complete=true (the
        # original bug: {"complete": true, "registry_count": null, ...}).
        result_none = run_challenge_statistics(
            history_csv=history_csv, output_dir=tmp_dir / "stats_none", format_name="Modern",
            week_start=WEEK_START, week_end=WEEK_END, completeness=None,
        )
        assert result_none.complete is False, "completeness=None must default to INCOMPLETE, not True"
        payload_none = json.loads((tmp_dir / "stats_none" / "challenge_statistics.status.json").read_text(encoding="utf-8"))
        assert payload_none["complete"] is False
        assert payload_none["registry_count"] is None
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_premier_none_explicit() -> None:
    tmp_dir = Path(tempfile.mkdtemp(prefix="challenge_premier_none_"))
    try:
        history_csv = tmp_dir / "challenge_history_modern.csv"
        history_csv.write_text(
            "EventDate,Format,ChallengeSize,EventSlug,EventID,Place,Deck,Archetype,Pilot\n", encoding="utf-8"
        )

        def fake_registry_no_premier(format_name, start_date, end_date, log=None):
            return [
                MtgoRegistryEvent(event_id="1", date="2026-08-03", slug="modern-challenge-64", kind="challenge", size=64, url="u1"),
            ]

        with _patched(challenge_mtgo_source, "build_mtgo_registry", fake_registry_no_premier):
            none_found = challenge_mtgo_source.verify_registry_against_history(
                "Modern", date(2026, 8, 3), date(2026, 8, 9), history_csv,
            )
        assert none_found.premier_checked is True
        assert none_found.premier_count == 0
        assert none_found.premier_events == []
        assert "No Modern premier events" in none_found.premier_note

        def failing_registry(format_name, start_date, end_date, log=None):
            raise RuntimeError("simulated: mtgo.com registry fetch exploded mid-scan")

        with _patched(challenge_mtgo_source, "build_mtgo_registry", failing_registry):
            scan_failed = challenge_mtgo_source.verify_registry_against_history(
                "Modern", date(2026, 8, 3), date(2026, 8, 9), history_csv,
            )
        assert scan_failed.premier_checked is False
        assert scan_failed.premier_count is None, "a failed scan must report null, never a silent 0"
        assert scan_failed.complete is False
        assert scan_failed.registry_count is None
        assert scan_failed.premier_note == "Premier scan did not complete"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    test_fail_closed()
    print("OK: test_fail_closed")
    test_status_never_null()
    print("OK: test_status_never_null")
    test_premier_none_explicit()
    print("OK: test_premier_none_explicit")
    print("All challenge completeness tests passed.")
