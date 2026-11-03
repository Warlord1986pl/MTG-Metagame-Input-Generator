"""Monthly package monthly/<YYYY-MM>/ (order of 2026-11-03): the runner with the DeckDB steps replaced by a synthetic
export (cards, variants, triage file) and the real pipeline. The week closing October (2026-W44) makes both folders
from one export; an ordinary week (2026-W45) only the week folder; --month 2026-10 rebuilds the same package (sheet by
sheet, PNG byte for byte); the checks stop on a mismatch."""
import csv
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import deckdb_input as di  # noqa: E402
from test_deckdb_cards import _card, _cards_csv  # noqa: E402
from test_deckdb_input import _row, _write  # noqa: E402
import deckdb_month_checks as mc  # noqa: E402
from weekly_runner import common, monthly as mo, rule_changes, runner  # noqa: E402
from weekly_runner.common import Stop  # noqa: E402

TODAY = date(2026, 11, 10)
RULE_ROWS = [{"entries": "7-9", "date": "2026-10-07", "deck_ids": "zoo", "decks": "Zoo", "change": "new deck Zoo (Aggro)"},
             {"entries": "12", "date": "2026-10-20", "deck_ids": "gone", "decks": "Gone Deck",
              "change": "deck Gone Deck removed"}]


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    """2026-08-01 .. 2026-11-08, one event of 10 lists a day: Zoo (variants), Burn, Tron, Rogue. Card X in Zoo lists:
    ~10% in September, 60% in October (a list change m02 reports)."""
    d = tmp_path_factory.mktemp("deckdb")
    rows, cards = [], []
    for n, day in enumerate(pd.date_range("2026-08-01", "2026-11-08", freq="D")):
        iso = day.date().isoformat()
        for k in range(10):
            st = "rogue" if k == 9 else "assigned"
            deck = ["Zoo", "Burn", "Tron"][k % 3] if st == "assigned" else "Rogue"
            arch = ["Aggro", "Aggro", "Ramp"][k % 3] if st == "assigned" else "Rogue"
            variant = ("Zoo: Phlage" if (n + k) % 2 else "Zoo: other") if deck == "Zoo" else ""
            rows.append(_row(1000 + n, 10 * n + k, iso, st, deck, arch, f"p{(n + k) % 13}", k + 1, variant=variant,
                             pc=50))
            dt = 10 * n + k
            cards.append(_card(1000 + n, dt, "main", "y", "Y", 4))
            cards.append(_card(1000 + n, dt, "main", "plains", "Plains", 2, basic=1))
            if deck == "Zoo" and ((iso[5:7] == "09" and (n + k) % 10 == 0) or (iso[5:7] >= "10" and (n + k) % 10 < 6)):
                cards.append(_card(1000 + n, dt, "main", "x", "X", 2))
    export = _write(d, rows, "assignments_rv1_2026-11-08.csv")
    cards_csv = _cards_csv(d, cards)
    (d / "triage_dv.json").write_text(json.dumps({"data_version": "dv", "status": "complete", "pending_groups": 0}),
                                      encoding="utf-8")
    fe = d / "fe.yaml"
    fe.write_text("events:\n- {key: x, major: true, status: confirmed, effective_mtgo: '2026-08-03'}\n",
                  encoding="utf-8")
    return {"dir": d, "export": export, "cards": cards_csv, "fe": fe}


