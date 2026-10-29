"""Synthetic tests of the week runner (metagame_week.py) and the Friday triage check (triage_check.py), without
network, DeckDB or pipeline: triage block, events missing / extra vs mtgo.com, no network, existing week folder."""
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from weekly_runner import common, mtgo_check, runner, triage_check  # noqa: E402
from weekly_runner.common import Stop  # noqa: E402


def _page(y, m, links):
    body = "".join(f'<a href="/decklist/{s}">x</a>' for s in links)
    return f"<html><title>{y}-{m:02d} Decklists</title>{body}</html>"


OCT = _page(2026, 10, [
    "modern-challenge-32-2026-10-1812856599", "modern-challenge-64-2026-10-1912856886",
    "modern-challenge-16-2026-10-2012856622", "modern-league-2026-10-2012856700",
    "modern-challenge-32-2026-10-2512856984", "modern-challenge-32-2026-10-2612856997",
    "pauper-challenge-32-2026-10-3112857999"])
WEEK = (date(2026, 10, 19), date(2026, 10, 25))
EXPORT = {"12856599": "2026-10-18", "12856886": "2026-10-19", "12856622": "2026-10-20", "12856984": "2026-10-25"}


def _listed():
    return {e.event_id: e for e in mtgo_check.parse_month(OCT, 2026, 10, date(2026, 11, 2))}


# --- mtgo.com check ---------------------------------------------------------------------------------------------

def test_parse_month_takes_modern_challenges_with_size():
    listed = _listed()
    assert sorted(listed) == ["12856599", "12856622", "12856886", "12856984", "12856997"]
    assert listed["12856886"].size == 64 and listed["12856622"].date == "2026-10-20"


def test_verify_matching_window_and_neighbours():
    v = mtgo_check.verify(*WEEK, _listed(), EXPORT)
    assert [r["event_id"] for r in v.rows] == ["12856886", "12856622", "12856984"]
    assert v.before["event_id"] == "12856599" and v.after["event_id"] == "12856997"
    assert v.before["outside"] and v.after["outside"] and not v.warnings


def test_missing_event_in_export_stops_with_its_id():
    export = {k: d for k, d in EXPORT.items() if k != "12856622"}
    with pytest.raises(Stop) as e:
        mtgo_check.verify(*WEEK, _listed(), export)
    assert e.value.code == common.EXIT_MTGO and "brak w eksporcie: 12856622" in e.value.message


def test_extra_event_in_export_stops_with_its_id():
    with pytest.raises(Stop) as e:
        mtgo_check.verify(*WEEK, _listed(), {**EXPORT, "12899999": "2026-10-22"})
    assert e.value.code == common.EXIT_MTGO and "brak na mtgo.com: 12899999" in e.value.message


def test_one_day_shift_is_a_warning_with_the_week_more_is_a_stop():
    v = mtgo_check.verify(*WEEK, _listed(), {**EXPORT, "12856984": "2026-10-26"})
    assert any("12856984" in w and "2026-W44" in w for w in v.warnings)
    with pytest.raises(Stop) as e:
        mtgo_check.verify(*WEEK, _listed(), {**EXPORT, "12856984": "2026-10-23"})
    assert "więcej niż 1 dzień" in e.value.message


@pytest.mark.parametrize("html", ["<html><title>x</title></html>", _page(2026, 10, ["weird-link"]),
                                  _page(2026, 10, ["modern-league-2026-10-0112855000"])])
def test_changed_page_structure_stops(html):
    with pytest.raises(Stop) as e:
        mtgo_check.parse_month(html, 2026, 10, date(2026, 10, 2))
    assert e.value.code == common.EXIT_MTGO


def test_cut_page_is_retried_then_stops():
    class R:
        status_code, text = 200, "<html><title>cut</title></html>"
    calls = []
    with pytest.raises(Stop) as e:
        mtgo_check.fetch_month(2026, 10, date(2026, 10, 29), get=lambda url: calls.append(url) or R(),
                               pauses=(0, 0), sleep=lambda s: None)
    assert len(calls) == 3 and "brak linków" in e.value.message


# --- no network -------------------------------------------------------------------------------------------------

