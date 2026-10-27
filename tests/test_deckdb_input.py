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


def _triage(dir_: Path, status: str, pending: int = 0, dv: str = "dv") -> Path:
    p = dir_ / f"triage_{dv}.json"
    p.write_text(json.dumps({"data_version": dv, "status": status, "pending_groups": pending}), encoding="utf-8")
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
    tbl = di.share_table(rows, "material_deck", "Deck", trophies=True).set_index("Deck")
    zoo = tbl.loc["Zoo"]
    assert zoo["Lists"] == 3 and zoo["Share_Top32_pct"] == pytest.approx(50.0)
    assert zoo["Events_with_list"] == 2 and zoo["Event_presence_pct"] == pytest.approx(100.0)
    assert zoo["Top8_lists"] == 2 and zoo["Events_with_Top8"] == 2 and zoo["Top8_event_pct"] == pytest.approx(100.0)
    assert zoo["Trophies"] == 1
    assert tbl.loc[di.NO_DECK_LABEL, "Lists"] == 2 and tbl["Lists"].sum() == len(rows)
    assert "Trophies" not in di.share_table(rows, "material_deck", "Deck", trophies=False).columns
    split = di.no_deck_split(rows, trophies=False).set_index("Status")
    assert split.loc["rogue", "Lists"] == 1 and split.loc["unassigned", "Lists"] == 1
    assert split.loc["rogue", "Share_Top32_pct"] == pytest.approx(100 / 6)


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
    t = di.conversion_table(r, "material_deck", "Deck", pilots=True).set_index("Deck")
    a, b = t.loc["A"], t.loc["B"]
    assert a["Lists"] == 16 and a["Top8_lists"] == 5 and a["Top8_conversion_pct"] == pytest.approx(500 / 16)
    assert a["Pilots_Top32"] == 12 and not math.isnan(a["CI_low_pct"])
    assert a["CI_low_pct"] <= a["Top8_conversion_pct"] <= a["CI_high_pct"]
    assert a["Top_pilot_lists"] == 5 and a["Top_pilot_share_Top32_pct"] == pytest.approx(500 / 16)
    assert a["Top_pilot_share_Top8_pct"] == pytest.approx(60.0)
    assert a["Top_pilot_conversion_pct"] == pytest.approx(60.0)
    assert a["Without_top_pilot_conversion_pct"] == pytest.approx(200 / 11)
    assert a["Pilots_Top8"] == 3
    # B: 3 pilots (< 10) and q0 holds 1/3 of the lists and the only Top 8 entry -> both labels
    assert math.isnan(b["CI_low_pct"]) and b["Pilot_labels"] == "low pilot count, concentration"
    # A: p0 has 5/16 lists (< 1/3) but 3/5 of the Top 8 entries (>= 1/2) -> concentration only
    assert a["Pilot_labels"] == "concentration"
    arch_b = di.conversion_table(r[r["deck"] == "B"], "material_archetype", "Archetype", pilots=False)
    assert arch_b.loc[0, "Pilot_labels"] == "low pilot count"  # no concentration label for archetypes
    arch = di.conversion_table(r, "material_archetype", "Archetype", pilots=False)
    assert "Top_pilot_lists" not in arch.columns and arch.loc[0, "Lists"] == 19


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
    assert list(var["Variant"]) == ["V1 Zoo"] and list(var["Month"]) == ["2026-10"]


def test_finalists_have_no_player_identifiers(tmp_path):
    _, rows = _period(tmp_path)
    fin = di.finalists_table(rows)
    assert list(fin.columns) == ["EventID", "Date", "Deck_1st", "Deck_2nd"]
    assert list(fin["Deck_1st"]) == ["Zoo"]  # event 2 has no rank 2 in this sample


