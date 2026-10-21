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


def _row(eid, dtid, day, status, deck, arch, player, rank, variant="", rv="rv1", pc=40):
    return {
        "event_id": str(eid), "decktournamentid": str(dtid), "loginid": f"L{player}", "event_date": day,
        "status": status, "deck": deck, "archetype": arch, "rules_version": rv, "name_match_version": "2",
        "variant": variant, "player": f"g-{player}", "final_rank": rank, "player_count": pc, "data_version": "dv",
    }


def _write(tmp_path: Path, rows, name="export.csv") -> Path:
    p = tmp_path / name
    pd.DataFrame(rows, columns=COLS).to_csv(p, index=False, encoding="utf-8")
    return p


def _sample(tmp_path: Path) -> Path:
    return _write(tmp_path, [
        _row(1, 11, "2026-10-01", "assigned", "Zoo", "Aggro", "a", 1),
        _row(1, 12, "2026-10-01", "assigned", "Zoo", "Aggro", "b", 9),
        _row(1, 13, "2026-10-01", "rogue", "Rogue", "Rogue", "c", 2),
        _row(2, 21, "2026-10-02", "assigned", "Zoo", "Aggro", "a", 3, pc=60),
        _row(2, 22, "2026-10-02", "assigned", "Burn", "Aggro", "d", 12, pc=60),
        _row(2, 23, "2026-10-02", "unassigned", "Unassigned", "Unassigned", "e", 1, pc=60),
    ])


def _period(tmp_path):
    exp = di.load_export(_sample(tmp_path))
    return exp, di.window_rows(exp, date(2026, 10, 1), date(2026, 10, 2))


def test_refuses_more_than_one_rules_version(tmp_path):
    p = _write(tmp_path, [
        _row(1, 11, "2026-10-01", "assigned", "Zoo", "Aggro", "a", 1),
        _row(1, 12, "2026-10-01", "assigned", "Zoo", "Aggro", "b", 2, rv="rv2"),
    ])
    with pytest.raises(di.DeckdbInputError, match="rules_version"):
        di.load_export(p)


def test_refuses_missing_or_mixed_data_version(tmp_path):
    rows = [_row(1, 11, "2026-10-01", "assigned", "Zoo", "Aggro", "a", 1),
            _row(1, 12, "2026-10-01", "assigned", "Zoo", "Aggro", "b", 2)]
    no_col = tmp_path / "no_col.csv"
    pd.DataFrame(rows).drop(columns="data_version").to_csv(no_col, index=False)
    with pytest.raises(di.DeckdbInputError, match="data_version"):
        di.load_export(no_col)
    rows[1]["data_version"] = "dv2"
    with pytest.raises(di.DeckdbInputError, match="data_version"):
        di.load_export(_write(tmp_path, rows))
    rows[0]["data_version"] = rows[1]["data_version"] = ""
    with pytest.raises(di.DeckdbInputError, match="data_version"):
        di.load_export(_write(tmp_path, rows))


def test_refuses_period_ending_after_last_event_date(tmp_path):
    exp = di.load_export(_sample(tmp_path))
    with pytest.raises(di.DeckdbInputError, match="ostatniej dacie"):
        di.window_rows(exp, date(2026, 10, 1), date(2026, 10, 3))
    assert len(di.window_rows(exp, date(2026, 10, 2), date(2026, 10, 2))) == 3  # closed both sides


def test_config_has_no_default_path(tmp_path):
    cfg = tmp_path / "cfg.csv"
    cfg.write_text("key,value\nexport_path,\n", encoding="utf-8")
    with pytest.raises(di.DeckdbInputError):
        di.load_deckdb_config(cfg)
    with pytest.raises(di.DeckdbInputError):
        di.load_deckdb_config(tmp_path / "missing.csv")
    cfg.write_text("key,value\nexport_path,x.csv\nformat_events_path,\n", encoding="utf-8")
    c = di.load_deckdb_config(cfg)
    assert c.export_path == Path("x.csv") and c.format_events_path is None and c.cards_path is None


def test_time_levels():
    weeks = di.complete_iso_weeks(date(2026, 10, 18), 2)  # a Sunday
    assert weeks == [(date(2026, 10, 5), date(2026, 10, 11)), (date(2026, 10, 12), date(2026, 10, 18))]
    assert di.complete_iso_weeks(date(2026, 10, 21), 1) == [(date(2026, 10, 12), date(2026, 10, 18))]
    assert di.iso_week_label(date(2026, 10, 12)) == "2026-W42"
    assert di.month_period(date(2026, 10, 18)) == (date(2026, 10, 1), date(2026, 10, 18), False)
    assert di.month_period(date(2026, 9, 30)) == (date(2026, 9, 1), date(2026, 9, 30), True)


