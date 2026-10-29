"""triage_check.py: the Friday alarm (docs/runner.md). Imports the Modern Challenges of the running ISO week up to
today (as the runner), runs DeckDB scripts/weekly_triage.py, writes weekly/<week>/triage_check_<date>.md and shows a
Windows toast. Exit code 0 complete, 1 pending_notice, 2 pending_blocking, 3 error. Exports nothing, changes no rule
and no decision. With --as-of before the last Challenge of the DB the triage runs on a copy of the DB without the
later events (control runs on a past day; decisions and triage windows stay as they are today)."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

from .common import REPO, Stop, backfill_and_import, check_network, iso_label, load_runner_config, probe, toast

EXIT = {"complete": 0, "pending_notice": 1, "pending_blocking": 2}
EXIT_ERROR = 3
# Alarm thresholds of the Rogue & unclassified share (order of 2026-10-29; not written in DeckDB's master prompt)
ROGUE_SHARE_FLAG = 0.05
ROGUE_RISE_FLAG_PP = 2.0
TITLE = "MTG triage check"


def toast_text(status: str, groups: int) -> str:
    if status == "complete":
        return "triage complete, nothing to do"
    return f"triage {status}: {groups} group(s) awaiting decision"


def rogue_flags(week: dict, prev: dict) -> List[str]:
    flags = []
    if week["share"] is not None and week["share"] > ROGUE_SHARE_FLAG:
        flags.append(f"Rogue & unclassified {week['share']:.1%} list tygodnia (> {ROGUE_SHARE_FLAG:.0%})")
    if week["share"] is not None and prev["share"] is not None:
        rise = 100 * (week["share"] - prev["share"])
        if rise > ROGUE_RISE_FLAG_PP:
            flags.append(f"Rogue & unclassified wzrost o {rise:.1f} pp wobec poprzedniego tygodnia "
                         f"(> {ROGUE_RISE_FLAG_PP:.0f} pp)")
    return flags


def render(as_of: date, mon: date, state: dict, detail: dict, truncated: Optional[dict], backfill: dict) -> str:
    wk, prev = detail["week"], detail["previous_week"]
    groups = {g["item_key"]: g for g in detail["groups"]}
    out = [f"# Triage check {iso_label(mon)} (stan na {as_of})", "",
           f"- Status: **{state['status']}**",
           f"- Grupy do rozstrzygnięcia: {state['pending_groups']} "
           f"(razem {state['waiting_share_of_week']:.1%} list tygodnia triage'u {state['week']['iso_week']})",
           f"- Okno importu: {mon} .. {as_of}; " + "; ".join(
               f"{m}: na stronie {r.get('listed', 0)}, pobrane {r.get('fetched', 0)}, już zapisane "
               f"{r.get('already_stored', 0)}" for m, r in backfill.items()),
           f"- Listy tygodnia do {as_of}: {wk['lists']} z {wk['events']} eventów",
           f"- Reguły DeckDB `{state['rules_version']}`, data_version `{state['data_version']}`",
           "- Nowe listy triage'u (po ostatnim oknie w rules/triage.yaml): " + (
               f"{state['new_lists']['lists']} ({state['new_lists']['since']} .. {state['new_lists']['until']})"
               if state["new_lists"]["since"] <= state["new_lists"]["until"] else
               f"brak (ostatnie okno triage'u kończy się po {state['new_lists']['until']})")]
    if state["week"]["since"] != mon.isoformat():
        out.append(f"- Uwaga: triage liczy udział w tygodniu {state['week']['iso_week']} (tydzień ostatniego "
                   "Challenge'a w bazie), bo w bieżącym tygodniu nie ma jeszcze list")
    if truncated:
        out.append(f"- Przebieg na kopii bazy bez eventów po {as_of} (usunięte w kopii: "
                   f"{len(truncated['removed_events'])}); decyzje i okna triage'u w stanie bieżącym")
    out += ["", "## Grupy do rozstrzygnięcia", ""]
    if not state["waiting"]:
        out.append("Brak.")
    else:
        out += ["| grupa | list | w tygodniu | udział w tygodniu | czeka | przykłady (event_id / decktournamentid) | "
                "karty wspólne |", "|---|---|---|---|---|---|---|"]
        for g in state["waiting"]:
            d = groups.get(g["item_key"], {})
            ex = ", ".join(f"{e['event_id']} / {e['decktournamentid']}" for e in d.get("examples", [])) or "-"
            out.append(f"| {g['item_key']}{' (' + g['working_name'] + ')' if g.get('working_name') else ''} | "
                       f"{g['lists']} | {g['lists_in_week']} | {g['share_of_week']:.1%} | {g['days_waiting']} dni, "
                       f"{g['state']} | {ex} | {', '.join(d.get('core', [])) or '-'} |")
    out += ["", "## Rogue & unclassified", ""]
    for label, s in (("Tydzień do dziś", wk), ("Poprzedni pełny tydzień", prev)):
        out.append(f"- {label} ({s['since']} .. {s['until']}): "
                   + (f"{s['rogue_unclassified']} z {s['lists']} list ({s['share']:.1%}; Rogue {s['rogue']}, "
                      f"nieprzypisane {s['unassigned']})" if s["lists"] else "brak list"))
    if wk["share"] is not None and prev["share"] is not None:
        out.append(f"- Zmiana: {100 * (wk['share'] - prev['share']):+.1f} pp")
    flags = rogue_flags(wk, prev)
    out.append(f"- Flaga: {'; '.join(flags)}" if flags else
               f"- Flaga: brak (progi: > {ROGUE_SHARE_FLAG:.0%} list albo wzrost > {ROGUE_RISE_FLAG_PP:.0f} pp)")
    out += ["", f"Powiadomienie: \"{toast_text(state['status'], state['pending_groups'])}\"",
            f"Wygenerowano {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}."]
    return "\n".join(out) + "\n"


def check(as_of: date, weekly_root: Path, show_toast: bool = True) -> tuple[int, Path]:
    mon = as_of - timedelta(days=as_of.weekday())
    work = REPO / "outputs" / "_working" / "triage_check" / as_of.isoformat()
    work.mkdir(parents=True, exist_ok=True)
    log = work / "logs" / "deckdb.log"
    cfg = load_runner_config()
    check_network(code=EXIT_ERROR)
    backfill = backfill_and_import(cfg, mon, as_of, work, EXIT_ERROR)
    info = probe(cfg, ["info"], log, EXIT_ERROR, "DeckDB (info)")
    db_args, truncated = [], None
    if info["last_challenge"] and info["last_challenge"] > as_of.isoformat():
        truncated = probe(cfg, ["truncate", "--until", as_of.isoformat(), "--out", str(work / "deckdb_as_of.sqlite")],
                          log, EXIT_ERROR, "kopia bazy na dzień --as-of")
        db_args = ["--db", truncated["db"]]
    res = probe(cfg, [*db_args, "triage", "--out-dir", str(work)], log, EXIT_ERROR, "DeckDB weekly_triage.py")
    state = json.loads(Path(res["triage_json"]).read_text(encoding="utf-8"))
    detail = probe(cfg, [*db_args, "detail", "--triage-json", res["triage_json"], "--since", mon.isoformat(),
                         "--until", as_of.isoformat()], log, EXIT_ERROR, "szczegóły grup i Rogue & unclassified")
    if state["status"] not in EXIT:
        raise Stop(EXIT_ERROR, f"nieznany status triage'u {state['status']!r}")
    out = weekly_root / iso_label(mon) / f"triage_check_{as_of.isoformat()}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(as_of, mon, state, detail, truncated, backfill), encoding="utf-8")
    if truncated:
        Path(truncated["db"]).unlink(missing_ok=True)
    text = toast_text(state["status"], state["pending_groups"])
    print(text)
    if show_toast:
        toast(TITLE, text)
    return EXIT[state["status"]], out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="triage_check.py", description="Piątkowy alarm triage'u (docs/runner.md)")
    ap.add_argument("--as-of", default=None, help="RRRR-MM-DD, domyślnie dziś (inny dzień: przebieg kontrolny)")
    ap.add_argument("--no-toast", action="store_true", help="bez powiadomienia Windows")
    ap.add_argument("--weekly-root", default=str(REPO / "weekly"), help=argparse.SUPPRESS)  # tests
    a = ap.parse_args(argv)
    try:
        as_of = date.fromisoformat(a.as_of) if a.as_of else date.today()
        if as_of > date.today():
            raise Stop(EXIT_ERROR, f"--as-of {as_of} jest w przyszłości")
        code, out = check(as_of, Path(a.weekly_root), not a.no_toast)
    except Stop as e:
        print(f"STOP [{EXIT_ERROR}]: {e.message}", file=sys.stderr)
        if not a.no_toast:
            try:
                toast(TITLE, f"triage check error (code {EXIT_ERROR}): {e.message.splitlines()[0][:150]}")
            except Stop as t:
                print(t.message, file=sys.stderr)
        return EXIT_ERROR
    except Exception:  # noqa: BLE001 -- any other error is code 3, never 1 (= pending_notice)
        import traceback

        print(f"STOP [{EXIT_ERROR}]: nieoczekiwany błąd\n{traceback.format_exc()}", file=sys.stderr)
        if not a.no_toast:
            try:
                toast(TITLE, f"triage check error (code {EXIT_ERROR}): unexpected error, see the console")
            except Stop as t:
                print(t.message, file=sys.stderr)
        return EXIT_ERROR
    print(f"Raport: {out}")
    return code
