"""Control of the legacy mode on W42 (2026-10-05 .. 2026-10-18) after a change of pipeline code.

    python scripts/legacy_w42_control.py [--work DIR] [--keep]

1. copies the persistent legacy inputs (history / ledger CSVs of outputs/, outputs/cache, outputs/league) into a work
   directory (default: a new temp directory), so the real outputs/ is never written;
2. runs the legacy generation of W42 there (--anchor-sunday 2026-10-18, 14-day window, Challenge decklists, trend 12
   weeks, --skip-league) with this interpreter;
3. compares the run directory with the reference ``przed/`` of 2026-10-21 (outputs/_working/deckdb_robocze/
   _punkt_odniesienia_legacy_W42_2026-10-21/przed/W42_2026-10-05_to_2026-10-18): CSV/JSON/XML/TXT byte for byte,
   XLSX per sheet by values, PNG by decoded pixels.

Expected: 83 files, 82 identical, 1 different -- metagame_input_grouped.xml, only its generated_at attribute.
Exit code 0 when exactly that, 1 otherwise (every other difference is listed). Needs the project venv
(E:/github/.venv/Scripts/python.exe) and the reference directory on disk (outputs/ is not in git).
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = ROOT / "outputs"
REFERENCE = (OUTPUTS / "_working" / "deckdb_robocze" / "_punkt_odniesienia_legacy_W42_2026-10-21" / "przed"
             / "W42_2026-10-05_to_2026-10-18")
RUN_DIR = Path("2026") / "10" / "W42_2026-10-05_to_2026-10-18"
PERSISTENT = ("broodscale_override_ledger_modern.csv", "broodscale_reverse_alias_ledger_modern.csv",
              "challenge_history_modern.csv", "metagame_history.csv", "metagame_history_challenge_top32_modern.csv",
              "metagame_history_modern.csv", "premier_history_modern.csv", "rakdos_override_ledger_modern.csv")
PERSISTENT_DIRS = ("cache", "league")
ARGS = ["--format", "Modern", "--history-points", "1", "--metagame-window-days", "14", "--rogue-threshold", "0.5",
        "--include-challenge-decklist", "--anchor-sunday", "2026-10-18", "--trend-weeks", "12", "--skip-league"]
EXPECTED_DIFF = "metagame_input_grouped.xml"


def _h(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _xlsx_equal(a: Path, b: Path) -> bool:
    import pandas as pd

    sa, sb = pd.read_excel(a, sheet_name=None), pd.read_excel(b, sheet_name=None)
    return list(sa) == list(sb) and all(sa[k].equals(sb[k]) for k in sa)


def _png_equal(a: Path, b: Path) -> bool:
    import numpy as np
    from PIL import Image

    ia, ib = Image.open(a), Image.open(b)
    return ia.size == ib.size and np.array_equal(np.asarray(ia), np.asarray(ib))


def compare(ref: Path, run: Path) -> tuple[int, list[str], list[str], list[str]]:
    """(identical, different, only in the reference, only in the run)."""
    fa = {p.relative_to(ref).as_posix() for p in ref.rglob("*") if p.is_file()}
    fb = {p.relative_to(run).as_posix() for p in run.rglob("*") if p.is_file()}
    same, diff = 0, []
    for f in sorted(fa & fb):
        a, b = ref / f, run / f
        if _h(a) == _h(b) or (f.endswith(".xlsx") and _xlsx_equal(a, b)) or (f.endswith(".png") and _png_equal(a, b)):
            same += 1
        else:
            diff.append(f)
    return same, diff, sorted(fa - fb), sorted(fb - fa)


def only_generated_at(a: Path, b: Path) -> bool:
    strip = lambda p: re.sub(r'generated_at="[^"]*"', 'generated_at=""', p.read_text(encoding="utf-8"))  # noqa: E731
    return strip(a) == strip(b)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", help="work directory (default: a new temp directory)")
    ap.add_argument("--keep", action="store_true", help="keep the work directory")
    a = ap.parse_args()
    if not REFERENCE.exists():
        print(f"reference missing: {REFERENCE}")
        return 1
    work = Path(a.work) if a.work else Path(tempfile.mkdtemp(prefix="legacy_w42_"))
    out = work / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    for f in PERSISTENT:
        shutil.copy2(OUTPUTS / f, out / f)
    for d in PERSISTENT_DIRS:
        shutil.copytree(OUTPUTS / d, out / d, dirs_exist_ok=True)
    log = work / "run.log"
    with log.open("w", encoding="utf-8") as fh:
        rc = subprocess.run([sys.executable, "-W", "ignore", str(ROOT / "src" / "metagame_input_generator.py"), *ARGS,
                             "--outputs-base", str(out)], cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT).returncode
    print(f"run exit {rc}, log {log}")
    if rc:
        return 1
    run = out / RUN_DIR
    same, diff, only_ref, only_run = compare(REFERENCE, run)
    print(f"files: {same + len(diff)} compared, {same} identical, {len(diff)} different, only reference "
          f"{len(only_ref)}, only run {len(only_run)}")
    for f in diff:
        note = " (generated_at only)" if f == EXPECTED_DIFF and only_generated_at(REFERENCE / f, run / f) else ""
        print(f"  DIFF {f}{note}")
    for f in only_ref:
        print(f"  ONLY_REFERENCE {f}")
    for f in only_run:
        print(f"  ONLY_RUN {f}")
    ok = (diff == [EXPECTED_DIFF] and only_generated_at(REFERENCE / EXPECTED_DIFF, run / EXPECTED_DIFF)
          and not only_ref and not only_run)
    print("OK: only generated_at of metagame_input_grouped.xml differs" if ok else "NOT OK: see the differences above")
    if not a.keep and not a.work:
        shutil.rmtree(work, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