def _offline(*a, **k):
    raise requests.ConnectionError("offline")


def test_no_network_stops(monkeypatch):
    monkeypatch.setattr(requests, "get", _offline)
    with pytest.raises(Stop) as e:
        common.check_network()
    assert e.value.code == common.EXIT_PREFLIGHT and "brak dostępu do sieci" in e.value.message
    with pytest.raises(Stop) as e:
        mtgo_check.fetch_month(2026, 10, date(2026, 10, 29), pauses=(0,), sleep=lambda s: None)
    assert "brak sieci" in e.value.message


def test_triage_check_without_network_is_code_3(monkeypatch, tmp_path):
    monkeypatch.setattr(requests, "get", _offline)
    assert triage_check.main(["--no-toast", "--weekly-root", str(tmp_path)]) == 3


@pytest.mark.skipif(not (REPO_ROOT / "docs" / "runner_config.csv").exists(), reason="runner config")
def test_runner_cli_without_network_stops(tmp_path):
    """The real CLI with an unreachable proxy: the preflight stops (exit 3) before any DeckDB write."""
    env = {**os.environ, "HTTPS_PROXY": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9",
           "PYTHONIOENCODING": "utf-8"}
    p = subprocess.run([sys.executable, str(REPO_ROOT / "metagame_week.py"), "--week", "2026-W42", "--weekly-root",
                        str(tmp_path)], cwd=str(REPO_ROOT), env=env, capture_output=True, text=True,
                       encoding="utf-8")
    assert p.returncode == common.EXIT_PREFLIGHT, p.stdout + p.stderr
    assert "brak dostępu do sieci" in p.stderr


# --- week folder and arguments ----------------------------------------------------------------------------------

def _run(tmp_path, week="2026-W43", force=False, test_monthly=False):
    return runner.WeekRun(week, tmp_path, force, test_monthly, date(2026, 10, 29))


def test_existing_week_folder_without_force_stops(tmp_path):
    (tmp_path / "2026-W43").mkdir()
    (tmp_path / "2026-W43" / "deck_w43.json").write_text("{}")  # content files alone do not count
    _run(tmp_path).check_args()
    (tmp_path / "2026-W43" / "data.xlsx").write_bytes(b"x")
    with pytest.raises(Stop) as e:
        _run(tmp_path).check_args()
    assert e.value.code == common.EXIT_ARGS and "--force" in e.value.message
    _run(tmp_path, force=True).check_args()


def test_week_arguments():
    assert common.last_complete_week(date(2026, 10, 29)) == "2026-W43"
    assert common.last_complete_week(date(2026, 11, 1)) == "2026-W43"  # Sunday: W44 is not complete yet
    assert common.last_complete_week(date(2026, 11, 2)) == "2026-W44"
    assert common.parse_week("2026-W40") == (date(2026, 9, 28), date(2026, 10, 4))
    with pytest.raises(Stop):
        common.parse_week("2026-W99")


def test_running_week_and_test_monthly_in_a_closing_week_stop(tmp_path):
    with pytest.raises(Stop):
        _run(tmp_path, week="2026-W44").check_args()
    with pytest.raises(Stop) as e:
        runner.WeekRun("2026-W40", tmp_path, False, True, date(2026, 10, 29)).check_args()
    assert "--test-monthly" in e.value.message


# --- triage -------------------------------------------------------------------------------------------------------

def _state(status, waiting):
    return {"data_version": "dv", "rules_version": "rv", "last_challenge": "2026-10-23", "status": status,
            "pending_groups": len(waiting), "waiting": waiting,
            "waiting_share_of_week": sum(w["share_of_week"] for w in waiting),
            "week": {"iso_week": "2026-W43", "since": "2026-10-19", "until": "2026-10-25", "lists": 300},
            "blocking": {"share_threshold": 0.03, "days_threshold": 28},
            "new_lists": {"since": "2026-10-19", "until": "2026-10-23", "lists": 20, "groups": []}, "windows": []}


GROUP = {"item_key": "signature_accept:new-thing:triage-2026-W43", "working_name": "New Thing", "lists": 12,
         "lists_in_week": 12, "share_of_week": 0.04, "days_waiting": 0, "state": "awaiting decision",
         "added_to_queue": "2026-10-23"}