def _patch(monkeypatch, data, tmp_path):
    def preflight(self):
        self.cfg = {"deckdb_dir": str(data["dir"]), "pipeline_python": sys.executable, "trend_weeks": "4"}
        self.facts.update(versions={"python": sys.version.split()[0]}, pipeline_commit="pipelinecommit",
                          deckdb_commit="deckdbcommit", config_commit="configcommit")

    def triage(self):
        self.facts["triage"] = {"status": "complete", "pending_groups": 0, "waiting_share_of_week": 0.0,
                                "week": {"iso_week": "2026-W45"}}
        self.facts["triage_path"] = str(data["dir"] / "triage_dv.json")

    def export(self):
        rows = pd.read_csv(data["export"], dtype=str)
        week = rows[(rows["event_date"] >= self.mon.isoformat()) & (rows["event_date"] <= self.sun.isoformat())]
        self.facts.update(export={"assignments": data["export"], "cards": data["cards"]},
                          export_sha={"assignments": common.sha256(data["export"]),
                                      "cards": common.sha256(data["cards"])},
                          rules_version="rv1", data_version="dv", events=int(week["event_id"].nunique()),
                          lists=len(week), export_events={})

    monkeypatch.setattr(runner.WeekRun, "preflight", preflight)
    monkeypatch.setattr(runner.WeekRun, "import_lists", lambda self: None)
    monkeypatch.setattr(runner.WeekRun, "triage", triage)
    monkeypatch.setattr(runner.WeekRun, "export", export)
    monkeypatch.setattr(runner.WeekRun, "verify_mtgo", lambda self: None)
    monkeypatch.setattr(runner.WeekRun, "config_text", lambda self: (
        f"key,value\nexport_path,x\nformat_events_path,{data['fe'].as_posix()}\ncards_path,x\n"))
    monkeypatch.setattr(rule_changes, "month_rule_changes", lambda deckdb_dir, ms, me: [dict(r) for r in RULE_ROWS])


def _run(tmp_path, week=None, month=None, force=False, root="monthly"):
    r = runner.WeekRun(week, tmp_path / "weekly", force, False, TODAY, tmp_path / root, month,
                       work_root=tmp_path / "work")
    r.cache_dir = tmp_path / "cache"
    r.cache_dir.mkdir(exist_ok=True)
    return r, runner.execute(r, "month" if month else "data")


@pytest.fixture(scope="module")
def w44(data, tmp_path_factory):
    mp = pytest.MonkeyPatch()
    tmp = tmp_path_factory.mktemp("w44")
    _patch(mp, data, tmp)
    try:
        r, rc = _run(tmp, week="2026-W44")
    finally:
        mp.undo()
    return tmp, r, rc