def test_coverage_and_events(tmp_path):
    _, rows = _period(tmp_path)
    assert di.coverage(rows) == {"events": 2, "lists": 6, "participants": 100, "coverage_pct": 6.0}
    ev = di.event_table(rows)
    assert list(ev["ISO_week"]) == ["2026-W40", "2026-W40"] and list(ev["Month"]) == ["2026-10"] * 2


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
    _triage(tmp_path, "complete")
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
    assert {p.name for p in root.iterdir()} == {"week_2026-W42", "month_2026-10_incomplete",
                                               "format_period_2026-09-07_to_2026-10-18"}
    exp = di.load_export(export_csv)
    for x in [f for f in files if f.suffix == ".xlsx" and f.name != "data.xlsx"]:  # data.xlsx: presentation folder
        book = pd.read_excel(x, sheet_name=None)
        assert {"STATUS", "Glossary", "Omitted"} <= set(book)
        assert exp.sha256 in book["STATUS"]["Value"].astype(str).tolist()
        assert not any("Winrate" in c or "swiss" in c.lower() for s in book.values() for c in map(str, s.columns))
    assert all((d / "GLOSSARY.md").exists() for d in root.iterdir())
    assert all(f.suffix == ".png" for f in files if "share_top32" in f.name or "trend_share" in f.name
               or "results_matrix" in f.name or "change_vs_previous_week" in f.name)
    week_dir = root / "week_2026-W42"
    assert {"change_vs_previous_week_decks.png", "change_vs_previous_week_archetypes.png", "share_top32_decks.png",
            "top8_WORKING_2026-W42.csv"} <= {p.name for p in week_dir.iterdir()}
    week = pd.read_excel(week_dir / "deckdb_WORKING_2026-W42.xlsx", sheet_name=None)
    # K41: no result measure of decks in the weekly materials
    banned = ("conversion", "percentile", "top8_lists", "top8_event", "trophies", "ci_low_pct")
    assert not any(b in c.lower() for s in ("Decks", "Archetypes") for c in map(str, week[s].columns) for b in banned)
    assert {"Change_pp", "Change_CI_excludes_0", "Pilots", "Event_presence_pct"} <= set(week["Decks"].columns)
    assert not any("conversion" in t.lower() for t in week["Glossary"]["Term"])
    assert (root / "format_period_2026-09-07_to_2026-10-18" / "results_matrix_decks.png").exists()
    for x in [f for f in files if f.suffix == ".csv"]:
        c = pd.read_csv(x, encoding="utf-8-sig", dtype=str)
        assert set(c["export_sha256"]) == {exp.sha256} and not ({"player", "loginid"} & set(c.columns))
    period = pd.read_excel(root / "format_period_2026-09-07_to_2026-10-18" /
                           "deckdb_WORKING_2026-09-07_to_2026-10-18.xlsx", sheet_name=None)
    assert "Pilots" in period and "Top_pilot_lists" not in period["Archetypes"].columns
    # presentation set (order 2026-10-26 b): next to the working charts, no sha / seed / rules version inside
    pres = {d.name: sorted(p.name for p in (d / "presentation").iterdir()) for d in root.iterdir()}
    assert pres["week_2026-W42"] == ["archetypes_share.png", "decks_share.png", "speaker_notes.md",
                                     "trend_archetypes.png", "trend_decks.png"]
    assert pres["month_2026-10_incomplete"] == ["speaker_notes.md", "trend_archetypes.png", "trend_decks.png"]
    assert pres["format_period_2026-09-07_to_2026-10-18"] == ["deck_results.png", "speaker_notes.md"]
    for d in root.iterdir():
        notes = (d / "presentation" / "speaker_notes.md").read_text(encoding="utf-8")
        assert exp.sha256 not in notes and "seed" not in notes.lower() and exp.rules_version not in notes
    assert (week_dir / "share_top32_decks.png").exists()  # working charts stay
    # --deckdb-level week writes the weekly set only
    args_w = mig.parse_args(["--input-mode", "deckdb", "--deckdb-config", str(cfg), "--as-of", "2026-10-18",
                             "--trend-weeks", "3", "--outputs-base", str(tmp_path / "outw"), "--deckdb-level", "week"])
    mig.run_deckdb_generation(args_w, log=lambda _m: None)
    assert {p.name for p in dm.root_dir(tmp_path / "outw", date(2026, 10, 18)).iterdir()} == {"week_2026-W42"}
    # a period the export does not cover is refused before anything is written
    args2 = mig.parse_args(["--input-mode", "deckdb", "--deckdb-config", str(cfg), "--as-of", "2026-10-19",
                            "--outputs-base", str(tmp_path / "out2")])
    with pytest.raises(di.DeckdbInputError):
        mig.run_deckdb_generation(args2, log=lambda _m: None)
    assert not (tmp_path / "out2").exists()


