"""Read-only helper run under the DeckDB interpreter (C:/Python/python.exe) by the week runner and the triage check.
It imports DeckDB's own modules; nothing here writes to the DeckDB database. Prints one JSON line (the last line).

    deckdb_probe.py --deckdb-dir DIR [--db PATH] info [--since D --until D]
    deckdb_probe.py --deckdb-dir DIR [--db PATH] triage --out-dir DIR          (scripts/weekly_triage.py, unchanged)
    deckdb_probe.py --deckdb-dir DIR truncate --until D --out PATH            (copy of the DB without events after D)
    deckdb_probe.py --deckdb-dir DIR [--db PATH] detail --triage-json P --since D --until D
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import runpy
import sqlite3
import sys
from pathlib import Path


def _setup(deckdb_dir: str, db: str | None) -> None:
    root = Path(deckdb_dir).resolve()
    os.chdir(root)
    sys.path.insert(0, str(root))
    if db:  # a truncated copy (triage check of a past day): every DeckDB reader goes through config.db_path()
        from deckdb import config

        config.db_path = lambda cfg=None: Path(db)


def _conn():
    from deckdb import config
    from deckdb.safety import connect_readonly

    return connect_readonly(config.db_path())


def cmd_info(a) -> dict:
    from deckdb import config
    from deckdb.review.revisions import data_version
    from deckdb.review.rules import rules_version

    conn = _conn()
    last = conn.execute("SELECT MAX(start_date) FROM events WHERE event_type = 'challenge'").fetchone()[0]
    out = {"db_path": str(config.db_path()), "rules_version": rules_version(), "data_version": data_version(conn),
           "last_challenge": last, "python": sys.version.split()[0]}
    import yaml  # noqa: F401 -- the DeckDB interpreter must have it (rules, triage windows)

    out["yaml"] = yaml.__version__
    if a.since:
        out["events"] = [
            {"event_id": e, "date": d, "tier": t, "site_name": s, "lists": n}
            for e, d, t, s, n in conn.execute(
                "SELECT e.event_id, e.start_date, e.tier, e.site_name, COUNT(d.decklist_id) FROM events e "
                "LEFT JOIN decklists d ON d.event_id = e.event_id WHERE e.event_type = 'challenge' "
                "AND e.start_date BETWEEN ? AND ? GROUP BY e.event_id ORDER BY e.start_date, e.event_id",
                (a.since, a.until))]
    return out


def cmd_triage(a) -> dict:
    script = Path("scripts") / "weekly_triage.py"
    sys.argv = [str(script), "--out-dir", a.out_dir]
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as e:
        if e.code not in (0, None):
            raise
    from deckdb.review.revisions import data_version

    path = Path(a.out_dir) / f"triage_{data_version(_conn())}.json"
    if not path.exists():
        raise SystemExit(f"weekly_triage.py did not write {path}")
    return {"triage_json": str(path)}


def cmd_truncate(a) -> dict:
    """Copy of the DB (sqlite backup API from a read-only connection) without the events after *until* and their
    lists: the state of the database on that day as far as the lists go (decisions and rules stay as today)."""
    src = _conn()
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    dst = sqlite3.connect(str(out))
    src.backup(dst)
    dst.execute("PRAGMA foreign_keys = OFF")
    ids = [r[0] for r in dst.execute("SELECT event_id FROM events WHERE start_date > ?", (a.until,))]
    dst.executemany("DELETE FROM decklists WHERE event_id = ?", [(i,) for i in ids])
    dst.executemany("DELETE FROM events WHERE event_id = ?", [(i,) for i in ids])
    dst.commit()
    dst.close()
    return {"db": str(out), "removed_events": ids}


def _group_ids(conn, triage: dict) -> dict:
    """item_key -> (decklist ids, core card names) of every waiting group, formed as weekly_triage.py forms them."""
    from deckdb.review import data as dm
    from deckdb.review import regions as rg
    from deckdb.review import triage as tg

    out = {g["item_key"]: (g["ids"], g["core"]) for g in triage["new_lists"].get("groups") or [] if g.get("item_key")}
    need = [w for w in triage["windows"] if any(not i["decided"] and i["item_key"] not in out for i in w["items"])]
    if need:
        A = dm.load_all(conn)
        managed, res = rg.rules_assignment(A)
        decided = tg.decided_keys(conn)
        for w in need:
            for g in tg.analyze(conn, A, managed, res, w["since"], w["until"], decided)["groups"]:
                if g["item_key"] and g["item_key"] not in out:
                    out[g["item_key"]] = (list(g["ids"]), [n for n, _s in g["core"][:10]])
    return out


def cmd_detail(a) -> dict:
    from deckdb.export_assignments import rows

    conn = _conn()
    triage = json.loads(Path(a.triage_json).read_text(encoding="utf-8"))
    groups = _group_ids(conn, triage)
    since, until = a.since, a.until
    meta = {did: (ev, dt_, d) for did, ev, dt_, d in conn.execute(
        "SELECT d.decklist_id, d.event_id, d.decktournamentid, e.start_date FROM decklists d JOIN events e "
        "ON e.event_id = d.event_id WHERE e.event_type = 'challenge'")}
    detail = []
    for w in triage["waiting"]:
        ids, core = groups.get(w["item_key"], ([], []))
        in_week = [i for i in ids if i in meta and since <= meta[i][2] <= until]
        sample = (in_week + [i for i in ids if i not in in_week])[:2]
        detail.append({"item_key": w["item_key"], "core": core,
                       "examples": [{"event_id": meta[i][0], "decktournamentid": meta[i][1], "date": meta[i][2]}
                                    for i in sample if i in meta]})
    # Rogue & unclassified (Rogue + unassigned) of the window and of the previous full ISO week: the classification
    # of export-assignments (rows() computes it in memory; no file is written)
    data = rows(conn, None)
    mon = dt.date.fromisoformat(since)
    p_since, p_until = (mon - dt.timedelta(days=7)).isoformat(), (mon - dt.timedelta(days=1)).isoformat()

    def share(lo, hi):
        r = [x for x in data if lo <= x["event_date"] <= hi]
        n = sum(1 for x in r if x["status"] in ("rogue", "unassigned"))
        return {"since": lo, "until": hi, "lists": len(r), "rogue_unclassified": n,
                "rogue": sum(1 for x in r if x["status"] == "rogue"),
                "unassigned": sum(1 for x in r if x["status"] == "unassigned"),
                "events": len({x["event_id"] for x in r}), "share": n / len(r) if r else None}

    return {"groups": detail, "week": share(since, until), "previous_week": share(p_since, p_until)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deckdb-dir", required=True)
    ap.add_argument("--db")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("info")
    p.add_argument("--since")
    p.add_argument("--until")
    p = sub.add_parser("triage")
    p.add_argument("--out-dir", required=True)
    p = sub.add_parser("truncate")
    p.add_argument("--until", required=True)
    p.add_argument("--out", required=True)
    p = sub.add_parser("detail")
    p.add_argument("--triage-json", required=True)
    p.add_argument("--since", required=True)
    p.add_argument("--until", required=True)
    a = ap.parse_args()
    _setup(a.deckdb_dir, a.db)
    res = {"info": cmd_info, "triage": cmd_triage, "truncate": cmd_truncate, "detail": cmd_detail}[a.cmd](a)
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
