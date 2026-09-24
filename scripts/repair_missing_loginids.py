#!/usr/bin/env python3
"""Fill a missing LoginID in persisted Challenge/premier history from mtgo.com's own record.

For every history row that has an EventID but no LoginID, this looks up that event's durably
cached mtgo.com blob (outputs/cache/mtgo_json/<format>/<EventID>.json -- the same raw record the
row was originally parsed from), takes the final_rank entry at the row's Place, and writes that
entry's LoginID together with the display name mtgo.com's decklist gives that LoginID in that
same event -- the same two fields the fixed parser (challenge_mtgo_source.ingest_labeled_event)
takes from mtgo.com. Keyed strictly by (EventID, Place) against the source record: never by name,
never across events, never a merge. A prior name then appears through the pipeline's normal
rename handling (league_site_export._name_history, scoped to each season's end so a closed season
is not rewritten).

Anything that cannot be resolved from the source (no cached blob, incomplete final_rank, no entry
at that Place, no decklist for that LoginID) is reported and makes the script exit 1 without
writing anything. Dry-run by default; --apply rewrites only the affected lines, byte-for-byte
preserving every other line, the BOM and the line endings.

Written for 12854500 (C16, 2026-09-19) place 13, stored as "Jetpool" with an empty LoginID by the
old name-keyed MTGGoldfish join (see challenge_mtgo_source.ingest_labeled_event).

Usage:
  python scripts/repair_missing_loginids.py --format modern            # dry run
  python scripts/repair_missing_loginids.py --format modern --apply
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]


def _source_entry(cache_dir: Path, event_id: str, place: int) -> Tuple[Optional[Tuple[str, str]], str]:
    blob_path = cache_dir / f"{event_id}.json"
    if not blob_path.exists():
        return None, f"no cached mtgo.com blob at {blob_path}"
    data = json.loads(blob_path.read_text(encoding="utf-8"))
    final_rank = data.get("final_rank") or []
    ranks = sorted(int(r["rank"]) for r in final_rank)
    if ranks != list(range(1, len(ranks) + 1)) or not ranks:
        return None, f"final_rank in {blob_path.name} is not a complete 1..N sequence"
    at_place = [r for r in final_rank if int(r["rank"]) == place]
    if len(at_place) != 1:
        return None, f"final_rank has {len(at_place)} entries at place {place}"
    loginid = str(at_place[0]["loginid"]).strip()
    players = [d.get("player", "") for d in data.get("decklists", []) if str(d.get("loginid")) == loginid]
    if len(players) != 1 or not loginid or not players[0]:
        return None, f"no unique decklist for LoginID {loginid!r}"
    return (loginid, players[0]), ""


def repair_file(path: Path, cache_dir: Path, apply: bool) -> Tuple[List[str], List[str]]:
    """Returns (changes, problems). Writes only when *apply* and there are no problems."""
    raw = path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    lines = text.splitlines(keepends=True)
    header = next(csv.reader([lines[0]]))
    col: Dict[str, int] = {name: i for i, name in enumerate(header)}
    changes: List[str] = []
    problems: List[str] = []
    for i in range(1, len(lines)):
        body = lines[i].rstrip("\r\n")
        ending = lines[i][len(body):]
        fields = next(csv.reader([body]))
        if len(fields) != len(header):
            continue
        event_id, login_id = fields[col["EventID"]].strip(), fields[col["LoginID"]].strip()
        if not event_id or login_id:
            continue
        place = int(fields[col["Place"]])
        found, why = _source_entry(cache_dir, event_id, place)
        where = f"{path.name} line {i + 1} (EventID {event_id}, Place {place}, Pilot {fields[col['Pilot']]!r})"
        if found is None:
            problems.append(f"{where}: {why}")
            continue
        new_id, new_name = found
        changes.append(f"{where}: LoginID '' -> {new_id}, Pilot {fields[col['Pilot']]!r} -> {new_name!r}")
        fields[col["LoginID"]], fields[col["Pilot"]] = new_id, new_name
        buf = io.StringIO()
        csv.writer(buf, lineterminator=ending or "").writerow(fields)
        lines[i] = buf.getvalue()
    if apply and changes and not problems:
        out = "".join(lines).encode("utf-8")
        path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + out)
    return changes, problems


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--format", default="modern")
    ap.add_argument("--outputs", type=Path, default=REPO_ROOT / "outputs")
    ap.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    args = ap.parse_args(argv)

    cache_dir = args.outputs / "cache" / "mtgo_json" / args.format
    all_problems: List[str] = []
    for name in (f"challenge_history_{args.format}.csv", f"premier_history_{args.format}.csv"):
        path = args.outputs / name
        if not path.exists():
            continue
        changes, problems = repair_file(path, cache_dir, apply=False)
        all_problems += problems
        for c in changes:
            print(("APPLY " if args.apply else "WOULD ") + c)
    for p in all_problems:
        print(f"UNRESOLVED {p}", file=sys.stderr)
    if all_problems:
        print("nothing written: fix or quarantine the unresolved rows first", file=sys.stderr)
        return 1
    if args.apply:
        for name in (f"challenge_history_{args.format}.csv", f"premier_history_{args.format}.csv"):
            path = args.outputs / name
            if path.exists():
                repair_file(path, cache_dir, apply=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
