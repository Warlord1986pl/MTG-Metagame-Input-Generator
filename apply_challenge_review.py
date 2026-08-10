"""Apply your manual answers from a challenge_needs_review_*.csv back into the persistent
Challenge history.

Workflow:
  1. Open a challenge_needs_review_*.csv (written next to the weekly run, or in
     outputs/challenge_migration_reports/) in Excel/a text editor.
  2. For rows you've identified, fill in the "resolved_deck" column with the deck name
     (e.g. "Eldrazi Tron"). Leave it blank for rows you haven't gotten to yet.
  3. Save the CSV, then run:

       python apply_challenge_review.py path\\to\\challenge_needs_review_....csv

  4. Re-run Challenge statistics for the affected week(s) (GUI Statistics tab, or the CLI) to
     regenerate challenge_statistics.xlsx with your corrections included.

Matching is by (event_id, pilot); Archetype is recomputed through the normal
alias/mapping/archetype-rules pipeline, same as everywhere else in this project.
"""
import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from metagame_input_generator import _format_slug_for_url  # noqa: E402
from challenge_history_engine import apply_review_corrections  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("review_csv", help="Path to a challenge_needs_review_*.csv with resolved_deck filled in")
    parser.add_argument(
        "--history-csv",
        default=None,
        help="Path to challenge_history_<format>.csv (default: derived from the review CSV's Format column)",
    )
    args = parser.parse_args()

    review_csv = Path(args.review_csv)
    if not review_csv.exists():
        print(f"[ERROR] Not found: {review_csv}")
        sys.exit(1)

    history_csv = Path(args.history_csv) if args.history_csv else None
    if history_csv is None:
        import pandas as pd

        review = pd.read_csv(review_csv, dtype=str, encoding="utf-8-sig", keep_default_na=False)
        formats = sorted({f for f in review.get("format", pd.Series(dtype=str)).unique() if f})
        if len(formats) != 1:
            print(
                f"[ERROR] Could not determine a single Format from {review_csv} (found: {formats}). "
                "Pass --history-csv explicitly."
            )
            sys.exit(1)
        format_slug = _format_slug_for_url(formats[0])
        history_csv = REPO_ROOT / "outputs" / f"challenge_history_{format_slug}.csv"

    if not history_csv.exists():
        print(f"[ERROR] History file not found: {history_csv}")
        sys.exit(1)

    summary = apply_review_corrections(review_csv, history_csv, log=print)

    print()
    print(
        f"Applied: {summary['applied']} | still blank (skipped): {summary['skipped_blank']} | "
        f"not found in history: {len(summary['not_found'])} | ambiguous match: {len(summary['ambiguous'])}"
    )
    if summary["applied"]:
        print()
        print("Corrections written. Re-run Challenge statistics for the affected week(s) to refresh the xlsx.")


if __name__ == "__main__":
    main()