def test_closing_week_makes_week_folder_and_monthly_package(w44):
    tmp, r, rc = w44
    assert rc == 0, (r.work / "run_report_STOP.md").read_text(encoding="utf-8")
    week, month = tmp / "weekly" / "2026-W44", tmp / "monthly" / "2026-10"
    assert (week / "data.xlsx").exists() and (week / "run_report.md").exists()
    names = {p.name for p in month.iterdir()}
    assert names == {"data.xlsx", "notes.md", "m01_deck_results.png", "m02_list_changes.png", *mc.MONTH_CHARTS,
                     mo.README_NAME, mo.REPORT_NAME}
    assert (tmp / "work" / "2026-W44" / "pipeline" / "monthly" / "2026-10.charts.json").exists()  # not in the package
    assert not any(p.is_dir() for p in month.iterdir())
    xl = pd.read_excel(month / "data.xlsx", sheet_name=None)
    assert list(xl) == ["contents", *mo.PACKAGE_SHEETS]
    for name in list(xl)[1:]:
        if list(xl[name].columns) == ["Info"]:  # an empty table ("no rows"), e.g. no new card this month
            continue
        assert set(xl[name]["rules_version"]) == {"rv1"} and set(xl[name]["data_version"]) == {"dv"}
    assert list(xl["variants_month"].columns[:5]) == ["Deck", "Variant", "Lists", "Top8", "Trophies"]
    oct_zoo = 31 * 3  # k = 0, 3, 6 every day of October
    assert int(xl["variants_month"]["Lists"].sum()) == oct_zoo
    assert set(xl["m_deck_results"]["Period_end"]) == {"2026-10-31"}
    assert int(xl["m_deck_results"]["Lists"].sum()) == 90 * 10  # 2026-08-03 .. 2026-10-31, no November list
    assert xl["monthly_metrics"]["month_to"].max() == "2026-10-31"
    # format period from 2026-08-03 (fe.yaml): the first full month is September, August is partial and left out
    for name, col in (("monthly_metrics", "month"), ("share_history_months_decks", "Month"),
                      ("share_history_months_archetypes", "Month")):
        assert sorted(set(xl[name][col])) == ["2026-09", "2026-10"]
    mm = xl["monthly_metrics"]
    assert mm.loc[mm["month"] == "2026-09", "change_beyond"].isna().all()  # the first month has no test
    octd = mm[(mm["month"] == "2026-10") & (mm["level"] == "deck")]
    assert set(octd.loc[octd["tracked"] == 1, "tracked_reason"]) <= {"top10_month", "top10_prev_month",
                                                                     "largest_in_archetype"}
    assert octd.loc[octd["tracked"] == 0, "change_beyond"].isna().all()
    t8 = xl["top8_month"]
    assert len(t8) == 31 * 8 and int(t8["Winner"].sum()) == 31 and "player" not in " ".join(t8.columns).lower()
    wim = xl["weeks_in_month"]
    assert set(wim.loc[wim["Partial"] == 1, "ISO_week"]) == {"2026-W40", "2026-W44"}  # 1-4 Oct, 26-31 Oct
    notes = (month / "notes.md").read_text(encoding="utf-8")
    heads = ["## Status", "## DeckDB rule changes this month", "## Files", "## Speaker notes", "## Glossary"]
    assert [notes.index(h) for h in heads] == sorted(notes.index(h) for h in heads)
    assert "- Month: 2026-10, 2026-10-01 .. 2026-10-31, complete month" in notes
    assert "- Monthly material: 2026-10-01 .. 2026-10-31, complete month (31 events, 310 lists)" in notes
    assert "- entries 7-9, 2026-10-07: new deck Zoo (Aggro); lists of the month: Zoo 93" in notes
    assert "- entry 12, 2026-10-20: deck Gone Deck removed; lists of the month: Gone Deck 0" in notes
    assert "### Classification changes since the previous monthly export" in notes
    speaker = notes.split("## Speaker notes")[1].split("## Glossary")[0]
    assert [h for h in speaker.splitlines() if h.startswith("### ")] == [
        "### m01_deck_results.png", "### m02_list_changes.png", *[f"### {n}" for n in mc.MONTH_CHARTS]]
    for term in ("tracked decks (month)", "monthly change test (BH)", "weeks in month", "trophy timeline"):
        assert f"- **{term}** - " in notes
    assert "start with September 2026, the first full month of the format period" in notes
    assert "g-p" not in notes  # no player names
    readme = (month / mo.README_NAME).read_text(encoding="utf-8").split("\n")
    assert readme[0].startswith("> Generated: ") and "pipelinecommit" in readme[0] and "`rv1`" in readme[0]
    assert "\n".join(readme[mo.HEADER_LINES:]) == (REPO_ROOT / mo.RULES_REL).read_text(encoding="utf-8")
    rep = (month / mo.REPORT_NAME).read_text(encoding="utf-8")
    assert "BŁĄD" not in rep and rep.count("- OK: ") == 10 and "identyczne z folderem tygodnia 2026-W44" in rep
    runs = list(csv.DictReader((tmp / "weekly" / "runs.csv").open(encoding="utf-8")))
    assert runs[-1]["tydzien"] == "2026-W44" and runs[-1]["monthly_folder"].endswith("monthly/2026-10")


def test_ordinary_week_makes_only_the_week_folder(data, tmp_path, monkeypatch):
    _patch(monkeypatch, data, tmp_path)
    old = tmp_path / "weekly" / "runs.csv"  # a runs.csv from before the column monthly_folder is migrated
    old.parent.mkdir(parents=True)
    old.write_text(",".join(runner.RUNS_COLUMNS[:-1]) + "\n2026-11-02 15:03:32,2026-W44,data,OK,x,rv,14,436,complete\n",
                   encoding="utf-8")
    r, rc = _run(tmp_path, week="2026-W45")
    assert rc == 0
    assert (tmp_path / "weekly" / "2026-W45" / "data.xlsx").exists()
    assert not (tmp_path / "monthly").exists() and r.month is None
    runs = list(csv.DictReader(old.open(encoding="utf-8")))
    assert [x["monthly_folder"] for x in runs] == ["", ""] and runs[-1]["tydzien"] == "2026-W45"