def test_format_period_start_is_last_confirmed_major(tmp_path):
    y = tmp_path / "fe.yaml"
    y.write_text(
        "events:\n"
        "- {key: a, major: true, status: confirmed, effective_mtgo: '2026-05-19'}\n"
        "- {key: b, major: false, status: confirmed, effective_mtgo: '2026-08-11'}\n"
        "- {key: c, major: true, status: proposal, effective_mtgo: '2026-09-01'}\n"
        "- {key: d, major: true, status: confirmed, effective_mtgo: '2026-11-01'}\n", encoding="utf-8")
    assert di.format_period_start(y, date(2026, 10, 18)) == (date(2026, 5, 19), "a")
    with pytest.raises(di.DeckdbInputError):
        di.format_period_start(None, date(2026, 10, 18))


def test_share_table_week_month_measures(tmp_path):
    _, rows = _period(tmp_path)
    tbl = di.share_table(rows, "material_deck", "Talia", trophies=True).set_index("Talia")
    zoo = tbl.loc["Zoo"]
    assert zoo["Listy"] == 3 and zoo["Udzial_Top32_pct"] == pytest.approx(50.0)
    assert zoo["Eventy_z_lista"] == 2 and zoo["Obecnosc_pct_eventow"] == pytest.approx(100.0)
    assert zoo["Top8"] == 2 and zoo["Eventy_z_Top8"] == 2 and zoo["Top8_pct_eventow"] == pytest.approx(100.0)
    assert zoo["Trofea"] == 1
    assert tbl.loc[di.NO_DECK_LABEL, "Listy"] == 2 and tbl["Listy"].sum() == len(rows)
    assert "Trofea" not in di.share_table(rows, "material_deck", "Talia", trophies=False).columns
    split = di.no_deck_split(rows, trophies=False).set_index("Status")
    assert split.loc["rogue", "Listy"] == 1 and split.loc["unassigned", "Listy"] == 1
    assert split.loc["rogue", "Udzial_Top32_pct"] == pytest.approx(100 / 6)


def test_conversion_baseline_pilots_and_bootstrap_threshold(tmp_path):
    rows = []
    # deck A: 12 pilots; pilot p0 has 5 lists (3 in Top 8), others 1 list each (2 in Top 8 among them)
    for i in range(5):
        rows.append(_row(i + 1, 100 + i, "2026-10-01", "assigned", "A", "Aggro", "p0", 1 if i < 3 else 20))
    for k in range(1, 12):
        rows.append(_row(20 + k, 200 + k, "2026-10-01", "assigned", "A", "Aggro", f"p{k}", 5 if k <= 2 else 25))
    # deck B: 3 pilots only
    for k in range(3):
        rows.append(_row(40 + k, 300 + k, "2026-10-01", "assigned", "B", "Aggro", f"q{k}", 2 if k == 0 else 30))
    exp = di.load_export(_write(tmp_path, rows))
    r = di.window_rows(exp, date(2026, 10, 1), date(2026, 10, 1))
    assert di.top8_baseline(r) == pytest.approx(6 / 19 * 100)
    t = di.conversion_table(r, "material_deck", "Talia", pilots=True).set_index("Talia")
    a, b = t.loc["A"], t.loc["B"]
    assert a["Listy"] == 16 and a["Top8"] == 5 and a["Konwersja_pct"] == pytest.approx(500 / 16)
    assert a["Piloci_Top32"] == 12 and not math.isnan(a["Przedzial_dol_pct"])
    assert a["Przedzial_dol_pct"] <= a["Konwersja_pct"] <= a["Przedzial_gora_pct"]
    assert a["Najcz_pilot_listy"] == 5 and a["Najcz_pilot_udzial_Top32_pct"] == pytest.approx(500 / 16)
    assert a["Najcz_pilot_udzial_Top8_pct"] == pytest.approx(60.0)
    assert a["Najcz_pilot_konwersja_pct"] == pytest.approx(60.0)
    assert a["Bez_najcz_pilota_konwersja_pct"] == pytest.approx(200 / 11)
    assert a["Piloci_Top8"] == 3
    # B: 3 pilots (< 10) and q0 holds 1/3 of the lists and the only Top 8 entry -> both labels
    assert math.isnan(b["Przedzial_dol_pct"]) and b["Etykieta_pilotow"] == "mało pilotów, koncentracja"
    # A: p0 has 5/16 lists (< 1/3) but 3/5 of the Top 8 entries (>= 1/2) -> concentration only
    assert a["Etykieta_pilotow"] == "koncentracja"
    arch_b = di.conversion_table(r[r["deck"] == "B"], "material_archetype", "Archetyp", pilots=False)
    assert arch_b.loc[0, "Etykieta_pilotow"] == "mało pilotów"  # no concentration label for archetypes
    arch = di.conversion_table(r, "material_archetype", "Archetyp", pilots=False)
    assert "Najcz_pilot_listy" not in arch.columns and arch.loc[0, "Listy"] == 19


