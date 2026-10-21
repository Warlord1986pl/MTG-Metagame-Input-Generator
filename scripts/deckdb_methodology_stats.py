"""Numbers for docs/deckdb_metodologia.md (draft spec of the deckdb-mode materials), outside the
materials code: expected conversion (c), pilot measures (d) and pilot-bootstrap conversion
intervals vs. Wilson (e), on the format period starting at the last major format event.

    python scripts/deckdb_methodology_stats.py --since 2026-05-19 [--min-lists 30] [--boot 2000]

Reads the export path from docs/deckdb_config.csv (deckdb_input.load_deckdb_config). Pilots are
identified only by the export's `player` column; no loginid is read. Prints markdown tables.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import deckdb_input as di  # noqa: E402


def md(df: pd.DataFrame, digits: int = 1) -> str:
    """Markdown table without the optional `tabulate` dependency."""
    def cell(v):
        if isinstance(v, (float, np.floating)):
            return "" if np.isnan(v) else f"{v:.{digits}f}"
        return str(v)
    lines = ["| " + " | ".join(df.columns) + " |", "|" + "---|" * len(df.columns)]
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join(lines)


def period_rows(since: date) -> tuple[di.DeckdbExport, pd.DataFrame]:
    export = di.load_export(di.load_deckdb_config())
    rows = di.window_rows(export, since, date.fromisoformat(export.last_event_date))
    rows = rows.assign(
        material_deck=rows["deck"].where(rows["status"] == di.STATUS_ASSIGNED, di.NO_DECK_LABEL),
        top8=(rows["final_rank"] <= 8).astype(int),
    )
    lists_per_event = rows.groupby("event_id")["event_id"].transform("size")
    rows["expected_top8"] = 8.0 / lists_per_event  # 8/L, L = lists of the event
    return export, rows


def expected_conversion(rows: pd.DataFrame, decks: list[str]) -> pd.DataFrame:
    base = rows["top8"].sum() / len(rows) * 100.0
    out = []
    for deck in decks:
        g = rows[rows["material_deck"] == deck]
        exp = g["expected_top8"].sum()
        out.append({
            "Talia": deck,
            "Listy": len(g),
            "Top8": int(g["top8"].sum()),
            "Konwersja_pct": g["top8"].mean() * 100.0,
            "Oczekiwane_Top8": exp,
            "Oczekiwana_konwersja_pct": exp / len(g) * 100.0,
            "Oczekiwana_minus_baza_pp": exp / len(g) * 100.0 - base,
            "Konwersja_minus_oczekiwana_pp": (g["top8"].mean() - exp / len(g)) * 100.0,
        })
    return pd.DataFrame(out), base


def pilot_measures(rows: pd.DataFrame, decks: list[str]) -> pd.DataFrame:
    out = []
    for deck in decks:
        g = rows[rows["material_deck"] == deck]
        t8 = g[g["top8"] == 1]
        per32 = g.groupby("player").agg(lists=("top8", "size"), top8=("top8", "sum"))
        # most frequent pilot in Top 32; ties broken by more Top 8 entries, then by player id
        per32 = per32.reset_index().sort_values(["lists", "top8", "player"], ascending=[False, False, True])
        lead = per32.iloc[0]
        ties32 = int((per32["lists"] == lead["lists"]).sum())
        per8 = t8.groupby("player").size().sort_values(ascending=False)
        rest = g[g["player"] != lead["player"]]
        out.append({
            "Talia": deck,
            "Listy": len(g),
            "Top8": len(t8),
            "Piloci_Top32": int(g["player"].nunique()),
            "Piloci_Top8": int(t8["player"].nunique()),
            "Najcz_pilot_udzial_Top32_pct": lead["lists"] / len(g) * 100.0,
            "Remisy_najcz_Top32": ties32,
            "Ten_pilot_udzial_Top8_pct": lead["top8"] / len(t8) * 100.0 if len(t8) else np.nan,
            "Najcz_pilot_Top8_udzial_pct": per8.iloc[0] / len(t8) * 100.0 if len(t8) else np.nan,
            "Konwersja_pct": g["top8"].mean() * 100.0,
            "Najcz_pilot_listy": int(lead["lists"]),
            "Najcz_pilot_konwersja_pct": lead["top8"] / lead["lists"] * 100.0,
            "Bez_najcz_pilota_konwersja_pct": rest["top8"].mean() * 100.0 if len(rest) else np.nan,
        })
    return pd.DataFrame(out)


def pilot_bootstrap(rows: pd.DataFrame, decks: list[str], boot: int, seed: int) -> tuple[pd.DataFrame, float]:
    """Cluster bootstrap: for a deck with P pilots, draw P pilots with replacement, take all lists of
    every drawn pilot, conversion = Top 8 lists / lists; 2.5 and 97.5 percentiles of *boot* draws."""
    rng = np.random.default_rng(seed)
    out = []
    t0 = time.perf_counter()
    for deck in decks:
        g = rows[rows["material_deck"] == deck]
        per = g.groupby("player").agg(lists=("top8", "size"), top8=("top8", "sum"))
        n_l = per["lists"].to_numpy()
        n_t = per["top8"].to_numpy()
        idx = rng.integers(0, len(per), size=(boot, len(per)))
        conv = n_t[idx].sum(axis=1) / n_l[idx].sum(axis=1) * 100.0
        b_lo, b_hi = np.percentile(conv, [2.5, 97.5])
        w_lo, w_hi = di.wilson_interval(int(n_t.sum()), int(n_l.sum()))
        out.append({
            "Talia": deck,
            "Listy": int(n_l.sum()),
            "Piloci": len(per),
            "Konwersja_pct": n_t.sum() / n_l.sum() * 100.0,
            "Bootstrap_dol": b_lo,
            "Bootstrap_gora": b_hi,
            "Wilson_dol": w_lo * 100.0,
            "Wilson_gora": w_hi * 100.0,
            "Szer_bootstrap_pp": b_hi - b_lo,
            "Szer_Wilson_pp": (w_hi - w_lo) * 100.0,
            "Iloraz_szerokosci": (b_hi - b_lo) / ((w_hi - w_lo) * 100.0),
        })
    return pd.DataFrame(out), time.perf_counter() - t0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True, help="YYYY-MM-DD, początek okresu formatu")
    ap.add_argument("--min-lists", type=int, default=30)
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20261021)
    args = ap.parse_args()

    export, rows = period_rows(date.fromisoformat(args.since))
    counts = rows.groupby("material_deck").size()
    decks = [d for d in counts.sort_values(ascending=False).index
             if counts[d] >= args.min_lists and d != di.NO_DECK_LABEL]
    print(f"eksport {export.path.name} reguły {export.rules_version} sha256 {export.sha256}")
    print(f"okres {args.since}..{export.last_event_date}: {rows['event_id'].nunique()} eventów, {len(rows)} list, "
          f"talie z >= {args.min_lists} listami: {len(decks)}")
    exp, base = expected_conversion(rows, decks)
    print(f"\nbaza wspólna (listy Top 8 / listy okresu): {base:.3f}%")
    print(md(exp, 2))
    print()
    print(md(pilot_measures(rows, decks)))
    boot, secs = pilot_bootstrap(rows, decks, args.boot, args.seed)
    print(f"\nbootstrap: {args.boot} losowań x {len(decks)} talii, {secs:.2f} s")
    print(md(boot, 2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