def test_month_rebuild_reproduces_the_closing_week_package(w44, data, tmp_path, monkeypatch):
    tmp, _, rc = w44
    assert rc == 0
    _patch(monkeypatch, data, tmp_path)
    r, rc = _run(tmp_path, month="2026-10", root="monthly_rebuilt")
    assert rc == 0, (r.work / "run_report_monthly_STOP.md").read_text(encoding="utf-8")
    a, b = tmp / "monthly" / "2026-10", tmp_path / "monthly_rebuilt" / "2026-10"
    assert {p.name for p in a.iterdir()} == {p.name for p in b.iterdir()}
    xa, xb = pd.read_excel(a / "data.xlsx", sheet_name=None), pd.read_excel(b / "data.xlsx", sheet_name=None)
    assert list(xa) == list(xb)
    for name in xa:
        pd.testing.assert_frame_equal(xa[name], xb[name], check_exact=True)
    for name in ("m01_deck_results.png", "m02_list_changes.png"):
        assert (a / name).read_bytes() == (b / name).read_bytes()
    assert not (tmp_path / "weekly" / "2026-W44").exists()  # no week stage
    runs = list(csv.DictReader((tmp_path / "weekly" / "runs.csv").open(encoding="utf-8")))
    assert runs[-1]["etap"] == "month" and runs[-1]["monthly_folder"].endswith("monthly_rebuilt/2026-10")
    # an existing package without --force is a stop before any step
    r2, rc = _run(tmp_path, month="2026-10", root="monthly_rebuilt")
    assert rc == common.EXIT_ARGS


def test_month_arguments(tmp_path):
    assert mo.closing_month(date(2026, 11, 1)) == "2026-10"
    assert mo.closing_month(date(2026, 10, 4)) == "2026-09"
    assert mo.closing_month(date(2026, 11, 8)) is None and mo.closing_month(date(2026, 10, 25)) is None
    import deckdb_folder as fo
    for sunday in pd.date_range("2026-01-04", "2027-12-26", freq="7D"):
        s = sunday.date()
        m = fo.month_for(s)
        assert mo.closing_month(s) == (f"{m[0]:%Y-%m}" if m else None)
    r = runner.WeekRun(None, tmp_path, False, False, date(2026, 10, 31), tmp_path / "m", "2026-10")
    with pytest.raises(Stop) as e:
        r.check_args_month()
    assert e.value.code == common.EXIT_ARGS and "nie jest zakończony" in e.value.message
    assert runner.main(["--month", "2026-10", "--week", "2026-W44"], today=TODAY) == common.EXIT_ARGS
    assert runner.WeekRun("2026-W44", tmp_path, False, True, TODAY).month is None  # --test-monthly: no package


def test_checks_stop_on_a_mismatch(w44, tmp_path):
    """Point 4: a list count in notes.md that differs from the export, a README edited below its header and an m01
    colour that disagrees with the sheet's BH flag each stop the run."""
    import shutil

    tmp, r, _ = w44
    src = tmp / "monthly" / "2026-10"
    drawn = tmp / "work" / "2026-W44" / "pipeline" / "monthly" / "2026-10.charts.json"
    args = ("2026-10", r.facts["export"]["assignments"], "rv1", r.facts["export_sha"]["assignments"], "dv",
            REPO_ROOT / mo.RULES_REL)

    def copy(name):
        d = tmp_path / name
        shutil.copytree(src, d)
        return d

    d = copy("notes")
    p = d / "notes.md"
    p.write_text(p.read_text(encoding="utf-8").replace("(31 events, 310 lists)", "(31 events, 309 lists)"),
                 encoding="utf-8")
    with pytest.raises(Stop, match="notes.md: miesiąc"):
        mo.check_package(d, *args, drawn_path=drawn)
    d = copy("readme")
    with (d / mo.README_NAME).open("a", encoding="utf-8") as fh:
        fh.write("dopisek\n")
    with pytest.raises(Stop, match="poza nagłówkiem"):
        mo.check_package(d, *args, drawn_path=drawn)
    d = copy("bands")
    xl = pd.read_excel(d / "data.xlsx", sheet_name=None)
    xl["m_results_bands"].loc[0, "Colour_geometry"] = "green" if xl["m_results_bands"].loc[0, "Colour_geometry"] == \
        "grey" else "grey"
    with pd.ExcelWriter(d / "data.xlsx", engine="openpyxl") as xw:
        for name, df in xl.items():
            df.to_excel(xw, sheet_name=name, index=False)
    with pytest.raises(Stop, match="m01"):
        mo.check_package(d, *args, drawn_path=drawn)
    mo.check_package(src, *args, drawn_path=drawn)  # the original passes


