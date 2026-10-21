"""Working ("ROBOCZE") materials of input mode "deckdb", built only from deckdb_input tables.

Counterparts of the legacy Challenge/metagame materials; every one states the DeckDB rules version
and the export's sha256 (chart footer, STATUS sheet, CSV columns). No material carries a loginid or
a player name -- only deck/archetype/variant labels and counts. Legacy materials without a
counterpart in the export (Videre-based or delta vs. Videre) are not produced and are listed in
the STATUS workbook's "Pominiete" sheet (OMITTED_MATERIALS).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Callable, List, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

try:
    import deckdb_input as di
except ModuleNotFoundError:
    from . import deckdb_input as di

SHARE_LABEL = "udział w Top 32 Challenge'ów"
WORKING_DIR_NAME = "deckdb_robocze"
TOP_N_CHART = 20

# (legacy material, why it has no deckdb counterpart)
OMITTED_MATERIALS: List[tuple[str, str]] = [
    ("metagame_input.csv / .xlsx / rogue_grouped / grouped.xml", "dane Videre (Meta, winrate, liczba gier)"),
    ("challenge_*_vs_metagame.csv", "porównanie z Meta Videre"),
    ("challenge_delta_ranking_decks.png / _archetypes.png", "delta względem Meta (Videre / udział) — bez delty"),
    ("challenge_vs_meta_*.png: słupek Metagame Share %", "Meta Videre; wykres zastąpiony udziałem w Top 32 i obecnością w eventach"),
    ("encounter_prob_Deck/_Archetype.png", "udział w Top 32 nie jest prawdopodobieństwem spotkania talii (metodologia DeckDB)"),
    ("record_probability.png / record_probabilities.xlsx", "rozkład rekordów z winrate Videre"),
    ("deck_analysis*.xlsx: Importance, Quartile, Prep Priority, Performance Label, Trend Label", "miary złożone z Meta i winrate Videre, bez definicji dla eksportu"),
    ("challenge_statistics.xlsx: Top8/Top16/Winner Event Freq %, AvgPlace, Top16/Top4/Top2", "miary spoza polecenia (nie zdefiniowane dla trybu deckdb)"),
    ("challenge_statistics.xlsx: arkusze C16/C32/C64/C96", "eksport nie ma kolumny poziomu Challenge'a"),
    ("challenge_statistics.xlsx: BestPilots, finalists_*.csv: piloci", "nicki/loginid nie mogą trafić do materiałów"),
    ("challenge_C*_decklist.csv", "zawierały nicki; miejsca talii są w eksporcie"),
    ("broodscale_override_*.csv, rakdos_override_*.csv, alias_report_*.csv", "reguły kartowe i aliasy pipeline'u nie działają w trybie deckdb"),
    ("challenge_manifest / challenge_event_status / premier_events", "rejestr mtgo.com i premiery; eventy okna są w arkuszu Eventy"),
]


def working_dir(outputs_base: Path, start: date, end: date) -> Path:
    return outputs_base / WORKING_DIR_NAME / f"ROBOCZE_{start.isoformat()}_to_{end.isoformat()}"


def _stamp(export: di.DeckdbExport, start: date, end: date, cov: dict) -> str:
    return (
        f"ROBOCZE · DeckDB reguły {export.rules_version} · eksport {export.path.name} sha256 {export.sha256}\n"
        f"okno {start.isoformat()}…{end.isoformat()}: {cov['events']} eventów, {cov['lists']} list Top 32, "
        f"pokrycie uczestników {cov['coverage_pct']:.1f}%"
    )


def _save(fig: plt.Figure, path: Path, stamp: str) -> Path:
    fig.text(0.01, 0.005, stamp, fontsize=6.5, ha="left", va="bottom", color="#444444")
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    fig.savefig(path, dpi=150, facecolor="white")
    plt.close(fig)
    return path


def chart_share(tbl: pd.DataFrame, key: str, n_events: int, path: Path, title: str, stamp: str) -> Path:
    """Counterpart of challenge_vs_meta_*: share of Top 32 Challenge + event presence."""
    df = tbl.sort_values("Listy", ascending=False)
    y = np.arange(len(df))
    fig, ax = plt.subplots(figsize=(12, max(5, 0.32 * len(df) + 1.5)))
    h = 0.4
    ax.barh(y - h / 2, df["Udzial_Top32_pct"], height=h, label=f"{SHARE_LABEL} (%)", color="#2a6fb0")
    presence_pct = df["Eventy_z_lista"] / n_events * 100.0
    ax.barh(y + h / 2, presence_pct, height=h, label=f"obecność w eventach (% z {n_events} eventów)", color="#9cc3e4")
    for i, (s, ev, n) in enumerate(zip(df["Udzial_Top32_pct"], df["Eventy_z_lista"], df["Listy"])):
        ax.text(s + 0.3, i - h / 2, f"{s:.1f}% ({n} list)", va="center", fontsize=7)
        ax.text(ev / n_events * 100.0 + 0.3, i + h / 2, f"{ev}/{n_events}", va="center", fontsize=7, color="#555555")
    ax.set_yticks(y)
    ax.set_yticklabels(df[key])
    ax.invert_yaxis()
    ax.set_xlabel("%")
    ax.set_title(title)
    ax.legend(loc="lower right")
    ax.grid(axis="x", linestyle="--", alpha=0.3)
    return _save(fig, path, stamp)


def chart_conversion(tbl: pd.DataFrame, key: str, n_events: int, path: Path, title: str, stamp: str) -> Path:
    """Counterpart of challenge_conversion_matrix_*: top TOP_N_CHART rows by lists; each column
    coloured on its own scale, cells show the value itself."""
    df = tbl.sort_values("Listy", ascending=False).head(TOP_N_CHART)
    cols = [
        ("Udzial_Top32_pct", f"{SHARE_LABEL}\n%", lambda v: f"{v:.1f}%"),
        ("Eventy_z_lista", f"obecność\n(z {n_events} eventów)", lambda v: f"{int(v)}"),
        ("Top8", "Top 8\n(listy)", lambda v: f"{int(v)}"),
        ("Konwersja_Top32_Top8_pct", "konwersja\nTop 32 → Top 8", lambda v: f"{v:.1f}%"),
        ("Trofea", "trofea", lambda v: f"{int(v)}"),
    ]
    data = df[[c for c, _, _ in cols]].to_numpy(dtype=float)
    col_max = np.where(data.max(axis=0) > 0, data.max(axis=0), 1)
    norm = data / col_max
    fig, ax = plt.subplots(figsize=(12, 0.38 * len(df) + 2))
    ax.imshow(norm, aspect="auto", cmap="YlGnBu", vmin=0, vmax=1)
    for i in range(data.shape[0]):
        for j, (_, _, fmt) in enumerate(cols):
            ax.text(j, i, fmt(data[i, j]), ha="center", va="center", fontsize=8,
                    color="#111111" if norm[i, j] < 0.65 else "#ffffff")
    ax.set_yticks(range(len(df)))
    ax.set_yticklabels(df[key])
    ax.set_xticks(range(len(cols)))
    ax.set_xticklabels([lbl for _, lbl, _ in cols])
    ax.set_title(f"{title}\nkolor: skala osobna dla każdej kolumny")
    return _save(fig, path, stamp)


def chart_winrate(tbl: pd.DataFrame, key: str, path: Path, title: str, stamp: str) -> Path:
    df = tbl.sort_values("Listy", ascending=False).head(TOP_N_CHART)
    df = df[df["Mecze_szw"] > 0]
    y = np.arange(len(df))
    fig, ax = plt.subplots(figsize=(12, 0.38 * len(df) + 2.2))
    wr = df["Winrate_szw_pct"].to_numpy()
    lo = wr - df["Wilson95_dol_pct"].to_numpy()
    hi = df["Wilson95_gora_pct"].to_numpy() - wr
    ax.errorbar(wr, y, xerr=[lo, hi], fmt="o", color="#2a6fb0", ecolor="#7aa6d1", capsize=3)
    for i, (w, m) in enumerate(zip(wr, df["Mecze_szw"])):
        ax.text(df["Wilson95_gora_pct"].iloc[i] + 0.5, i, f"{w:.1f}% ({int(m)} meczów)", va="center", fontsize=7)
    ax.axvline(50, color="#888888", linestyle="--", linewidth=1)
    ax.set_yticks(y)
    ax.set_yticklabels(df[key])
    ax.invert_yaxis()
    ax.set_xlabel("winrate szwajcarski, % (przedział Wilsona 95%)")
    notes = [di.WINRATE_NOTE, di.WILSON_NOTE]
    if (df[key] == di.NO_DECK_LABEL).any():
        notes.append(di.NO_DECK_WINRATE_NOTE)
    ax.set_title("\n".join([title, *notes]), fontsize=10)
    return _save(fig, path, stamp)


def chart_trend(trend: pd.DataFrame, current: pd.DataFrame, key: str, weeks: int, path: Path, title: str, stamp: str) -> Path:
    """Counterpart of meta_trend_*_lastNw: top 10 of the current window, one line each."""
    top = current.sort_values("Listy", ascending=False).head(10)[key].tolist()
    windows = trend[["Okno_do"]].drop_duplicates().sort_values("Okno_do")["Okno_do"].tolist()
    fig, ax = plt.subplots(figsize=(12, 6))
    for label in top:
        s = trend[trend[key] == label].set_index("Okno_do")["Udzial_Top32_pct"].reindex(windows).fillna(0.0)
        ax.plot(windows, s.values, marker="o", label=label)
    ax.set_xlabel("koniec okna 14-dniowego")
    ax.set_ylabel(f"{SHARE_LABEL} (%)")
    ax.set_title(f"{title} — ostatnie {weeks} okien")
    ax.tick_params(axis="x", rotation=35)
    ax.legend(fontsize=8, loc="upper left", bbox_to_anchor=(1.0, 1.0))
    ax.grid(linestyle="--", alpha=0.3)
    return _save(fig, path, stamp)


def _stamp_cols(df: pd.DataFrame, export: di.DeckdbExport) -> pd.DataFrame:
    out = df.copy()
    out["rules_version"] = export.rules_version
    out["export_sha256"] = export.sha256
    return out


def build_working_breakdown(
    export: di.DeckdbExport,
    start: date,
    end: date,
    out_dir: Path,
    cache_dir: Optional[Path],
    trend_weeks: int = 12,
    log: Callable[[str], None] = print,
) -> List[Path]:
    rows = di.window_rows(export, start, end)
    if rows.empty:
        raise di.DeckdbInputError(f"Okno {start}..{end}: brak list w eksporcie")
    out_dir.mkdir(parents=True, exist_ok=True)
    cov = di.coverage(rows)
    stamp = _stamp(export, start, end, cov)
    label = f"{start.isoformat()}_to_{end.isoformat()}"
    n_events = cov["events"]

    decks = di.deck_table(rows)
    archs = di.archetype_table(rows)
    variants = di.variant_table(rows)
    split = di.no_deck_split(rows)
    events = di.event_table(rows)
    finalists = di.finalists_table(rows)
    trend_d = di.trend_table(export, end, trend_weeks, "Talia")
    trend_a = di.trend_table(export, end, trend_weeks, "Archetyp")
    cache = di.match_mtgo_cache(rows, cache_dir, start, end) if cache_dir is not None else None

    status = [
        ("Status", "ROBOCZE — tryb wejścia deckdb, nie publikować bez akceptacji"),
        ("Okno (obustronnie domknięte, event_date)", f"{start.isoformat()} … {end.isoformat()}"),
        ("Eventy", n_events),
        ("Listy Top 32", cov["lists"]),
        ("Listy z talią", int((rows["status"] == di.STATUS_ASSIGNED).sum())),
        ("Listy bez talii (Rogue + nieprzypisane)", int((rows["status"] != di.STATUS_ASSIGNED).sum())),
        ("  w tym Rogue", int((rows["status"] == di.STATUS_ROGUE).sum())),
        ("  w tym nieprzypisane", int((rows["status"] == di.STATUS_UNASSIGNED).sum())),
        ("Suma player_count eventów okna", cov["participants"]),
        ("Pokrycie uczestników (listy / suma player_count), %", round(cov["coverage_pct"], 2)),
        ("rules_version", export.rules_version),
        ("Plik eksportu", str(export.path)),
        ("sha256 eksportu", export.sha256),
        ("Daty eventów w eksporcie", f"{export.first_event_date} … {export.last_event_date}"),
        ("Populacja", "opublikowane listy Top 32 Modern Challenge; nie meta MTGO ani lig"),
        ("Winrate", di.WINRATE_NOTE),
        ("Przedział", di.WILSON_NOTE),
        ("Bez talii", di.NO_DECK_WINRATE_NOTE),
        ("Warianty (D2)", f">= {di.VARIANT_MIN_SHARE:.0%} list talii, >= {di.VARIANT_MIN_LISTS} listy, "
                          f">= {di.VARIANT_MIN_PLAYERS} graczy; talia z >= {di.VARIANT_MIN_DECK_LISTS_PER_MONTH} "
                          "listami w co najmniej jednym miesiącu kalendarzowym okna; metryki talii bez zmian"),
        ("Trend", f"{trend_weeks} okien 14-dniowych co 7 dni, ostatnie kończy się {end.isoformat()}"),
    ]
    if cache is not None:
        status += [
            ("Cache MTGO: listy dopasowane (event_id, decktournamentid)", cache["matched"]),
            ("Cache MTGO: listy eksportu bez pary w cache'u", cache["export_not_in_cache"]),
            ("Cache MTGO: listy cache'u bez pary w eksporcie", cache["cache_not_in_export"]),
            ("Cache MTGO: eventy eksportu bez pliku w cache'u", cache["export_events_not_in_cache"]),
            ("Cache MTGO: eventy Challenge w cache'u bez wierszy w eksporcie", cache["cache_events_not_in_export"]),
            ("Cache MTGO: użycie", "tylko kontrola spójności; żaden materiał nie korzysta z list kart"),
        ]

    written: List[Path] = []
    xlsx = out_dir / f"deckdb_statistics_ROBOCZE_{label}.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as writer:
        pd.DataFrame(status, columns=["Pole", "Wartość"]).to_excel(writer, sheet_name="STATUS", index=False)
        decks.to_excel(writer, sheet_name="Talie", index=False)
        archs.to_excel(writer, sheet_name="Archetypy", index=False)
        (variants if not variants.empty else pd.DataFrame([{"Info": "Żaden wariant nie spełnia progu D2"}])).to_excel(
            writer, sheet_name="Warianty", index=False)
        split.to_excel(writer, sheet_name="Bez_talii_rozbicie", index=False)
        events.to_excel(writer, sheet_name="Eventy", index=False)
        trend_d.to_excel(writer, sheet_name="Trend_talie", index=False)
        trend_a.to_excel(writer, sheet_name="Trend_archetypy", index=False)
        pd.DataFrame(OMITTED_MATERIALS, columns=["Materiał trybu dotychczasowego", "Powód pominięcia"]).to_excel(
            writer, sheet_name="Pominiete", index=False)
    written.append(xlsx)

    fin_csv = out_dir / f"finalisci_ROBOCZE_{label}.csv"
    _stamp_cols(finalists, export).to_csv(fin_csv, index=False, encoding="utf-8-sig")
    written.append(fin_csv)
    hist_csv = out_dir / f"udzial_top32_historia_ROBOCZE_{label}.csv"
    _stamp_cols(trend_d, export).to_csv(hist_csv, index=False, encoding="utf-8-sig")
    written.append(hist_csv)

    written.append(chart_share(decks, "Talia", n_events, out_dir / "udzial_top32_talie.png",
                               f"Talie: {SHARE_LABEL} i obecność w eventach", stamp))
    written.append(chart_share(archs, "Archetyp", n_events, out_dir / "udzial_top32_archetypy.png",
                               f"Archetypy: {SHARE_LABEL} i obecność w eventach", stamp))
    written.append(chart_conversion(decks, "Talia", n_events, out_dir / "konwersja_talie.png",
                                    f"Talie: {SHARE_LABEL}, Top 8, konwersja, trofea (top {TOP_N_CHART})", stamp))
    written.append(chart_conversion(archs, "Archetyp", n_events, out_dir / "konwersja_archetypy.png",
                                    f"Archetypy: {SHARE_LABEL}, Top 8, konwersja, trofea", stamp))
    written.append(chart_winrate(decks, "Talia", out_dir / "winrate_szwajcarski_talie.png",
                                 f"Talie: winrate szwajcarski (top {TOP_N_CHART} wg {SHARE_LABEL})", stamp))
    written.append(chart_winrate(archs, "Archetyp", out_dir / "winrate_szwajcarski_archetypy.png",
                                 "Archetypy: winrate szwajcarski", stamp))
    if trend_weeks > 0:
        written.append(chart_trend(trend_d, decks, "Talia", trend_weeks,
                                   out_dir / f"trend_udzial_talie_last{trend_weeks}w.png", f"Talie: {SHARE_LABEL}", stamp))
        written.append(chart_trend(trend_a, archs, "Archetyp", trend_weeks,
                                   out_dir / f"trend_udzial_archetypy_last{trend_weeks}w.png", f"Archetypy: {SHARE_LABEL}", stamp))

    log(f"[deckdb] okno {start}..{end}: {n_events} eventów, {cov['lists']} list, pokrycie {cov['coverage_pct']:.1f}%, "
        f"reguły {export.rules_version}, sha256 {export.sha256}")
    if cache is not None:
        log(f"[deckdb] cache MTGO: dopasowane {cache['matched']}, eksport bez cache'u {cache['export_not_in_cache']}, "
            f"cache bez eksportu {cache['cache_not_in_export']}")
    for p in written:
        log(f"[deckdb] zapisano {p}")
    return written
