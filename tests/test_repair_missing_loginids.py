"""Offline tests for scripts/repair_missing_loginids.py.

Run directly: python tests/test_repair_missing_loginids.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import repair_missing_loginids as repair  # noqa: E402

HEADER = "EventDate,Format,Tier,PlayerCount,EventSlug,EventID,Place,Deck,DeckGuess,Archetype,Pilot,LoginID\n"
GOOD = "2026-09-19,modern,16,36,modern-challenge-16,12854500,12,Broodscale Combo,,Combo,svessesvv,2198387\n"
BROKEN = "2026-09-19,modern,16,36,modern-challenge-16,12854500,13,Broodscale Combo,,Combo,Jetpool,\n"
LEGACY = "2026-06-02,Modern,32,40,old-slug,,5,Burn,,Aggro,someone,\n"  # no EventID: never touched


def _setup(with_blob: bool = True) -> Path:
    outputs = Path(tempfile.mkdtemp(prefix="repair_loginids_"))
    cache = outputs / "cache" / "mtgo_json" / "modern"
    cache.mkdir(parents=True)
    (outputs / "challenge_history_modern.csv").write_bytes(
        b"\xef\xbb\xbf" + (HEADER + LEGACY + GOOD + BROKEN).encode("utf-8")
    )
    if with_blob:
        blob = {
            "decklists": [{"loginid": "2198387", "player": "svessesvv"}, {"loginid": "2111039", "player": "Overman220"}],
            "final_rank": [{"loginid": str(1000 + i), "rank": str(i)} for i in range(1, 12)]
            + [{"loginid": "2198387", "rank": "12"}, {"loginid": "2111039", "rank": "13"}],
        }
        (cache / "12854500.json").write_text(json.dumps(blob), encoding="utf-8")
    return outputs


def test_dry_run_writes_nothing() -> None:
    outputs = _setup()
    before = (outputs / "challenge_history_modern.csv").read_bytes()
    assert repair.main(["--outputs", str(outputs)]) == 0
    assert (outputs / "challenge_history_modern.csv").read_bytes() == before


def test_apply_takes_login_id_and_name_from_the_source() -> None:
    outputs = _setup()
    assert repair.main(["--outputs", str(outputs), "--apply"]) == 0
    after = (outputs / "challenge_history_modern.csv").read_bytes()
    expected = b"\xef\xbb\xbf" + (HEADER + LEGACY + GOOD + BROKEN.replace("Jetpool,\n", "Overman220,2111039\n")).encode("utf-8")
    assert after == expected, after
    # Idempotent: a second run finds nothing to do.
    assert repair.main(["--outputs", str(outputs), "--apply"]) == 0
    assert (outputs / "challenge_history_modern.csv").read_bytes() == expected


def test_unresolvable_row_fails_and_writes_nothing() -> None:
    outputs = _setup(with_blob=False)
    before = (outputs / "challenge_history_modern.csv").read_bytes()
    assert repair.main(["--outputs", str(outputs), "--apply"]) == 1
    assert (outputs / "challenge_history_modern.csv").read_bytes() == before


if __name__ == "__main__":
    test_dry_run_writes_nothing()
    test_apply_takes_login_id_and_name_from_the_source()
    test_unresolvable_row_fails_and_writes_nothing()
    print("All repair_missing_loginids tests passed.")