def test_rule_change_rows_from_registry_diff():
    before = {"decks": [{"id": "a", "name": "Alpha", "archetype": "Aggro", "signature_cards": [1]},
                        {"id": "b", "name": "Beta", "archetype": "Combo", "signature_cards": [2],
                         "variants": {"items": [{"name": "v1"}]}}],
              "priorities": [{"higher": "a", "lower": "b", "decision": "wpis 1"}]}
    after = {"decks": [{"id": "a", "name": "Alpha Prime", "archetype": "Aggro", "signature_cards": [1, 3],
                        "decisions": ["wpis 5"]},
                       {"id": "b", "name": "Beta", "archetype": "Combo", "signature_cards": [2],
                        "variants": {"items": [{"name": "v1"}, {"name": "v2"}]}},
                       {"id": "c", "name": "Gamma", "archetype": "Ramp"}],
             "priorities": [{"higher": "b", "lower": "a", "decision": "wpis 5"}]}
    assert rule_changes.deck_changes(before, after, "a") == ["renamed Alpha -> Alpha Prime", "signature changed"]
    assert rule_changes.deck_changes(before, after, "b") == ["variants changed (1 -> 2 variants)"]
    assert rule_changes.deck_changes(before, after, "c") == ["new deck Gamma (Ramp)"]
    assert rule_changes.deck_changes(before, before, "a") == []  # e.g. a rejected signature or "variant D"
    assert rule_changes.deck_changes(before, after, "zz") == []
    texts, ids = rule_changes.priority_changes(before, after, 5)
    assert texts == ["priority Beta over Alpha Prime added"] and ids == ["a", "b"]
    dates, subjects = rule_changes.parse_decisions(
        "| # | Data decyzji | Decyzja | U | G |\n|---|---|---|---|---|\n| 5 | 2026-10-07 | **X** | y | z |\n"
        "| wpis 5 | deck:a, priorities:x | 2026-10-07T00:00:00Z | apply-decisions |\n")
    assert dates == {5: "2026-10-07"} and subjects == {5: ["deck:a", "priorities:x"]}
    assert rule_changes.entry_label([48, 49, 50, 52]) == "48-50, 52"


def test_charts_and_sheets_mismatch_stops(w44, tmp_path):
    """Point 6: a chart whose drawn colour, BH emphasis or timeline point disagrees with its sheet, a sheet row removed
    from top8_month and a May row in a monthly history each stop the run (code 11)."""
    import shutil

    tmp, r, _ = w44
    src = tmp / "monthly" / "2026-10"
    drawn = json.loads((tmp / "work" / "2026-W44" / "pipeline" / "monthly" / "2026-10.charts.json").read_text(
        encoding="utf-8"))
    args = ("2026-10", r.facts["export"]["assignments"], "rv1", r.facts["export_sha"]["assignments"], "dv",
            REPO_ROOT / mo.RULES_REL)

    def tampered(edit, name):
        d = json.loads(json.dumps(drawn))
        edit(d)
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(d), encoding="utf-8")
        return path

    def recolour(d):
        d["m05_decks_share.png"]["rows"][0]["colour"] = "#000000"

    def bold(d):
        d["m03_archetypes_share.png"]["rows"][0]["parts"][1]["weight"] = (
            "normal" if d["m03_archetypes_share.png"]["rows"][0]["parts"][1]["weight"] == "bold" else "bold")

    def drop_point(d):
        d["m11_trophy_timeline.png"]["points"].pop()

    def line(d):
        d["m06_decks_trend.png"]["lines"][0]["y"][-1] += 1.0

    for edit, what in ((recolour, "m05 .* kolor"), (bold, "m03 .* wyróżnienie"), (drop_point, "m11 liczba punktów"),
                       (line, "m06")):
        with pytest.raises(Stop, match=what) as e:
            mo.check_package(src, *args, drawn_path=tampered(edit, edit.__name__))
        assert e.value.code == common.EXIT_MONTHLY

    def rewrite(d, sheet, fn):
        xl = pd.read_excel(d / "data.xlsx", sheet_name=None)
        xl[sheet] = fn(xl[sheet])
        with pd.ExcelWriter(d / "data.xlsx", engine="openpyxl") as xw:
            for name, df in xl.items():
                df.to_excel(xw, sheet_name=name, index=False)

    path = tmp_path / "ok.json"
    path.write_text(json.dumps(drawn), encoding="utf-8")
    d = tmp_path / "top8"
    shutil.copytree(src, d)
    rewrite(d, "top8_month", lambda df: df.iloc[1:])
    with pytest.raises(Stop, match="top8_month"):
        mo.check_package(d, *args, drawn_path=path)
    d = tmp_path / "may"
    shutil.copytree(src, d)
    rewrite(d, "share_history_months_decks", lambda df: pd.concat([df.iloc[:1].assign(Month="2026-05"), df]))
    with pytest.raises(Stop, match="bez maja 2026"):
        mo.check_package(d, *args, drawn_path=path)


