"""Sensitivity of the month-on-month change test of the monthly package (order of 2026-11-03 c): the package's event
bootstrap (monthly_metrics of monthly/<YYYY-MM>/data.xlsx) against a pilot bootstrap of the same change.

Pilot bootstrap: in each month its pilots are drawn with replacement (as many as the month has), each with all their
lists of the month; share = lists of the label among the drawn lists. The two months are drawn independently, the
current month first, CHANGE_DRAWS draws, CHANGE_SEED (as the event bootstrap). Two-sided p = 2 x min(P(d <= 0),
P(d >= 0)) (>= 1 / draws), BH q = 0.1 in the same families as the package: the month's tracked decks, the archetypes
(with 'Rogue & unclassified'). Read-only: writes the report only.

    python scripts/monthly_sensitivity.py --month 2026-10 --export <assignments.csv> --package monthly/2026-10 \
        --out docs/reports/monthly_2026-10_sensitivity.md
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import deckdb_input as di  # noqa: E402


def pilot_share_draws(rows: pd.DataFrame, key: str, labels: list, draws: int, rng: np.random.Generator) -> np.ndarray:
    """draws x labels shares (%) of the month's lists, drawing its pilots with replacement (all lists of a pilot)."""
    pilots = sorted(rows["player"].unique())
    m = pd.crosstab(rows["player"], rows[key]).reindex(index=pilots, columns=labels, fill_value=0).to_numpy(float)
    idx = rng.integers(0, len(pilots), size=(draws, len(pilots)))
    tot = m.sum(axis=1)[idx].sum(axis=1)
    return m[idx].sum(axis=1) / tot[:, None] * 100.0