def _week_rows(day0, share_a, n_events=4, lists=8, tag=""):
    """*n_events* events of *lists* lists each on consecutive days from *day0*: the first share_a x lists
    of every event are deck A, then one B, one Rogue, the rest C<k> (one deck per list)."""
    rows = []
    for e in range(n_events):
        d = (pd.Timestamp(day0) + pd.Timedelta(days=e)).date().isoformat()
        n_a = int(share_a * lists)
        for k in range(lists):
            if k < n_a:
                deck, status = "A", "assigned"
            elif k == n_a:
                deck, status = "B", "assigned"
            elif k == n_a + 1:
                deck, status = "Rogue", "rogue"
            else:
                deck, status = f"C{k}", "assigned"
            rows.append(_row(f"{tag}{e}", f"{tag}{e}{k}", d, status, deck, "Aggro", f"{tag}{e}-{k % 3}", k + 1))
    return rows


def test_week_share_table_pooling_change_and_event_bootstrap(tmp_path):
    rows = _week_rows("2026-10-05", 0.0, tag="9") + _week_rows("2026-10-12", 0.5, tag="8")
    exp = di.load_export(_write(tmp_path, rows))
    prev = di.window_rows(exp, date(2026, 10, 5), date(2026, 10, 11))
    cur = di.window_rows(exp, date(2026, 10, 12), date(2026, 10, 15))
    t = di.week_share_table(cur, prev, "material_deck", "Deck", top_n=2)
    assert list(t["Deck"]) == ["A", "B", di.OTHER_DECKS_LABEL, di.NO_DECK_LABEL]
    a, b, other = t.iloc[0], t.iloc[1], t.iloc[2]
    assert a["Share_Top32_pct"] == pytest.approx(50.0) and a["Prev_week_share_pct"] == 0.0
    # every event has the same A share -> the event bootstrap interval is the point itself and excludes 0
    assert a["Change_CI_low_pp"] == pytest.approx(50.0) and a["Change_CI_excludes_0"] == 1
    # one B per event in both weeks -> no change, the interval [0, 0] does not exclude 0
    assert b["Change_pp"] == 0.0 and b["Change_CI_excludes_0"] == 0
    assert a["Events_with_list"] == 4 and a["Week_events"] == 4 and a["Event_presence_pct"] == 100.0
    assert a["Pilots"] == 12  # 4 events x 3 pilots
    assert other["Labels_in_row"] == 2 and t["Lists"].sum() == len(cur)
    assert t["Share_Top32_pct"].sum() == pytest.approx(100.0)
    assert t["Prev_week_share_pct"].sum() == pytest.approx(100.0)
    pd.testing.assert_frame_equal(t, di.week_share_table(cur, prev, "material_deck", "Deck", top_n=2))  # fixed seed
    arch = di.week_share_table(cur, prev, "material_archetype", "Archetype")
    assert list(arch["Archetype"]) == ["Aggro", di.NO_DECK_LABEL]


def test_top8_table_has_places_variants_and_no_players(tmp_path):
    rows = _week_rows("2026-10-12", 0.5, n_events=2, lists=10)
    rows[0]["variant"] = "V A"
    exp = di.load_export(_write(tmp_path, rows))
    t = di.top8_table(di.window_rows(exp, date(2026, 10, 12), date(2026, 10, 13)))
    assert len(t) == 16 and set(t["Place"]) == set(range(1, 9)) and t["Winner"].sum() == 2
    assert t.loc[0, "Variant"] == "V A" and t.loc[t["Deck"] == di.NO_DECK_LABEL, "Variant"].eq("").all()
    assert not ({"player", "loginid", "Player"} & set(t.columns))


