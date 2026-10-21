import json
import math
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import deckdb_input as di  # noqa: E402

COLS = list(di.REQUIRED_COLUMNS)


def _row(eid, dtid, day, status, deck, arch, player, rank, w, l, bye=0, variant="", rv="rv1", pc=40):
    return {
        "event_id": str(eid), "decktournamentid": str(dtid), "loginid": f"L{player}", "event_date": day,
        "status": status, "deck": deck, "archetype": arch, "rules_version": rv, "variant": variant,
        "player": f"g-{player}", "final_rank": rank, "swiss_wins": w, "swiss_losses": l, "had_bye": bye,
        "swiss_rounds": 6, "player_count": pc,
    }


def _write(tmp_path: Path, rows) -> Path:
    p = tmp_path / "export.csv"
    pd.DataFrame(rows, columns=COLS).to_csv(p, index=False, encoding="utf-8")
    return p


def _sample(tmp_path: Path) -> Path:
    return _write(tmp_path, [
        _row(1, 11, "2026-10-01", "assigned", "Zoo", "Aggro", "a", 1, 5, 1),
        _row(1, 12, "2026-10-01", "assigned", "Zoo", "Aggro", "b", 9, 4, 2, bye=1),
        _row(1, 13, "2026-10-01", "rogue", "Rogue", "Rogue", "c", 2, 4, 2),
        _row(2, 21, "2026-10-02", "assigned", "Zoo", "Aggro", "a", 3, 5, 1, pc=60),
        _row(2, 22, "2026-10-02", "assigned", "Burn", "Aggro", "d", 12, 3, 3, pc=60),
        _row(2, 23, "2026-10-02", "unassigned", "Unassigned", "Unassigned", "e", 1, 6, 0, pc=60),
    ])


def test_refuses_more_than_one_rules_version(tmp_path):
    p = _write(tmp_path, [
        _row(1, 11, "2026-10-01", "assigned", "Zoo", "Aggro", "a", 1, 5, 1),
        _row(1, 12, "2026-10-01", "assigned", "Zoo", "Aggro", "b", 2, 5, 1, rv="rv2"),
    ])
    with pytest.raises(di.DeckdbInputError, match="rules_version"):
        di.load_export(p)


def test_refuses_window_ending_after_last_event_date(tmp_path):
    exp = di.load_export(_sample(tmp_path))
    with pytest.raises(di.DeckdbInputError, match="ostatniej dacie"):
        di.window_rows(exp, date(2026, 10, 1), date(2026, 10, 3))
    assert len(di.window_rows(exp, date(2026, 10, 1), date(2026, 10, 2))) == 6
    assert len(di.window_rows(exp, date(2026, 10, 2), date(2026, 10, 2))) == 3  # closed both sides


def test_config_has_no_default_path(tmp_path):
    cfg = tmp_path / "cfg.csv"
    cfg.write_text("key,value\nexport_path,\n", encoding="utf-8")
    with pytest.raises(di.DeckdbInputError):
        di.load_deckdb_config(cfg)
    with pytest.raises(di.DeckdbInputError):
        di.load_deckdb_config(tmp_path / "missing.csv")


def test_deck_metrics_come_only_from_export_columns(tmp_path):
    exp = di.load_export(_sample(tmp_path))
    rows = di.window_rows(exp, date(2026, 10, 1), date(2026, 10, 2))
    tbl = di.deck_table(rows).set_index("Talia")
    zoo = tbl.loc["Zoo"]
    assert zoo["Listy"] == 3 and zoo["Udzial_Top32_pct"] == pytest.approx(50.0)
    assert zoo["Eventy_z_lista"] == 2
    assert zoo["Top8"] == 2 and zoo["Konwersja_Top32_Top8_pct"] == pytest.approx(200 / 3)
    assert zoo["Trofea"] == 1
    assert zoo["Wygrane_szw"] == 14 and zoo["Mecze_szw"] == 18  # bye is not a match
    assert zoo["Winrate_szw_pct"] == pytest.approx(14 / 18 * 100)
    lo, hi = di.wilson_interval(14, 18)
    assert zoo["Wilson95_dol_pct"] == pytest.approx(lo * 100) and lo < 14 / 18 < hi
    assert zoo["Gracze"] == 2 and zoo["Najczestszy_gracz_udzial_pct"] == pytest.approx(200 / 3)
    assert zoo["Listy_z_bye"] == 1
    # Rogue + unassigned: one row in materials, separate in the working split
    nd = tbl.loc[di.NO_DECK_LABEL]
    assert nd["Listy"] == 2 and nd["Archetyp"] == di.NO_DECK_LABEL
    split = di.no_deck_split(rows).set_index("Status")
    assert split.loc["rogue", "Listy"] == 1 and split.loc["unassigned", "Listy"] == 1
    assert tbl["Listy"].sum() == len(rows)