def top_pilot_share(rows: pd.DataFrame, key: str, label: str) -> float:
    g = rows[rows[key] == label]
    return float(g["player"].value_counts().iloc[0] / len(g) * 100.0) if len(g) else float("nan")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", required=True)
    ap.add_argument("--export", required=True)
    ap.add_argument("--package", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    ms = date.fromisoformat(f"{a.month}-01")
    me = di.month_end(ms)
    ps, pe = di.previous_month(ms)
    exp = di.load_export(Path(a.export))
    cur, prev = di.window_rows(exp, ms, me), di.window_rows(exp, ps, pe)
    mm = pd.read_excel(Path(a.package) / "data.xlsx", sheet_name="monthly_metrics")
    pkg = mm[(mm["month"] == a.month) & (mm["tracked"] == 1)]
    if set(pkg["rules_version"]) != {exp.rules_version} or set(pkg["export_sha256"]) != {exp.sha256}:
        raise SystemExit("the package's monthly_metrics comes from another export")
    out_rows = []
    for level, key in ((di.LEVEL_DECK, "material_deck"), (di.LEVEL_ARCHETYPE, "material_archetype")):
        fam = pkg[pkg["level"] == level].reset_index(drop=True)
        labels = sorted(set(cur[key]) | set(prev[key]))
        rng = np.random.default_rng(di.CHANGE_SEED)
        diff = (pilot_share_draws(cur, key, labels, di.CHANGE_DRAWS, rng)
                - pilot_share_draws(prev, key, labels, di.CHANGE_DRAWS, rng))
        j = [labels.index(x) for x in fam["name"]]
        lo, hi = np.percentile(diff[:, j], [2.5, 97.5], axis=0)
        p = di.bootstrap_p(diff[:, j])
        q, rej, _t = di.bh(p)
        for i, r in fam.iterrows():
            share_now = (cur[key] == r["name"]).mean() * 100.0
            share_prev = (prev[key] == r["name"]).mean() * 100.0
            if abs((share_now - share_prev) - r["change_pp"]) > 1e-9:
                raise SystemExit(f"{r['name']}: change differs from the package")
            out_rows.append({
                "level": level, "name": r["name"], "reason": r["tracked_reason"], "change_pp": r["change_pp"],
                "ev_lo": r["change_ci_low_pp"], "ev_hi": r["change_ci_high_pp"], "ev_q": r["change_q_bh"],
                "ev_beyond": int(r["change_beyond"]), "pl_lo": lo[i], "pl_hi": hi[i], "pl_q": q[i],
                "pl_beyond": int(rej[i]), "top_now": top_pilot_share(cur, key, r["name"]),
                "top_prev": top_pilot_share(prev, key, r["name"]),
                "pilots_now": int(cur.loc[cur[key] == r["name"], "player"].nunique()),
                "pilots_prev": int(prev.loc[prev[key] == r["name"], "player"].nunique())})
    t = pd.DataFrame(out_rows)
    write_report(t, a, exp, cur, prev, ms, ps)
    print(t.to_string())
    return 0


def _ci(lo, hi) -> str:
    return f"{lo:+.1f} .. {hi:+.1f}".replace("-", "−")


def write_report(t: pd.DataFrame, a, exp, cur, prev, ms: date, ps: date) -> None:
    def lpm(rows):
        return rows.groupby("player").size()

    lines = [f"# Monthly package {a.month}: sensitivity of the month-on-month change test", "",
             f"Order of 2026-11-03 c. {ms:%B %Y} vs {ps:%B %Y}, the tracked decks and archetypes of the package "
             f"`monthly/{a.month}/` (monthly_metrics). Export `{Path(a.export).name}` (rules {exp.rules_version}, sha256 "
             f"{exp.sha256}). The package, the production code and the charts are not changed; script "
             "`scripts/monthly_sensitivity.py`.", "",
             "## Methods", "",
             "- **Event bootstrap** (the package): the events of each month drawn with replacement, independently, "
             f"{di.CHANGE_DRAWS} draws, seed {di.CHANGE_SEED}; values read from monthly_metrics.",
             "- **Pilot bootstrap** (this report): the pilots of each month drawn with replacement, each with all their "
             "lists of the month (every deck), independently per month, the current month first, the same draws and "
             "seed; share = lists of the deck among the drawn lists.",
             "- Both: 95% percentile interval, two-sided p = 2 x min(P(d <= 0), P(d >= 0)) (at least 1/2000), "
             "Benjamini-Hochberg q = 0.1 separately for the tracked decks and for the archetypes.", "",
             f"Repeat play: {ms:%B} {len(cur)} lists from {cur['player'].nunique()} pilots (mean {lpm(cur).mean():.2f}, "
             f"max {lpm(cur).max()} lists per pilot, {int((lpm(cur) >= 5).sum())} pilots with >= 5 lists); "
             f"{ps:%B} {len(prev)} lists from {prev['player'].nunique()} pilots (mean {lpm(prev).mean():.2f}, max "
             f"{lpm(prev).max()}). {cur['event_id'].nunique()} and {prev['event_id'].nunique()} events.", "",
             "## Table", "",
             "Top pilot share = lists of the deck's (archetype's) most frequent pilot / its lists, in the month.", "",
             "| level | deck / archetype | tracked reason | change pp | event CI | event q | event beyond | pilot CI | "
             "pilot q | pilot beyond | top pilot Oct | top pilot Sep | pilots Oct / Sep |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in t.sort_values(["level", "change_pp"], ascending=[False, False]).itertuples():
        lines.append(f"| {r.level} | {r.name} | {r.reason} | {f"{r.change_pp:+.1f}".replace("-", "−")} | {_ci(r.ev_lo, r.ev_hi)} | "
                     f"{r.ev_q:.3f} | {'yes' if r.ev_beyond else 'no'} | {_ci(r.pl_lo, r.pl_hi)} | {r.pl_q:.3f} | "
                     f"{'yes' if r.pl_beyond else 'no'} | {r.top_now:.0f}% | {r.top_prev:.0f}% | "
                     f"{r.pilots_now} / {r.pilots_prev} |")
    width = (t["pl_hi"] - t["pl_lo"]) / (t["ev_hi"] - t["ev_lo"])
    both = t[(t["ev_beyond"] == 1) & (t["pl_beyond"] == 1)]
    ev_only = t[(t["ev_beyond"] == 1) & (t["pl_beyond"] == 0)]
    pl_only = t[(t["ev_beyond"] == 0) & (t["pl_beyond"] == 1)]

    def names(df):
        return ", ".join(f"{r.name} ({r.change_pp:+.1f} pp)".replace("-", "−") for r in df.itertuples()) or "none"

    def count(df, level):
        return int((df["level"] == level).sum())

    def n(k, word):
        return f"{k} {word}" + ("" if k == 1 else "s")

    lines += ["", "## Summary", "",
              f"Of the {count(t, 'deck')} tracked decks and {count(t, 'archetype')} archetypes, "
              f"{n(count(both, 'deck'), 'deck')} and {n(count(both, 'archetype'), 'archetype')} are beyond normal variation in "
              f"both methods: {names(both)}. Only in the event bootstrap: {n(count(ev_only, 'deck'), 'deck')} and "
              f"{n(count(ev_only, 'archetype'), 'archetype')}: {names(ev_only)}. Only in the pilot bootstrap: "
              f"{names(pl_only)}. The pilot-bootstrap interval is {width.median():.2f} times as wide as the event-"
              f"bootstrap interval in the median (range {width.min():.2f}–{width.max():.2f}).", ""]
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
