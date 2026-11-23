"""Archetype and variant changes against the previous export (deckdb_folder.attribute_changes, 2026-11-23, DeckDB K48 /
K49): summed per (deck, from, to) with whole-DB and format-period counts, only over lists that keep their deck; a deck
change stays a move of classification_changes."""
import sys
from datetime import date
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import deckdb_folder as df_  # noqa: E402
import deckdb_input as di  # noqa: E402
from test_deckdb_input import COLS, _row  # noqa: E402

FSTART = date(2026, 5, 19)


def _export(tmp_path, name, rows):
    p = tmp_path / name
    pd.DataFrame(rows, columns=COLS).to_csv(p, index=False, encoding="utf-8")
    return p


def _records(df):
    return sorted(tuple(r) for r in df.astype(str).values.tolist())


def test_archetype_and_variant_change_without_deck_change(tmp_path):
    old = [_row(1, 11, "2026-01-10", "assigned", "Birthing Ritual", "Combo", 1, 1),  # before the format period
           _row(1, 12, "2026-01-10", "assigned", "Birthing Ritual", "Combo", 2, 2),
           _row(2, 21, "2026-06-01", "assigned", "Birthing Ritual", "Combo", 3, 1),
           _row(2, 22, "2026-06-01", "assigned", "Birthing Ritual", "Combo", 4, 2),
           _row(2, 23, "2026-06-01", "assigned", "Zoo", "Aggro", 5, 3, variant="Zoo: Kroxa"),
           _row(2, 24, "2026-06-01", "rogue", "Rogue", "Rogue", 6, 4)]
    new = [dict(r, rules_version="rv2") for r in old]
    new[0].update(archetype="Midrange", variant="Birthing Ritual: Oculus")
    new[1].update(archetype="Midrange", variant="Birthing Ritual: other")
    new[2].update(archetype="Midrange", variant="Birthing Ritual: Oculus")
    new[3].update(archetype="Midrange", variant="Birthing Ritual: Ephemerate")
    prev = _export(tmp_path, "assignments_rv1_old.csv", old)
    cur = di.load_export(_export(tmp_path, "assignments_rv2_new.csv", new))

    moves, desc, _ = df_.classification_changes(cur, prev)
    assert moves.empty and "0 changed deck" in desc
    arch, var = df_.attribute_changes(cur, prev, FSTART)
    assert _records(arch) == [("Birthing Ritual", "Combo", "Midrange", "4", "2")]
    assert _records(var) == sorted([("Birthing Ritual", "(none)", "Oculus", "2", "1"),
                                    ("Birthing Ritual", "(none)", "Ephemerate", "1", "1"),
                                    ("Birthing Ritual", "(none)", "other", "1", "0")])


def test_a_deck_change_is_not_an_archetype_or_variant_change(tmp_path):
    old = [_row(1, 11, "2026-06-01", "assigned", "Blink", "Midrange", 1, 1, variant="Esper Blink")]
    new = [dict(old[0], rules_version="rv2", deck="Birthing Ritual", archetype="Combo", variant="")]
    prev = _export(tmp_path, "assignments_rv1_old.csv", old)
    cur = di.load_export(_export(tmp_path, "assignments_rv2_new.csv", new))
    moves, _, _ = df_.classification_changes(cur, prev)
    assert moves.to_dict("records") == [{"From": "Blink", "To": "Birthing Ritual", "Lists": 1}]
    arch, var = df_.attribute_changes(cur, prev, FSTART)
    assert arch.empty and var.empty


def test_a_renamed_deck_keeps_its_variants(tmp_path):
    old = [_row(1, 11, "2026-06-01", "assigned", "Rakdos Moonshadow", "Aggro", 1, 1,
                variant="Rakdos Moonshadow: inne")]
    new = [dict(old[0], rules_version="rv2", deck="Moonshadow", variant="Moonshadow: inne")]
    prev = _export(tmp_path, "assignments_rv1_old.csv", old)
    cur = di.load_export(_export(tmp_path, "assignments_rv2_new.csv", new))
    arch, var = df_.attribute_changes(cur, prev, FSTART, {"Rakdos Moonshadow": "Moonshadow"})
    assert arch.empty and var.empty


def test_without_a_previous_export_nothing_is_reported(tmp_path):
    cur = di.load_export(_export(tmp_path, "assignments_rv2_new.csv",
                                 [_row(1, 11, "2026-06-01", "assigned", "Zoo", "Aggro", 1, 1, rv="rv2")]))
    arch, var = df_.attribute_changes(cur, None, FSTART)
    assert arch.empty and var.empty
