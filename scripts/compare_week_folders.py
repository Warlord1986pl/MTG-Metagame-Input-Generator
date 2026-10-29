"""Compares two flat week folders (runner control 1): PNG, MD and CSV byte for byte, data.xlsx sheet by sheet
(openpyxl writes the creation time into the file properties, so the bytes of two writes always differ). Files only
in one folder are listed; files named in --ignore (e.g. run_report.md, mtgo_snapshot_*) are skipped.

    python scripts/compare_week_folders.py weekly/2026-W43 outputs/_working/reference/2026-W43 [--ignore GLOB ...]

Exit code 0 = identical apart from the xlsx file properties, 1 = differences (listed)."""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import sys
from pathlib import Path

import pandas as pd


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def compare(a: Path, b: Path, ignore: list[str]) -> list[str]:
    def files(d: Path) -> dict:
        return {p.name: p for p in d.iterdir() if p.is_file() and not any(fnmatch.fnmatch(p.name, g) for g in ignore)}

    fa, fb = files(a), files(b)
    diffs = [f"only in {a}: {n}" for n in sorted(set(fa) - set(fb))]
    diffs += [f"only in {b}: {n}" for n in sorted(set(fb) - set(fa))]
    for name in sorted(set(fa) & set(fb)):
        if name.endswith(".xlsx"):
            xa, xb = pd.read_excel(fa[name], sheet_name=None), pd.read_excel(fb[name], sheet_name=None)
            if list(xa) != list(xb):
                diffs.append(f"{name}: sheets {list(xa)} vs {list(xb)}")
                continue
            for sheet in xa:
                if not xa[sheet].equals(xb[sheet]):
                    diffs.append(f"{name}: sheet {sheet} differs")
        elif _sha(fa[name]) != _sha(fb[name]):
            diffs.append(f"{name}: bytes differ")
    return diffs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--ignore", nargs="*", default=[])
    args = ap.parse_args()
    diffs = compare(Path(args.a), Path(args.b), args.ignore)
    for d in diffs:
        print(d)
    print("identical (apart from the xlsx file properties)" if not diffs else f"{len(diffs)} difference(s)")
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main())
