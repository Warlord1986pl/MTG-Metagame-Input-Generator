"""Deck renames of the DeckDB rules (docs/deckdb_renames.csv, 2026-10-30, DeckDB entry 332): a rename is one "renamed"
line in notes.md Status, never a move of every list of the deck; a real move stays a move."""
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import deckdb_folder as df_  # noqa: E402
import deckdb_input as di  # noqa: E402
from test_deckdb_input import COLS, _row  # noqa: E402


def _export(tmp_path, name, rows):
    p = tmp_path / name
    pd.DataFrame(rows, columns=COLS).to_csv(p, index=False, encoding="utf-8")
    return p


def test_load_renames_refuses_bad_tables(tmp_path):
    with pytest.raises(di.DeckdbInputError, match="brak"):
        df_.load_renames(tmp_path / "missing.csv")
    p = tmp_path / "r.csv"
    p.write_text("old,new\nA,B\nB,C\n", encoding="utf-8")
    with pytest.raises(di.DeckdbInputError, match="łańcuch"):
        df_.load_renames(p)
    p.write_text("old,new\nA,B\nA,C\n", encoding="utf-8")
    with pytest.raises(di.DeckdbInputError, match="dwa razy"):
        df_.load_renames(p)
    p.write_text("old,new,rules_version,decision\nRakdos Moonshadow,Moonshadow,rv2,x\n", encoding="utf-8")
    assert df_.load_renames(p) == {"Rakdos Moonshadow": "Moonshadow"}


def test_the_repository_table_names_the_moonshadow_rename():
    assert df_.load_renames(REPO_ROOT / "docs" / "deckdb_renames.csv")["Rakdos Moonshadow"] == "Moonshadow"


def test_rename_is_not_a_move_but_a_real_move_stays(tmp_path):
    old = [_row(1, 11, "2026-10-19", "assigned", "Rakdos Moonshadow", "Aggro", 1, 1, rv="rv1"),
           _row(1, 12, "2026-10-19", "assigned", "Rakdos Moonshadow", "Aggro", 2, 2, rv="rv1"),
           _row(1, 13, "2026-10-19", "unassigned", "Unassigned", "Unassigned", 3, 3, rv="rv1"),
           _row(1, 14, "2026-10-19", "assigned", "Zoo", "Aggro", 4, 4, rv="rv1")]
    new = [dict(r, rules_version="rv2") for r in old]
    for r in new[:3]:
        r.update(status="assigned", deck="Moonshadow", archetype="Aggro")
    prev = _export(tmp_path, "assignments_rv1_old.csv", old)
    cur = di.load_export(_export(tmp_path, "assignments_rv2_new.csv", new))
    moves, desc, renamed = df_.classification_changes(cur, prev, renames={"Rakdos Moonshadow": "Moonshadow"})
    assert renamed.to_dict("records") == [{"Old": "Rakdos Moonshadow", "New": "Moonshadow", "Lists": 2}]
    assert moves.to_dict("records") == [{"From": di.NO_DECK_LABEL, "To": "Moonshadow", "Lists": 1}]
    assert "1 changed deck" in desc
    moves0, _, renamed0 = df_.classification_changes(cur, prev)  # without the table every list would be a move
    assert int(moves0["Lists"].sum()) == 3 and renamed0.empty
