"""Sensitivity of the change test: the event bootstrap against the pilot bootstrap of the same change of share.

Report of 2026-11-03 c (month) and order of 2026-11-03 d (weeks, report only). The pilot bootstrap is the production
one since the decision of 2026-11-03 (deckdb_input.pilot_share_draws): in each period its pilots are drawn with
replacement, each with all their lists of the period; the two periods independently, the current one first,
CHANGE_DRAWS draws, a generator seeded with CHANGE_SEED (as the event bootstrap). Two-sided p, BH q = 0.1 in the same
families as the event test: the tracked decks of the period and the archetypes (with 'Rogue & unclassified').
Read-only: writes the report only.

    month: python scripts/monthly_sensitivity.py --month 2026-10 --export <assignments.csv> --package monthly/2026-10 \
               --out docs/reports/monthly_2026-10_sensitivity.md
           (event columns *_event of the package's monthly_metrics; the pilot result must equal its *_pilot columns)
    weeks: python scripts/monthly_sensitivity.py --weeks 2026-W43 2026-W44 --export <assignments.csv> \
               --week-folder weekly/2026-W44 --out docs/reports/weekly_sensitivity_W43_W44.md
           (event test of weekly_metrics in the week folder's data.xlsx, built from the same export)
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import deckdb_input as di  # noqa: E402

KEYS = ((di.LEVEL_DECK, "material_deck"), (di.LEVEL_ARCHETYPE, "material_archetype"))


def top_pilot_share(rows: pd.DataFrame, key: str, label: str) -> float:
    g = rows[rows[key] == label]
    return float(g["player"].value_counts().iloc[0] / len(g) * 100.0) if len(g) else float("nan")


def compare(cur: pd.DataFrame, prev: pd.DataFrame, fam_rows: pd.DataFrame, ev: dict) -> pd.DataFrame:
    """One row per tested label: the event result (column names in *ev*) and the pilot bootstrap of the same change."""
    out = []
    for level, key in KEYS:
        fam = fam_rows[fam_rows["level"] == level].reset_index(drop=True)
        labels = sorted(set(cur[key]) | set(prev[key]))
        rng = np.random.default_rng(di.CHANGE_SEED)
        diff = (di.pilot_share_draws(cur, key, labels, di.CHANGE_DRAWS, rng)
                - di.pilot_share_draws(prev, key, labels, di.CHANGE_DRAWS, rng))
        j = [labels.index(x) for x in fam["name"]]
        lo, hi = np.percentile(diff[:, j], [2.5, 97.5], axis=0)
        p = di.bootstrap_p(diff[:, j])
        q, rej, _t = di.bh(p)
        for i, r in fam.iterrows():
            change = (cur[key] == r["name"]).mean() * 100.0 - (prev[key] == r["name"]).mean() * 100.0
            if abs(change - r["change_pp"]) > 1e-9:
                raise SystemExit(f"{r['name']}: change differs from the sheet")
            out.append({
                "level": level, "name": r["name"], "reason": r["tracked_reason"], "change_pp": r["change_pp"],
                "ev_lo": r[ev["lo"]], "ev_hi": r[ev["hi"]], "ev_q": r[ev["q"]], "ev_beyond": int(r[ev["beyond"]]),
                "pl_lo": lo[i], "pl_hi": hi[i], "pl_q": q[i], "pl_beyond": int(rej[i]),
                "top_now": top_pilot_share(cur, key, r["name"]), "top_prev": top_pilot_share(prev, key, r["name"]),
                "pilots_now": int(cur.loc[cur[key] == r["name"], "player"].nunique()),
                "pilots_prev": int(prev.loc[prev[key] == r["name"], "player"].nunique())})
    return pd.DataFrame(out)


def _ci(lo, hi) -> str:
    return f"{lo:+.1f} .. {hi:+.1f}".replace("-", "−")


def repeat_line(label: str, rows: pd.DataFrame) -> str:
    per = rows.groupby("player").size()
    return (f"{label}: {len(rows)} lists from {rows['player'].nunique()} pilots in {rows['event_id'].nunique()} events "
            f"(mean {per.mean():.2f}, max {per.max()} lists per pilot; {int((per >= 2).sum())} pilots with >= 2 lists, "
            f"{int((per >= 5).sum())} with >= 5)")


def table(t: pd.DataFrame, now: str, before: str) -> list:
    lines = [f"| level | deck / archetype | tracked reason | change pp | event CI | event q | event beyond | pilot CI | "
             f"pilot q | pilot beyond | top pilot {now} | top pilot {before} | pilots {now} / {before} |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in t.sort_values(["level", "change_pp"], ascending=[False, False]).itertuples():
        lines.append(f"| {r.level} | {r.name} | {r.reason} | {f'{r.change_pp:+.1f}'.replace('-', '−')} | "
                     f"{_ci(r.ev_lo, r.ev_hi)} | {r.ev_q:.3f} | {'yes' if r.ev_beyond else 'no'} | "
                     f"{_ci(r.pl_lo, r.pl_hi)} | {r.pl_q:.3f} | {'yes' if r.pl_beyond else 'no'} | {r.top_now:.0f}% | "
                     f"{r.top_prev:.0f}% | {r.pilots_now} / {r.pilots_prev} |")
    return lines


def summary(t: pd.DataFrame) -> str:
    both = t[(t["ev_beyond"] == 1) & (t["pl_beyond"] == 1)]
    ev_only = t[(t["ev_beyond"] == 1) & (t["pl_beyond"] == 0)]
    pl_only = t[(t["ev_beyond"] == 0) & (t["pl_beyond"] == 1)]
    width = (t["pl_hi"] - t["pl_lo"]) / (t["ev_hi"] - t["ev_lo"])

    def names(df):
        return ", ".join(f"{r.name} ({r.change_pp:+.1f} pp)".replace("-", "−") for r in df.itertuples()) or "none"

    def n(df, level, word):
        k = int((df["level"] == level).sum())
        return f"{k} {word}" + ("" if k == 1 else "s")

    return (f"Of the {n(t, 'deck', 'tracked deck')} and {n(t, 'archetype', 'archetype')}, "
            f"{n(both, 'deck', 'deck')} and {n(both, 'archetype', 'archetype')} are beyond normal variation in both "
            f"methods: {names(both)}. Only in the event bootstrap: {n(ev_only, 'deck', 'deck')} and "
            f"{n(ev_only, 'archetype', 'archetype')}: {names(ev_only)}. Only in the pilot bootstrap: {names(pl_only)}. "
            f"The pilot-bootstrap interval is {width.median():.2f} times as wide as the event-bootstrap interval in the "
            f"median (range {width.min():.2f}–{width.max():.2f}).")


METHODS = [
    "- **Pilot bootstrap**: the pilots of each period drawn with replacement, each with all their lists of the period "
    "(every deck), the two periods independently, the current one first, 2000 draws, seed "
    f"{di.CHANGE_SEED}; share = lists of the deck among the drawn lists (`deckdb_input.pilot_share_draws`).",
    "- Both: 95% percentile interval, two-sided p = 2 x min(P(d <= 0), P(d >= 0)) (at least 1/2000), "
    "Benjamini-Hochberg q = 0.1 separately for the tracked decks and for the archetypes.",
]


def run_month(a, exp) -> None:
    ms = date.fromisoformat(f"{a.month}-01")
    ps, pe = di.previous_month(ms)
    cur, prev = di.window_rows(exp, ms, di.month_end(ms)), di.window_rows(exp, ps, pe)
    mm = pd.read_excel(Path(a.package) / "data.xlsx", sheet_name="monthly_metrics")
    fam = mm[(mm["month"] == a.month) & (mm["tracked"] == 1)]
    if set(fam["rules_version"]) != {exp.rules_version} or set(fam["export_sha256"]) != {exp.sha256}:
        raise SystemExit("the package's monthly_metrics comes from another export")
    t = compare(cur, prev, fam, {"lo": "change_ci_low_event_pp", "hi": "change_ci_high_event_pp",
                                 "q": "change_q_bh_event", "beyond": "change_beyond_event"})
    pkg = fam.set_index(["level", "name"])
    for r in t.itertuples():  # the package's own pilot columns must be this computation
        row = pkg.loc[(r.level, r.name)]
        if int(row["change_beyond_pilot"]) != r.pl_beyond or abs(row["change_q_bh_pilot"] - r.pl_q) > 1e-12:
            raise SystemExit(f"{r.name}: pilot bootstrap differs from the package's *_pilot columns")
    lines = [f"# Monthly package {a.month}: sensitivity of the month-on-month change test", "",
             f"{ms:%B %Y} vs {ps:%B %Y}, the tracked decks and archetypes of `monthly/{a.month}/` (monthly_metrics). "
             f"Export `{Path(a.export).name}` (rules {exp.rules_version}, sha256 {exp.sha256}).", "",
             "## Methods", "",
             "- **Event bootstrap** (columns *_event of the package): the events of each month drawn with replacement, "
             f"independently, 2000 draws, seed {di.CHANGE_SEED}.", *METHODS, "",
             "Repeat play: " + repeat_line(f"{ms:%B}", cur) + "; " + repeat_line(f"{ps:%B}", prev) + ".", "",
             "## Table", "", *table(t, f"{ms:%b}", f"{ps:%b}"), "", "## Summary", "", summary(t), ""]
    Path(a.out).write_text("\n".join(lines), encoding="utf-8")
    print(t.to_string())


def run_weeks(a, exp) -> None:
    wm = pd.read_excel(Path(a.week_folder) / "data.xlsx", sheet_name="weekly_metrics")
    if set(wm["rules_version"]) != {exp.rules_version} or set(wm["export_sha256"]) != {exp.sha256}:
        raise SystemExit("the week folder's weekly_metrics comes from another export")
    lines = ["# Weekly change test: sensitivity, event bootstrap vs pilot bootstrap (report only)", "",
             f"Order of 2026-11-03 d, point 5: {', '.join(f'{w} vs the week before' for w in a.weeks)}, the tracked "
             f"decks and archetypes of each week (weekly_metrics of `{a.week_folder}/data.xlsx`). Export "
             f"`{Path(a.export).name}` (rules {exp.rules_version}, sha256 {exp.sha256}); every week is computed from this "
             "one export, so the W43 numbers can differ from the folder published for W43 (an older export and older "
             "rules). Nothing in production changes: the weekly materials keep the event bootstrap.", "",
             "## Methods", "",
             "- **Event bootstrap** (the weekly test, weekly_metrics): the events of each week drawn with replacement, "
             f"independently, 2000 draws, seed {di.CHANGE_SEED}.", *METHODS, ""]
    summaries = []
    for week in a.weeks:
        y, w = week.split("-W")
        ws = date.fromisocalendar(int(y), int(w), 1)
        ps = ws - timedelta(days=7)
        cur, prev = di.window_rows(exp, ws, ws + timedelta(days=6)), di.window_rows(exp, ps, ps + timedelta(days=6))
        fam = wm[(wm["iso_week"] == week) & (wm["tracked"] == 1)]
        if fam.empty:
            raise SystemExit(f"{week}: no tracked rows in weekly_metrics")
        t = compare(cur, prev, fam, {"lo": "change_ci_low_pp", "hi": "change_ci_high_pp", "q": "change_q_bh",
                                     "beyond": "change_beyond"})
        prev_label = di.iso_week_label(ps)
        lines += [f"## {week} vs {prev_label}", "",
                  "Repeat play: " + repeat_line(week, cur) + "; " + repeat_line(prev_label, prev) + ".", "",
                  *table(t, week.split("-")[1], prev_label.split("-")[1]), "", summary(t), ""]
        summaries.append(f"**{week}:** " + summary(t))
        print(week)
        print(t.to_string())
    lines += ["## Summary", "", *[s + "\n" for s in summaries]]
    Path(a.out).write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--month")
    g.add_argument("--weeks", nargs="+")
    ap.add_argument("--export", required=True)
    ap.add_argument("--package")
    ap.add_argument("--week-folder")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    exp = di.load_export(Path(a.export))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    if a.month:
        run_month(a, exp)
    else:
        run_weeks(a, exp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