def test_place_percentile_and_result_flags(tmp_path):
    rows = []
    for e in range(12):  # 12 events of 4 lists: W always 1st, L always 4th, M 2nd or 3rd, X the other place
        mid = 2 if e % 2 else 3
        for deck, rank in (("W", 1), ("M", mid), ("X", 5 - mid), ("L", 4)):
            rows.append(_row(e, f"{e}{deck}", "2026-10-01", "assigned", deck, "Aggro", f"{deck}{e}", rank))
    rows.append(_row(99, 991, "2026-10-01", "assigned", "W", "Aggro", "z", 1))
    rows.append(_row(99, 992, "2026-10-01", "assigned", "L", "Aggro", "z2", 3))  # ranks 1, 3: not 1..L
    exp = di.load_export(_write(tmp_path, rows))
    r = di.window_rows(exp, date(2026, 10, 1), date(2026, 10, 1))
    first = r[r["event_id"] == "0"].set_index("deck")["place_percentile"]
    assert first.to_dict() == pytest.approx({"W": 100.0, "M": 100 / 3, "X": 200 / 3, "L": 0.0})
    assert r.loc[r["event_id"] == "99", "place_percentile"].isna().all()
    assert di.percentile_baseline(r) == pytest.approx(50.0)
    t = di.conversion_table(r, "material_deck", "Deck", pilots=True).set_index("Deck")
    assert t.loc["W", "Mean_place_percentile"] == pytest.approx(100.0)
    assert t.loc["W", "Percentile_CI_excludes_baseline"] == 1 and t.loc["L", "Percentile_CI_excludes_baseline"] == 1
    assert t.loc["M", "Mean_place_percentile"] == pytest.approx(50.0)
    assert t.loc["M", "Percentile_CI_excludes_baseline"] == 0
    # every list is in the Top 8, conversion = baseline = 100% -> no conversion interval excludes it
    assert t["Conversion_CI_excludes_baseline"].sum() == 0


def test_results_concentration_top_tenth(tmp_path):
    rows = []
    for k in range(20):  # 20 pilots, one event each (t0..t2 in the Top 8); t0 also wins 2 more events
        rows.append(_row(k, k, "2026-10-01", "assigned", "A", "Aggro", f"t{k}", 1 if k < 3 else 20))
    rows += [_row(50 + k, 500 + k, "2026-10-02", "assigned", "A", "Aggro", "t0", 1) for k in range(2)]
    exp = di.load_export(_write(tmp_path, rows))
    c = di.results_concentration(di.window_rows(exp, date(2026, 10, 1), date(2026, 10, 2)))
    # place ceil(0.1 x 20) = 2 has 1 Top 8 entry: t0 (3) and the tied t1, t2 (1 each) are all in the group
    assert c["Pilots"] == 20 and c["Top_pilots"] == 3 and c["Top_pilots_pct"] == pytest.approx(15.0)
    assert c["Min_Top8_in_group"] == 1 and c["Pilots_at_threshold"] == 2
    assert c["Group"] == "top 15% of pilots (all with at least 1 Top 8 entry)"
    assert c["Top8_entries"] == 5 and c["Top_pilots_share_Top8_pct"] == pytest.approx(100.0)
    assert c["Top_pilots_share_lists_pct"] == pytest.approx(5 / 22 * 100)


