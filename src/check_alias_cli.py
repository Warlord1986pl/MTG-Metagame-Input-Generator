"""check-alias: card-list gate for a deck-name alias merge, run BEFORE the alias is added to
docs/deck_aliases.csv (never after -- see CLAUDE.md point 5). Reuses the same maindeck-multiset
similarity machinery challenge_mtgo_source.py already uses for its own nearest-neighbor deck
classification (alias_check.py), rather than a second comparison mechanism.

Usage:
  python src/check_alias_cli.py check-alias --source WBG --target "Devoted Combo" \
      --window 2026-08-31:2026-09-13 [--format Modern]

Exit code is non-zero when the verdict is not "safe", so the command can gate a script/CI step.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from alias_check import check_alias, format_report_text, write_alias_report_csv  # noqa: E402
from metagame_input_generator import _format_slug_for_url  # noqa: E402


def _parse_window(text: str) -> tuple[date, date]:
    start_str, _, end_str = text.partition(":")
    return date.fromisoformat(start_str.strip()), date.fromisoformat(end_str.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--outputs-base", default=str(REPO_ROOT / "outputs"))
    parser.add_argument("--docs-dir", default=str(REPO_ROOT / "docs"))
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check-alias", help="Compare source vs target deck lists before merging an alias.")
    check.add_argument("--source", required=True, help="Raw/source deck name, e.g. WBG")
    check.add_argument("--target", required=True, help="Target canonical deck name, e.g. 'Devoted Combo'")
    check.add_argument("--window", required=True, help="YYYY-MM-DD:YYYY-MM-DD")
    check.add_argument("--format", default="Modern")
    check.add_argument("--write-report", default=None, help="Optional path to also write an alias_report CSV.")

    args = parser.parse_args()
    outputs_base = Path(args.outputs_base)
    docs_dir = Path(args.docs_dir)

    if args.command == "check-alias":
        window_start, window_end = _parse_window(args.window)
        format_slug = _format_slug_for_url(args.format)
        history_csv = outputs_base / f"challenge_history_{format_slug}.csv"
        mtgo_json_cache_dir = outputs_base / "cache" / "mtgo_json" / format_slug

        report = check_alias(
            history_csv=history_csv,
            mtgo_json_cache_dir=mtgo_json_cache_dir,
            alias_check_config_csv=docs_dir / "alias_check_config.csv",
            format_name=args.format,
            source_name=args.source,
            target_name=args.target,
            window_start=window_start,
            window_end=window_end,
        )
        print(format_report_text(report))

        if args.write_report:
            write_alias_report_csv([report], Path(args.write_report))
            print(f"\nWrote {args.write_report}")

        return 0 if report.verdict == "safe" else 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