def test_variant_threshold_d2_per_month(tmp_path):
    rows = []
    for i in range(20):  # October: 20 Zoo lists, V1 x15 (3 players), V2 x3 (1 player), V3 x2 (2 players)
        variant = "V1 Zoo" if i < 15 else ("V2 Zoo" if i < 18 else "V3 Zoo")
        player = f"p{i % 3}" if i < 15 else ("solo" if i < 18 else f"q{i}")
        rows.append(_row(1, 100 + i, "2026-10-30", "assigned", "Zoo", "Aggro", player, i + 1, variant=variant))
    for i in range(10):  # November: 10 Zoo lists, 3 x "W Zoo" by 2 players -- month below 20 lists
        rows.append(_row(2, 400 + i, "2026-11-02", "assigned", "Zoo", "Aggro", f"n{i % 2}" if i < 3 else f"m{i}",
                         i + 1, variant="W Zoo" if i < 3 else ""))
    exp = di.load_export(_write(tmp_path, rows))
    var = di.variant_table(di.window_rows(exp, date(2026, 10, 30), date(2026, 11, 2)))
    assert list(var["Wariant"]) == ["V1 Zoo"] and list(var["Miesiac"]) == ["2026-10"]


def test_finalists_have_no_player_identifiers(tmp_path):
    _, rows = _period(tmp_path)
    fin = di.finalists_table(rows)
    assert list(fin.columns) == ["EventID", "Data", "Talia_1", "Talia_2"]
    assert list(fin["Talia_1"]) == ["Zoo"]  # event 2 has no rank 2 in this sample


def test_coverage_and_events(tmp_path):
    _, rows = _period(tmp_path)
    assert di.coverage(rows) == {"events": 2, "lists": 6, "participants": 100, "coverage_pct": 6.0}
    ev = di.event_table(rows)
    assert list(ev["Tydzien_ISO"]) == ["2026-W40", "2026-W40"] and list(ev["Miesiac"]) == ["2026-10"] * 2


def test_match_mtgo_cache_counts_both_ways(tmp_path):
    _, rows = _period(tmp_path)
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
    assert res["matched"] == 2 and res["export_not_in_cache"] == 4 and res["cache_not_in_export"] == 1
    assert res["export_events_not_in_cache"] == 1 and res["cache_events_not_in_export"] == 0


def test_materials_three_levels_and_cli_mode(tmp_path):
    import deckdb_materials as dm
    import metagame_input_generator as mig

    rows = []
    days = pd.date_range("2026-09-01", "2026-10-18", freq="D")
    for n, d in enumerate(days):
        for k in range(10):
            deck = ["Zoo", "Burn", "Tron"][k % 3]
            rows.append(_row(1000 + n, 10 * n + k, d.date().isoformat(), "assigned", deck,
                             "Ramp" if deck == "Tron" else "Aggro", f"p{(n + k) % 13}", k + 1, pc=50))
    export_csv = _write(tmp_path, rows)
    fe = tmp_path / "fe.yaml"
    fe.write_text("events:\n- {key: x, major: true, status: confirmed, effective_mtgo: '2026-09-07'}\n",
                  encoding="utf-8")
    cfg = tmp_path / "cfg.csv"
    cfg.write_text(f"key,value\nexport_path,{export_csv.as_posix()}\nformat_events_path,{fe.as_posix()}\n",
                   encoding="utf-8")
    out = tmp_path / "out"
    args = mig.parse_args(["--input-mode", "deckdb", "--deckdb-config", str(cfg), "--as-of", "2026-10-18",
                           "--trend-weeks", "3", "--outputs-base", str(out)])
    files = mig.run_deckdb_generation(args, log=lambda _m: None)
    root = dm.root_dir(out, date(2026, 10, 18))
    assert {p.name for p in root.iterdir()} == {"tydzien_2026-W42", "miesiac_2026-10_niepelny",
                                               "okres_formatu_2026-09-07_to_2026-10-18"}
    exp = di.load_export(export_csv)
    for x in [f for f in files if f.suffix == ".xlsx"]:
        book = pd.read_excel(x, sheet_name=None)
        assert {"STATUS", "Slownik", "Pominiete"} <= set(book)
        assert exp.sha256 in book["STATUS"]["Wartość"].astype(str).tolist()
        assert not any("Winrate" in c or "swiss" in c.lower() for s in book.values() for c in map(str, s.columns))
    for x in [f for f in files if f.suffix == ".csv"]:
        c = pd.read_csv(x, encoding="utf-8-sig", dtype=str)
        assert set(c["export_sha256"]) == {exp.sha256} and not ({"player", "loginid"} & set(c.columns))
    period = pd.read_excel(root / "okres_formatu_2026-09-07_to_2026-10-18" /
                           "deckdb_ROBOCZE_2026-09-07_to_2026-10-18.xlsx", sheet_name=None)
    assert "Piloci" in period and "Najcz_pilot_listy" not in period["Archetypy"].columns
    # a period the export does not cover is refused before anything is written
    args2 = mig.parse_args(["--input-mode", "deckdb", "--deckdb-config", str(cfg), "--as-of", "2026-10-19",
                            "--outputs-base", str(tmp_path / "out2")])
    with pytest.raises(di.DeckdbInputError):
        mig.run_deckdb_generation(args2, log=lambda _m: None)
    assert not (tmp_path / "out2").exists()