def test_triage_gate_and_pending_marker(tmp_path):
    """DeckDB K43: no triage file or a pending one refuses the run; --allow-pending-triage marks every material."""
    import deckdb_materials as dm
    import metagame_input_generator as mig

    rows = []
    days = pd.date_range("2026-09-01", "2026-10-18", freq="D")
    for n, d in enumerate(days):
        for k in range(10):
            rows.append(_row(1000 + n, 10 * n + k, d.date().isoformat(), "assigned", ["Zoo", "Burn"][k % 2], "Aggro",
                             f"p{(n + k) % 13}", k + 1, pc=50))
    export_csv = _write(tmp_path, rows)
    fe = tmp_path / "fe.yaml"
    fe.write_text("events:\n- {key: x, major: true, status: confirmed, effective_mtgo: '2026-09-07'}\n", encoding="utf-8")
    cfg = tmp_path / "cfg.csv"
    cfg.write_text(f"key,value\nexport_path,{export_csv.as_posix()}\nformat_events_path,{fe.as_posix()}\n",
                   encoding="utf-8")
    base = ["--input-mode", "deckdb", "--deckdb-config", str(cfg), "--as-of", "2026-10-18", "--trend-weeks", "3"]
    with pytest.raises(di.DeckdbInputError, match="triage"):  # no triage file
        mig.run_deckdb_generation(mig.parse_args(base + ["--outputs-base", str(tmp_path / "o1")]), log=lambda _m: None)
    _triage(tmp_path, "pending", 3)
    with pytest.raises(di.DeckdbInputError, match="pending"):
        mig.run_deckdb_generation(mig.parse_args(base + ["--outputs-base", str(tmp_path / "o2")]), log=lambda _m: None)
    assert not (tmp_path / "o1").exists() and not (tmp_path / "o2").exists()
    _triage(tmp_path, "pending", 3, dv="other")  # a triage file of another data_version does not count
    mark = "triage pending: 3 list group(s) awaiting decision"
    mig.run_deckdb_generation(mig.parse_args(base + ["--outputs-base", str(tmp_path / "o3"), "--allow-pending-triage",
                                                     "--deckdb-level", "week"]), log=lambda _m: None)
    week = dm.root_dir(tmp_path / "o3", date(2026, 10, 18)) / "week_2026-W42"
    assert mark in (week / "status.md").read_text(encoding="utf-8")
    assert mark in (week / "presentation" / "speaker_notes.md").read_text(encoding="utf-8")
    status = pd.read_excel(week / "deckdb_WORKING_2026-W42.xlsx", sheet_name="STATUS")
    assert mark in status["Value"].astype(str).tolist()
    assert dm.TRIAGE is None  # the marker does not leak into the next run


def _folder_setup(tmp_path):
    rows = []
    days = pd.date_range("2026-08-01", "2026-10-18", freq="D")
    for n, d in enumerate(days):
        for k in range(10):
            st = "rogue" if k == 9 else "assigned"
            rows.append(_row(1000 + n, 10 * n + k, d.date().isoformat(), st,
                             ["Zoo", "Burn", "Tron"][k % 3] if st == "assigned" else "Rogue",
                             ["Midrange", "Aggro", "Ramp"][k % 3] if st == "assigned" else "Rogue",
                             f"p{(n + k) % 13}", k + 1, pc=50))
    export_csv = _write(tmp_path, rows)
    fe = tmp_path / "fe.yaml"
    fe.write_text("events:\n- {key: x, major: true, status: confirmed, effective_mtgo: '2026-08-03'}\n", encoding="utf-8")
    cfg = tmp_path / "cfg.csv"
    cfg.write_text(f"key,value\nexport_path,{export_csv.as_posix()}\nformat_events_path,{fe.as_posix()}\n",
                   encoding="utf-8")
    return ["--input-mode", "deckdb", "--deckdb-config", str(cfg), "--as-of", "2026-10-18", "--trend-weeks", "4"]