def test_triage_blocking_stops_with_groups_and_instruction(tmp_path, monkeypatch):
    path = tmp_path / "triage_dv.json"
    path.write_text(json.dumps(_state("pending_blocking", [GROUP])), encoding="utf-8")
    monkeypatch.setattr(runner, "probe", lambda *a, **k: {"triage_json": str(path)})
    r = _run(tmp_path)
    r.cfg = {"deckdb_dir": str(tmp_path)}
    with pytest.raises(Stop) as e:
        r.triage()
    m = e.value.message
    assert e.value.code == common.EXIT_TRIAGE
    assert "1 grup(a)" in m and "4.0%" in m and GROUP["item_key"] in m and "rozstrzygnij" in m


@pytest.mark.parametrize("status,code", [("pending_notice", 1), ("pending_blocking", 2)])
def test_triage_check_unclassified_group_gives_pending_and_toast(tmp_path, monkeypatch, status, code):
    tj = tmp_path / "triage.json"
    tj.write_text(json.dumps(_state(status, [GROUP])), encoding="utf-8")
    week = {"since": "2026-10-19", "until": "2026-10-23", "lists": 300, "rogue_unclassified": 24, "rogue": 10,
            "unassigned": 14, "events": 9, "share": 0.08}
    prev = {**week, "since": "2026-10-12", "until": "2026-10-18", "rogue_unclassified": 15, "share": 0.05}
    answers = {"info": {"last_challenge": "2026-10-23"}, "triage": {"triage_json": str(tj)},
               "detail": {"groups": [{"item_key": GROUP["item_key"], "core": ["Card A", "Card B"],
                                      "examples": [{"event_id": "12856958", "decktournamentid": "777", "date": "x"}]}],
                          "week": week, "previous_week": prev}}
    monkeypatch.setattr(triage_check, "load_runner_config", lambda: {})
    monkeypatch.setattr(triage_check, "check_network", lambda **k: None)
    monkeypatch.setattr(triage_check, "backfill_and_import", lambda *a: {"2026-10": {"listed": 9, "fetched": 1}})
    monkeypatch.setattr(triage_check, "probe", lambda cfg, args, *a: answers[next(x for x in args if x in answers)])
    toasts = []
    monkeypatch.setattr(triage_check, "toast", lambda title, text: toasts.append(text))
    rc, out = triage_check.check(date(2026, 10, 23), tmp_path)
    assert rc == code
    assert toasts == [f"triage {status}: 1 group(s) awaiting decision"]
    text = out.read_text(encoding="utf-8")
    assert out.name == "triage_check_2026-10-23.md" and out.parent.name == "2026-W43"
    assert "12856958 / 777" in text and "Card A, Card B" in text and "+3.0 pp" in text
    assert "8.0% list tygodnia (> 5%)" in text and "wzrost o 3.0 pp" in text


def test_triage_check_complete_message_and_flags():
    assert triage_check.toast_text("complete", 0) == "triage complete, nothing to do"
    base = {"share": 0.04}
    assert triage_check.rogue_flags(base, {"share": 0.035}) == []
    assert len(triage_check.rogue_flags({"share": 0.051}, {"share": 0.05})) == 1
    assert len(triage_check.rogue_flags({"share": 0.045}, {"share": 0.02})) == 1
    assert triage_check.rogue_flags({"share": None}, {"share": 0.02}) == []


def test_import_refusals_stop_clean_import_passes():
    clean = ("imported events: 0\nrejected events (failed parsing/validation, nothing written): 0\n"
             "refused writes (differ from stored rows; logged + quarantined, nothing written): 0\n")
    common.check_import_output(clean, common.EXIT_IMPORT)
    bad = clean.replace("nothing written): 0\n", "nothing written): 1\n  decklists 12855459/42: place differs\n", 1)
    with pytest.raises(Stop) as e:
        common.check_import_output(bad, common.EXIT_IMPORT)
    assert e.value.code == common.EXIT_IMPORT and "12855459/42" in e.value.message