def test_monthly_measures_rules(tmp_path):
    """full_months leaves out the partial first month (2026-05-19 -> June); weeks_in_month cuts the ISO weeks at the
    month boundary; pilots_month concentration by >= 1/3 of the lists or >= 1/2 of the Top 8 entries; the change test
    only on tracked decks, with the monthly reasons."""
    assert di.full_months(date(2026, 5, 19), date(2026, 10, 1))[0] == (date(2026, 6, 1), date(2026, 6, 30))
    assert len(di.full_months(date(2026, 5, 19), date(2026, 10, 1))) == 5
    assert di.full_months(date(2026, 6, 1), date(2026, 6, 1)) == [(date(2026, 6, 1), date(2026, 6, 30))]
    rows = []
    for n, day in enumerate(pd.date_range("2026-09-01", "2026-10-31", freq="D")):
        for k in range(20):
            deck = ["A", "B", "C", "D"][k % 4]
            # deck D: 5 lists a day (places 4, 8, 12, 16, 20), 2 of them Top 8; pilot "lead" plays place 4 every day
            # and place 8 every other day: 30% of the D lists (< 1/3) but 75% of its Top 8 entries (>= 1/2)
            player = "lead" if deck == "D" and (k == 3 or (k == 7 and n % 2)) else f"p{(n + k) % 17}"
            rows.append(_row(5000 + n, 100 * n + k, day.date().isoformat(), "assigned", deck,
                             {"A": "Aggro", "B": "Combo", "C": "Ramp", "D": "Control"}[deck], player, k + 1, pc=40))
    exp = di.load_export(_write(tmp_path, rows))
    months = di.full_months(date(2026, 9, 1), date(2026, 10, 1))
    mm = di.monthly_metrics_tested(exp, months)
    octd = mm[(mm["month"] == "2026-10") & (mm["level"] == "deck")].set_index("name")
    assert set(octd["tracked_reason"]) == {"top10_month"} and octd["change_beyond"].notna().all()
    assert mm.loc[mm["month"] == "2026-09", "change_beyond"].isna().all()
    oct_rows = di.window_rows(exp, date(2026, 10, 1), date(2026, 10, 31))
    pm = di.pilots_month(oct_rows, ["A", "D"], {"A": "Aggro", "D": "Control"}).set_index("Deck")
    assert pm.loc["D", "Top_pilot_share_pct"] < 100 / 3 and pm.loc["D", "Concentrated"] == 1
    assert pm.loc["D", "Top_pilot_top8_share_pct"] >= 50.0
    assert pm.loc["A", "Concentrated"] == 0 and int(octd.loc["D", "concentrated"]) == 1
    wim = di.weeks_in_month(exp, date(2026, 10, 1), date(2026, 10, 31))
    first = wim[wim["ISO_week"] == "2026-W40"].iloc[0]
    assert (first["From"], first["To"], first["Partial"]) == ("2026-10-01", "2026-10-04", 1)
    assert wim.groupby("ISO_week")["Share_Top32_pct"].sum().round(9).eq(100.0).all()