def test_pending_notice_runs_without_marks_and_blocking_refuses(tmp_path):
    """Order of 2026-10-26 k, B1: pending_notice runs (notice in status.md and speaker notes only, no chart mark),
    pending_blocking is refused without --allow-pending-triage."""
    import deckdb_presentation as dp
    import metagame_input_generator as mig

    base = _folder_setup(tmp_path)
    p = tmp_path / "triage_dv.json"
    p.write_text(json.dumps({"data_version": "dv", "status": "pending_blocking", "pending_groups": 2,
                             "waiting": [{"item_key": "k1", "lists_in_week": 5, "days_waiting": 30}]}), encoding="utf-8")
    with pytest.raises(di.DeckdbInputError, match="pending_blocking"):
        mig.run_deckdb_generation(mig.parse_args(base + ["--outputs-base", str(tmp_path / "o1")]), log=lambda _m: None)
    p.write_text(json.dumps({"data_version": "dv", "status": "pending_notice", "pending_groups": 2,
                             "waiting_share_of_week": 0.012}), encoding="utf-8")
    seen = []
    orig = dp.footer
    dp.footer = lambda *a, **k: seen.append(orig(*a, **k)) or seen[-1]
    try:
        mig.run_deckdb_generation(mig.parse_args(base + ["--outputs-base", str(tmp_path / "o2"), "--deckdb-level",
                                                         "week"]), log=lambda _m: None)
    finally:
        dp.footer = orig
    notice = "2 list group(s) awaiting review, 1.2% of this week's lists, shown as Rogue & unclassified"
    folder = tmp_path / "o2" / "presentation" / "2026-W42"
    notes = (folder / "notes.md").read_text(encoding="utf-8")
    status, speaker = notes.split("## Files")[0], notes.split("## Speaker notes")[1].split("## Glossary")[0]
    assert notice in status and notice in speaker
    assert seen and not any("triage" in f or "awaiting" in f for f in seen)  # no chart carries it
    assert dp.MARK == "" and dp.NOTICE == ""


def test_presentation_folder_flat_with_month(tmp_path):
    """Order of 2026-10-26 k, B7 and its correction: one flat folder outputs/presentation/<year>-W<nn>/ of at most 13
    files: the numbered charts, data.xlsx (contents + one sheet per table) and notes.md (Status, Files, Speaker notes,
    Glossary); the month joins on the first week of a month or when forced."""
    import deckdb_folder as fo
    import metagame_input_generator as mig

    base = _folder_setup(tmp_path)
    (tmp_path / "triage_dv.json").write_text(json.dumps({"data_version": "dv", "status": "complete",
                                                         "pending_groups": 0}), encoding="utf-8")
    mig.run_deckdb_generation(mig.parse_args(base + ["--outputs-base", str(tmp_path / "o"), "--deckdb-level", "week",
                                                     "--presentation-month", "2026-09"]), log=lambda _m: None)
    folder = tmp_path / "o" / "presentation" / "2026-W42"
    names = {p.name for p in folder.iterdir()}
    assert not any(p.is_dir() for p in folder.iterdir())
    week = set(fo.WEEK_FILES)
    # no card export in this test: no m02_list_changes.png and no m_list_changes sheet (12 files; 13 with cards)
    assert names == week | {"m01_deck_results.png"} and len(names) == 12 <= fo.MAX_FILES
    xl = pd.read_excel(folder / "data.xlsx", sheet_name=None)
    assert list(xl) == ["contents", *fo.SHEETS, "m_deck_results"]
    assert xl["contents"]["sheet"].tolist() == list(xl)[1:]
    assert "Rogue & unclassified" in xl["decks"]["Deck"].tolist()
    # K41: the monthly deck results cover the format period (2026-08-03, fe.yaml) up to the end of September
    assert int(xl["m_deck_results"]["Lists"].sum()) == 59 * 10
    notes = (folder / "notes.md").read_text(encoding="utf-8")
    heads = [h for h in ("## Status", "## Files", "## Speaker notes", "## Glossary")]
    assert [notes.index(h) for h in heads] == sorted(notes.index(h) for h in heads)
    assert all(f"`{n}`" in notes for n in names)
    assert fo.month_for(date(2026, 11, 1)) == (date(2026, 10, 1), date(2026, 10, 31))  # Sunday 1 Nov: first week
    assert fo.month_for(date(2026, 10, 4)) == (date(2026, 9, 1), date(2026, 9, 30))
    assert fo.month_for(date(2026, 10, 25)) is None