def test_archetype_is_pooled_decks(tmp_path):
    exp = di.load_export(_sample(tmp_path))
    rows = di.window_rows(exp, date(2026, 10, 1), date(2026, 10, 2))
    arch = di.archetype_table(rows).set_index("Archetyp")
    aggro = arch.loc["Aggro"]
    assert aggro["Talie"] == 2 and aggro["Listy"] == 4 and aggro["Wygrane_szw"] == 17
    assert aggro["Gracze"] == 3  # a, b, d -- union of players, not a sum over decks


def test_coverage(tmp_path):
    exp = di.load_export(_sample(tmp_path))
    rows = di.window_rows(exp, date(2026, 10, 1), date(2026, 10, 2))
    cov = di.coverage(rows)
    assert cov == {"events": 2, "lists": 6, "participants": 100, "coverage_pct": 6.0}


def test_variant_threshold_d2(tmp_path):
    rows = []
    # 20 Zoo lists in October: 15 x V1 (3 players), 3 x V2 (1 player), 2 x V3 (2 players), none plain
    for i in range(20):
        variant = "V1 Zoo" if i < 15 else ("V2 Zoo" if i < 18 else "V3 Zoo")
        player = f"p{i % 3}" if i < 15 else ("solo" if i < 18 else f"q{i}")
        rows.append(_row(1, 100 + i, "2026-10-01", "assigned", "Zoo", "Aggro", player, i + 1, 3, 3, variant=variant))
    # Burn: 19 lists, all one variant -> no split (monthly gate)
    for i in range(19):
        rows.append(_row(2, 200 + i, "2026-10-02", "assigned", "Burn", "Aggro", f"b{i}", i + 1, 3, 3, variant="X Burn"))
    exp = di.load_export(_write(tmp_path, rows))
    var = di.variant_table(di.window_rows(exp, date(2026, 10, 1), date(2026, 10, 2)))
    assert list(var["Wariant"]) == ["V1 Zoo"]  # V2: 1 player, V3: 2 lists


def test_finalists_have_no_player_identifiers(tmp_path):
    exp = di.load_export(_sample(tmp_path))
    fin = di.finalists_table(di.window_rows(exp, date(2026, 10, 1), date(2026, 10, 2)))
    assert list(fin.columns) == ["EventID", "Data", "Talia_1", "Talia_2"]
    assert list(fin["Talia_1"]) == ["Zoo"]  # event 2 has no rank 2 in this sample


def test_match_mtgo_cache_counts_both_ways(tmp_path):
    exp = di.load_export(_sample(tmp_path))
    rows = di.window_rows(exp, date(2026, 10, 1), date(2026, 10, 2))
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "1.json").write_text(json.dumps({
        "event_id": "1", "starttime": "2026-10-01 10:00", "description": "Modern Challenge 32",
        "decklists": [{"decktournamentid": "11"}, {"decktournamentid": "12"}, {"decktournamentid": "99"}],
    }), encoding="utf-8")
    (cache / "3.json").write_text(json.dumps({
        "event_id": "3", "starttime": "2026-10-02 10:00", "description": "Modern Showcase Challenge",
        "decklists": [{"decktournamentid": "31"}],
    }), encoding="utf-8")
    res = di.match_mtgo_cache(rows, cache, date(2026, 10, 1), date(2026, 10, 2))
    assert res["matched"] == 2
    assert res["export_not_in_cache"] == 4  # 13 + event 2's three lists
    assert res["cache_not_in_export"] == 1  # 99; the Showcase is never counted
    assert res["export_events_not_in_cache"] == 1 and res["cache_events_not_in_export"] == 0


def test_wilson_empty():
    assert all(math.isnan(v) for v in di.wilson_interval(0, 0))


def test_working_breakdown_and_cli_mode(tmp_path):
    import deckdb_materials as dm
    import metagame_input_generator as mig

    export_csv = _sample(tmp_path)
    cfg = tmp_path / "cfg.csv"
    cfg.write_text(f"key,value\nexport_path,{export_csv.as_posix()}\n", encoding="utf-8")
    args = mig.parse_args([
        "--input-mode", "deckdb", "--deckdb-config", str(cfg), "--week-start", "2026-10-01",
        "--week-end", "2026-10-02", "--trend-weeks", "1", "--outputs-base", str(tmp_path / "out"),
    ])
    with pytest.raises(di.DeckdbInputError):  # 14-day trend window starts before the file
        mig.run_deckdb_generation(args, log=lambda _m: None)
    exp = di.load_export(export_csv)
    out = dm.working_dir(tmp_path / "out", date(2026, 10, 1), date(2026, 10, 1))
    files = dm.build_working_breakdown(exp, date(2026, 10, 1), date(2026, 10, 1), out, None, trend_weeks=0,
                                       log=lambda _m: None)
    assert "ROBOCZE" in out.name and len(files) == 9  # no trend charts with trend_weeks=0
    status = pd.read_excel(files[0], sheet_name="STATUS")
    assert exp.sha256 in status["Wartość"].astype(str).tolist()
    fin = pd.read_csv(files[1], encoding="utf-8-sig", dtype=str)
    assert set(fin["rules_version"]) == {"rv1"} and set(fin["export_sha256"]) == {exp.sha256}
