"""Registry-first diagnostic/recompute CLI for the mtgo.com Challenge pipeline.

The registry (challenge_mtgo_source.build_mtgo_registry) is the independent source of truth for
"what tournaments happened" -- built straight from https://www.mtgo.com/decklists, not from
whatever the persisted history CSV already has. This tool diffs the two so a shortfall (a missing
Challenge 96, a month-boundary drop, a hard cutoff at the window end -- whatever shape this week's
gap takes) is visible and reported, never silently absorbed into a "complete: true" status.

Usage:
  # Diagnostic only -- builds the registry, diffs it against the persisted history for the
  # window, prints the missing events as a table, writes nothing.
  python verify_challenge_registry.py --format Modern --window-start 2026-07-27 --window-end 2026-08-09 --verify-only

  # Recompute challenge_statistics.xlsx for this window from whatever is ALREADY in the
  # persisted history (no fresh fetch/sync) with a real completeness gate. Refuses to write the
  # xlsx if the window is incomplete unless --allow-partial is also given (in which case the
  # xlsx is written but stamped PARTIAL, both in the STATUS sheet and the filename).
  python verify_challenge_registry.py --format Modern --window-start 2026-07-27 --window-end 2026-08-09
  python verify_challenge_registry.py --format Modern --window-start 2026-07-27 --window-end 2026-08-09 --allow-partial

Exit code is non-zero whenever the window is not complete (in both modes), so this is safe to use
as a CI/scheduled-task gate.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from challenge_mtgo_source import verify_registry_against_history  # noqa: E402
from challenge_history_engine import run_challenge_statistics  # noqa: E402
from metagame_input_generator import _format_slug_for_url, compute_run_dir  # noqa: E402


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


def _print_missing_table(missing) -> None:
    if not missing:
        print("MISSING: none")
        return
    print(f"MISSING ({len(missing)}):")
    print(f"{'EventID':<12} {'Tier':<6} {'Date':<12}")
    for m in sorted(missing, key=lambda x: (x.date, x.event_id)):
        tier = f"C{m.size}" if m.size else "?"
        print(f"{m.event_id:<12} {tier:<6} {m.date:<12}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--format", dest="format_name", default="Modern")
    parser.add_argument("--window-start", required=True, type=_parse_date)
    parser.add_argument("--window-end", required=True, type=_parse_date)
    parser.add_argument(
        "--verify-only", action="store_true",
        help="Build the registry, diff it against persisted history, print the diff, write nothing.",
    )
    parser.add_argument(
        "--allow-partial", action="store_true",
        help="(recompute mode only) write challenge_statistics.xlsx even if the window is incomplete, stamped PARTIAL.",
    )
    parser.add_argument("--outputs-base", default=str(REPO_ROOT / "outputs"))
    args = parser.parse_args(argv)

    outputs_base = Path(args.outputs_base)
    format_slug = _format_slug_for_url(args.format_name)
    history_csv = outputs_base / f"challenge_history_{format_slug}.csv"

    def log(msg: str) -> None:
        print(msg)

    completeness = verify_registry_against_history(
        format_name=args.format_name,
        start_date=args.window_start,
        end_date=args.window_end,
        history_csv=history_csv,
        log=log,
    )

    print()
    print(f"Window: {args.window_start.isoformat()}..{args.window_end.isoformat()} ({args.format_name})")
    print(f"Registry: {completeness.registry_count} challenge event(s), tiers={completeness.tier_counts_registry}")
    print(f"Present in {history_csv.name}: {completeness.fetched_count}, tiers={completeness.tier_counts_fetched}")
    print(f"Premier: checked={completeness.premier_checked} count={completeness.premier_count} -- {completeness.premier_note}")
    print()
    _print_missing_table(completeness.missing)
    print()
    print(f"COMPLETE: {completeness.complete}")

    if args.verify_only:
        return 0 if completeness.complete else 1

    run_dir = compute_run_dir(outputs_base, args.window_start, args.window_end)
    stats_dir = run_dir / "statistics" / "challenges"
    result = run_challenge_statistics(
        history_csv=history_csv,
        output_dir=stats_dir,
        format_name=args.format_name,
        week_start=args.window_start,
        week_end=args.window_end,
        log=log,
        completeness=completeness,
        allow_partial=args.allow_partial,
    )
    print(f"\nxlsx: {result.excel_path} (exists={result.excel_path.exists()}, complete={result.complete})")
    return 0 if result.complete or args.allow_partial else 1


if __name__ == "__main__":
    sys.exit(main())
