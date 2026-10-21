"""Working ("ROBOCZE") materials of input mode "deckdb" (docs/deckdb_metodologia.md, accepted 2026-10-21).

Three time levels, each its own directory under ``<outputs-base>/deckdb_robocze/ROBOCZE_<as-of>/``:

  tydzien_<YYYY-Www>                  the last complete ISO week: share, presence, Top 8; trend of
                                      the last N disjoint ISO weeks
  miesiac_<YYYY-MM>[_niepelny]        the as-of month: the same + trophies, variants by D2
  okres_formatu_<start>_to_<as-of>    conversion with the common baseline and the pilot-bootstrap
                                      interval, trophies, pilot measures (decks only)

Every material states the DeckDB rules version and the export's sha256 (chart footer, STATUS sheet,
CSV columns) and carries the glossary (``Slownik`` sheet, ``SLOWNIK.md`` next to the files, the
terms a chart uses in its footer). No material carries a loginid or a player id -- only labels and
counts. Winrate is not a measure (K36). Legacy materials without a counterpart are listed in the
``Pominiete`` sheet (OMITTED_MATERIALS).
"""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

try:
    import deckdb_cards as dc
    import deckdb_input as di
except ModuleNotFoundError:
    from . import deckdb_cards as dc
    from . import deckdb_input as di

SHARE_LABEL = "udział w Top 32 Challenge'ów"
WORKING_DIR_NAME = "deckdb_robocze"
TOP_N_CHART = 20

GLOSSARY: List[tuple[str, str]] = [
    ("event", "jeden Modern Challenge z eksportu DeckDB (event_id); eventy premier nie występują. Data = event_date "
              "(UTC); okresy są obustronnie domknięte."),
    ("Top 32", "opublikowane listy eventu: final_rank 1…L, L = min(32, player_count). Populacja materiałów to listy "
               "Top 32, nie wszyscy uczestnicy i nie meta MTGO."),
    ("udział", f"{SHARE_LABEL}: listy talii / wszystkie listy okresu. Rogue i nieprzypisane razem jako „bez talii”. "
               "Nie jest prawdopodobieństwem spotkania talii."),
    ("obecność", "liczba różnych eventów okresu z co najmniej jedną listą talii; także jako % eventów okresu."),
    ("Top 8", "lista z final_rank ≤ 8; Top 8 % eventów = eventy z co najmniej jedną listą talii w Top 8 / eventy "
              "okresu."),
    ("konwersja", "Top 32 → Top 8: listy talii w Top 8 / listy talii. Baza wspólna okresu = wszystkie listy Top 8 / "
                  "wszystkie listy okresu (z danych, nie 25%)."),
    ("przedział", f"przedział 95% konwersji: bootstrap po pilotach ({di.BOOT_DRAWS} losowań pilotów talii ze "
                  f"zwracaniem, wszystkie listy wylosowanego pilota), tylko przy co najmniej {di.BOOT_MIN_PILOTS} "
                  "pilotach; poniżej brak przedziału i etykieta „mało pilotów”."),
    ("etykiety pilotów", f"„{di.LABEL_FEW_PILOTS}”: mniej niż {di.BOOT_MIN_PILOTS} pilotów w okresie, bez przedziału; "
                         f"„{di.LABEL_CONCENTRATION}”: najczęstszy pilot ma co najmniej 1/3 list talii albo co "
                         "najmniej 1/2 jej wejść do Top 8 (tylko talie)."),
    ("oczekiwana konwersja", "Σ 8/L po listach talii / listy talii; tylko w skrypcie kontrolnym "
                             "(scripts/deckdb_methodology_stats.py), nie w materiałach."),
    ("trofeum", "lista z final_rank = 1; podawane jako liczba (miesiąc, okres formatu)."),
    ("pilot", "anonimowy identyfikator gracza z kolumny player eksportu; materiały podają tylko liczby i udziały, "
              "nigdy identyfikatora."),
    ("najczęstszy pilot", "pilot z największą liczbą list Top 32 talii (remis: więcej list Top 8, potem "
                          "identyfikator); jego udział w listach Top 32 i w wejściach do Top 8, jego konwersja i "
                          "konwersja talii bez niego. Tylko dla talii, nie dla archetypów."),
    ("pokrycie uczestników", "listy okresu / suma player_count eventów okresu."),
    ("okres formatu", "od dnia obowiązywania na MTGO ostatniego potwierdzonego dużego zdarzenia formatu "
                      "(rules/format_events.yaml DeckDB), narastająco do daty stanu."),
    ("tydzień", "tydzień ISO, poniedziałek–niedziela, tygodnie rozłączne; tylko pełne tygodnie."),
    ("miesiąc", "miesiąc kalendarzowy; miesiąc niepełny (eksport kończy się w jego trakcie) jest oznaczony "
                "i służy tylko do pracy roboczej."),
    ("warianty", "według D2 (DeckDB decisions.md, wpis 35): w miesiącu ≥ 5% list talii, ≥ 3 listy, ≥ 2 graczy; "
                 "tylko miesiące z ≥ 20 listami talii; wariant nie zmienia metryk talii."),
]

VARIANT_SPLIT_NOTE = ("Podział na zmiany list i zmiany składu talii zależy od bieżących definicji wariantów w regułach "
                      "DeckDB; w taliach bez wariantów cała zmiana jest liczona jako zmiana list.")

CARD_GLOSSARY: List[tuple[str, str]] = [
    ("karta", "oracle_id z eksportu kart DeckDB (deckdb export-cards); kopie zsumowane po drukach; lądy z nadtypem "
              "Basic (is_basic = 1) wyłączone ze wszystkich miar."),
    ("obecność karty", "listy talii z co najmniej jedną kopią karty w strefie / listy talii w miesiącu; maindeck i "
                       "sideboard osobno."),
    ("średnia liczba kopii", "kopie karty w strefie / listy talii z kartą (tylko listy z kartą)."),
    ("zmiana obecności", f"obecność w miesiącu 2 − obecność w miesiącu 1 (pp); testowana dla talii z ≥ {dc.MIN_LISTS} "
                         f"listami i ≥ {dc.MIN_PILOTS} pilotami w obu miesiącach; przedział: bootstrap po pilotach "
                         "losowanych niezależnie w każdym miesiącu (bez par)."),
    ("raportowanie zmiany", f"|zmiana| ≥ {dc.MIN_CHANGE_PP:.0f} pp i odrzucenie w korekcie Benjamini–Hochberg "
                            f"(q = {dc.BH_Q}) w rodzinie testów zmiany ogółem: wszystkie testowane pary (talia, "
                            "strefa, karta) porównania we wszystkich taliach; p dwustronne z przybliżenia normalnego "
                            "(wartość / błąd standardowy z bootstrapu). FDR jest kontrolowany osobno w każdej "
                            "kategorii (zmiana ogółem; składowa wewnątrz wariantów)."),
    ("rozkład zmiany", "zmiana wewnątrz wariantów + zmiana proporcji wariantów (symetrycznie, sumuje się do zmiany); "
                       "wszystkie etykiety wariantów eksportu, pusta = „(bez wariantu)”."),
    (dc.KIND_LIST, f"składowa wewnątrz wariantów |≥ {dc.MIN_CHANGE_PP:.0f} pp| istotna w osobnej rodzinie BH "
                   f"(q = {dc.BH_Q}) testów składowej wewnątrz wariantów (te same testowane pary; bootstrap składowej "
                   "na tych samych losowaniach pilotów). FDR jest kontrolowany osobno w każdej kategorii."),
    (dc.KIND_COMPOSITION, "zgłoszona zmiana obecności (raportowanie zmiany), która nie jest zmianą list: wynika "
                          "z proporcji wariantów talii, nie ze zmiany list wewnątrz wariantów."),
    ("karta poza testem", "obecność 0% w jednym z miesięcy; nie jest testowana zmianą obecności."),
    ("zniknięcie karty", f"0% w miesiącu 2; zgłaszane, gdy (1 − odsetek pilotów talii z miesiąca 1 grających kartę)"
                         f"^(liczba pilotów talii w miesiącu 2) < {dc.GONE_P} i kartę grało w miesiącu 1 co najmniej {dc.MIN_PILOTS_GONE} pilotów; "
                         "pozostałe to obserwacje."),
    ("nowa karta", f"0% w miesiącu 1; zgłaszana przy co najmniej {dc.MIN_PILOTS_NEW} pilotach w miesiącu 2; "
                   "pozostałe to obserwacje."),
    ("pierwsze pojawienie", f"najwcześniejsza data listy talii z kartą w strefie w całym eksporcie; listy i piloci z "
                            f"kartą w {dc.FIRST_SEEN_DAYS} dniach od niej; „przed początkiem danych” w pierwszych "
                            f"{dc.FIRST_SEEN_DAYS} dniach eksportu; zgłaszane przy co najmniej {dc.MIN_PILOTS_NEW} "
                            "pilotach w tych dniach, pozostałe to obserwacje."),
    ("zmiany list pilota", "kolejne opublikowane listy tego samego pilota w tej samej talii (okres formatu): suma "
                           "|różnic kopii| w maindecku i sideboardzie, karty dodane/usunięte, przeniesienia main ↔ "
                           "sideboard; następna lista innej talii = zmiana talii. Tylko rozkłady dla talii."),
    ("warianty a podział zmian", VARIANT_SPLIT_NOTE),
]

# (legacy material, why it has no deckdb counterpart)
OMITTED_MATERIALS: List[tuple[str, str]] = [
    ("metagame_input.csv / .xlsx / rogue_grouped / grouped.xml", "dane Videre (Meta, winrate, liczba gier)"),
    ("challenge_*_vs_metagame.csv", "porównanie z Meta Videre"),
    ("challenge_delta_ranking_decks.png / _archetypes.png", "delta względem Meta (Videre / udział) — bez delty"),
    ("challenge_vs_meta_*.png: słupek Metagame Share %", "Meta Videre; zastąpiony udziałem w Top 32 i obecnością"),
    ("encounter_prob_Deck/_Archetype.png", "udział w Top 32 nie jest prawdopodobieństwem spotkania talii"),
    ("record_probability.png / record_probabilities.xlsx", "rozkład rekordów z winrate"),
    ("winrate szwajcarski (wykresy winrate_szwajcarski_*, kolumny Wygrane/Przegrane/Mecze/Winrate/Wilson)",
     "decyzja K36: winrate nie jest miarą wyników"),
    ("rekord szwajcarski i bye (Listy_z_bye)", "używane tylko przez winrate (K36)"),
    ("deck_analysis*.xlsx: Importance, Quartile, Prep Priority, Performance Label, Trend Label",
     "miary złożone z Meta i winrate Videre, bez definicji dla eksportu"),
    ("challenge_statistics.xlsx: Top16 Event Freq %, Top16/Top4/Top2 Count, AvgPlace, BestPlace",
     "miary spoza specyfikacji (nie zdefiniowane dla trybu deckdb)"),
    ("trofea / listy, zwycięstwa % eventów", "decyzja 2026-10-21 d: trofea tylko jako liczby"),
    ("oczekiwana konwersja", "decyzja 2026-10-21 d: tylko w skrypcie kontrolnym, z ostrzeżeniem powyżej 1 pp"),
    ("challenge_statistics.xlsx: arkusze C16/C32/C64/C96", "eksport nie ma kolumny poziomu Challenge'a"),
    ("challenge_statistics.xlsx: BestPilots, finalists_*.csv: piloci", "nicki/loginid nie mogą trafić do materiałów"),
    ("miary pilotów dla archetypów", "decyzja 2026-10-21 d"),
    ("challenge_C*_decklist.csv", "zawierały nicki; miejsca talii są w eksporcie"),
    ("broodscale_override_*.csv, rakdos_override_*.csv, alias_report_*.csv",
     "reguły kartowe i aliasy pipeline'u nie działają w trybie deckdb"),
    ("challenge_manifest / challenge_event_status / premier_events", "rejestr mtgo.com i premiery; eventy są w arkuszu Eventy"),
]


def root_dir(outputs_base: Path, as_of: date) -> Path:
    return outputs_base / WORKING_DIR_NAME / f"ROBOCZE_{as_of.isoformat()}"


def _glossary_terms(*terms: str) -> str:
    g = dict(GLOSSARY)
    return "\n".join(f"{t}: {g[t]}" for t in terms)


def _stamp(export: di.DeckdbExport, level: str, start: date, end: date, cov: dict, note: str = "") -> str:
    return (
        f"ROBOCZE · {level} {start.isoformat()}…{end.isoformat()}{note} · {cov['events']} eventów, {cov['lists']} list "
        f"Top 32, pokrycie uczestników {cov['coverage_pct']:.1f}%\n"
        f"DeckDB reguły {export.rules_version} · eksport {export.path.name} sha256 {export.sha256}"
    )


def _save(fig: plt.Figure, path: Path, footer: str) -> Path:
    lines = footer.count("\n") + 1
    fig.text(0.01, 0.005, footer, fontsize=6.3, ha="left", va="bottom", color="#444444", wrap=True)
    fig.tight_layout(rect=(0, min(0.45, 0.018 * lines + 0.01), 1, 1))
    fig.savefig(path, dpi=150, facecolor="white")
    plt.close(fig)
    return path


def chart_share(tbl: pd.DataFrame, key: str, n_events: int, path: Path, title: str, footer: str) -> Path:
    """Counterpart of challenge_vs_meta_*: share of Top 32 Challenge + event presence + Top 8 % of events."""
    df = tbl.sort_values("Listy", ascending=False)
    y = np.arange(len(df))
    fig, ax = plt.subplots(figsize=(12, max(5, 0.42 * len(df) + 3.5)))
    h = 0.28
    ax.barh(y - h, df["Udzial_Top32_pct"], height=h, label=f"{SHARE_LABEL} (%)", color="#2a6fb0")
    ax.barh(y, df["Obecnosc_pct_eventow"], height=h, label=f"obecność (% z {n_events} eventów)", color="#9cc3e4")
    ax.barh(y + h, df["Top8_pct_eventow"], height=h, label=f"Top 8 (% z {n_events} eventów)", color="#e3a346")
    for i, r in enumerate(df.itertuples()):
        ax.text(r.Udzial_Top32_pct + 0.3, i - h, f"{r.Udzial_Top32_pct:.1f}% ({r.Listy} list)", va="center", fontsize=6.5)
        ax.text(r.Obecnosc_pct_eventow + 0.3, i, f"{r.Eventy_z_lista}/{n_events}", va="center", fontsize=6.5,
                color="#555555")
        ax.text(r.Top8_pct_eventow + 0.3, i + h, f"{r.Eventy_z_Top8}/{n_events} ({r.Top8} list Top 8)", va="center",
                fontsize=6.5, color="#7a4f10")
    ax.set_yticks(y)
    ax.set_yticklabels(df[key])
    ax.invert_yaxis()
    ax.set_xlabel("%")
    ax.set_title(title)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.06 - 1.2 / max(5, 0.42 * len(df) + 3.5)), ncol=3,
              fontsize=8, frameon=False)
    ax.grid(axis="x", linestyle="--", alpha=0.3)
    return _save(fig, path, footer)


def _conv_text(r: pd.Series) -> str:
    text = f"{r['Konwersja_pct']:.1f}%"
    if not math.isnan(r["Przedzial_dol_pct"]):
        text += f" [{r['Przedzial_dol_pct']:.0f}–{r['Przedzial_gora_pct']:.0f}]"
    return text + (f"\n{r['Etykieta_pilotow']}" if r.get("Etykieta_pilotow") else "")


def chart_conversion(tbl: pd.DataFrame, key: str, baseline: float, path: Path, title: str, footer: str,
                     pilots: bool) -> Path:
    """Counterpart of challenge_conversion_matrix_*: top TOP_N_CHART rows by lists. Conversion on a
    diverging scale centred on the common baseline; other columns on their own 0..max scale."""
    df = tbl.sort_values("Listy", ascending=False).head(TOP_N_CHART).reset_index(drop=True)
    cols = [
        ("Listy", "listy\nTop 32", lambda r: f"{int(r['Listy'])}"),
        ("Top8", "listy\nTop 8", lambda r: f"{int(r['Top8'])}"),
        ("Konwersja_pct", "konwersja Top 32 → Top 8\n[przedział bootstrap]", _conv_text),
        ("Trofea", "trofea", lambda r: f"{int(r['Trofea'])}"),
        ("Piloci_Top32", "piloci\nTop 32", lambda r: f"{int(r['Piloci_Top32'])}"),
    ]
    if pilots:
        def _f(col, fmt):
            return lambda r: "" if pd.isna(r[col]) else fmt.format(r[col])
        cols += [
            ("Piloci_Top8", "piloci\nTop 8", _f("Piloci_Top8", "{:.0f}")),
            ("Najcz_pilot_udzial_Top32_pct", "najczęstszy pilot\n% list Top 32", _f("Najcz_pilot_udzial_Top32_pct", "{:.1f}%")),
            ("Najcz_pilot_udzial_Top8_pct", "najczęstszy pilot\n% wejść do Top 8", _f("Najcz_pilot_udzial_Top8_pct", "{:.1f}%")),
            ("Najcz_pilot_konwersja_pct", "konwersja\nnajczęstszego", _f("Najcz_pilot_konwersja_pct", "{:.1f}%")),
            ("Bez_najcz_pilota_konwersja_pct", "konwersja\nbez niego", _f("Bez_najcz_pilota_konwersja_pct", "{:.1f}%")),
        ]
    seq = plt.get_cmap("YlGnBu")
    div = plt.get_cmap("RdBu")
    rgba = np.ones((len(df), len(cols), 4))
    level = np.zeros((len(df), len(cols)))
    for j, (col, _, _) in enumerate(cols):
        vals = pd.to_numeric(df[col], errors="coerce").to_numpy(dtype=float)
        ok = ~np.isnan(vals)
        if not ok.any():
            continue
        if col == "Konwersja_pct":
            # the scale spans the decks; the heterogeneous "bez talii" row is clipped to it
            deck_rows = ok & (df[key] != di.NO_DECK_LABEL).to_numpy()
            span = np.nanmax(np.abs(vals[deck_rows] - baseline)) if deck_rows.any() else 0.0
            span = span or 1.0
            pos = np.clip(0.5 + 0.5 * (vals - baseline) / span, 0.0, 1.0)
            rgba[ok, j] = div(pos[ok])
            level[ok, j] = np.abs(pos[ok] - 0.5) * 2
        else:
            top = np.nanmax(vals) if np.nanmax(vals) > 0 else 1.0
            rgba[ok, j] = seq(vals[ok] / top)
            level[ok, j] = vals[ok] / top
    fig, ax = plt.subplots(figsize=(4 + 1.6 * len(cols), 0.4 * len(df) + 4.2))
    ax.imshow(rgba, aspect="auto")
    for i, row in df.iterrows():
        for j, (_, _, fmt) in enumerate(cols):
            ax.text(j, i, fmt(row), ha="center", va="center", fontsize=7,
                    color="#111111" if level[i, j] < 0.65 else "#ffffff")
    ax.set_yticks(range(len(df)))
    ax.set_yticklabels(df[key])
    ax.set_xticks(range(len(cols)))
    ax.set_xticklabels([lbl for _, lbl, _ in cols], fontsize=7.5)
    ax.set_title(
        f"{title}\nkonwersja: skala rozbieżna wokół bazy wspólnej okresu {baseline:.2f}% (listy Top 8 / listy); "
        "pozostałe kolumny: skala osobna 0…max",
        fontsize=9,
    )
    return _save(fig, path, footer)


def chart_trend(trend: pd.DataFrame, current: pd.DataFrame, key: str, path: Path, title: str, footer: str) -> Path:
    """Counterpart of meta_trend_*_lastNw: top 10 of the last week, one line each, disjoint ISO weeks."""
    top = current.sort_values("Listy", ascending=False).head(10)[key].tolist()
    weeks = trend[["Tydzien_ISO"]].drop_duplicates().sort_values("Tydzien_ISO")["Tydzien_ISO"].tolist()
    fig, ax = plt.subplots(figsize=(12, 6.8))
    for label in top:
        s = trend[trend[key] == label].set_index("Tydzien_ISO")["Udzial_Top32_pct"].reindex(weeks).fillna(0.0)
        ax.plot(weeks, s.values, marker="o", label=label)
    ax.set_xlabel("tydzień ISO")
    ax.set_ylabel(f"{SHARE_LABEL} (%)")
    ax.set_title(f"{title} — ostatnie {len(weeks)} tygodni ISO (rozłączne)")
    ax.tick_params(axis="x", rotation=35)
    ax.legend(fontsize=8, loc="upper left", bbox_to_anchor=(1.0, 1.0))
    ax.grid(linestyle="--", alpha=0.3)
    return _save(fig, path, footer)


def _stamp_cols(df: pd.DataFrame, export: di.DeckdbExport) -> pd.DataFrame:
    out = df.copy()
    out["rules_version"] = export.rules_version
    out["export_sha256"] = export.sha256
    return out


def _status_rows(export: di.DeckdbExport, level: str, start: date, end: date, rows: pd.DataFrame, cov: dict,
                 extra: List[tuple]) -> pd.DataFrame:
    base = [
        ("Status", "ROBOCZE — tryb wejścia deckdb, nie publikować bez akceptacji"),
        ("Poziom czasu", level),
        ("Okres (obustronnie domknięty, event_date)", f"{start.isoformat()} … {end.isoformat()}"),
        ("Eventy", cov["events"]),
        ("Listy Top 32", cov["lists"]),
        ("Listy z talią", int((rows["status"] == di.STATUS_ASSIGNED).sum())),
        ("Listy bez talii (Rogue + nieprzypisane)", int((rows["status"] != di.STATUS_ASSIGNED).sum())),
        ("  w tym Rogue", int((rows["status"] == di.STATUS_ROGUE).sum())),
        ("  w tym nieprzypisane", int((rows["status"] == di.STATUS_UNASSIGNED).sum())),
        ("Suma player_count eventów", cov["participants"]),
        ("Pokrycie uczestników (listy / suma player_count), %", round(cov["coverage_pct"], 2)),
        ("rules_version", export.rules_version),
        ("name_match_version", export.name_match_version),
        ("data_version", export.data_version),
        ("Plik eksportu", str(export.path)),
        ("sha256 eksportu", export.sha256),
        ("Daty eventów w eksporcie", f"{export.first_event_date} … {export.last_event_date}"),
        ("Populacja", "opublikowane listy Top 32 Modern Challenge; nie meta MTGO ani lig"),
        ("Winrate", "nie jest miarą (DeckDB K36)"),
    ]
    return pd.DataFrame(base + extra, columns=["Pole", "Wartość"])


def _write_level(out_dir: Path, label: str, export: di.DeckdbExport, sheets: Dict[str, pd.DataFrame],
                 csvs: Dict[str, pd.DataFrame]) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    xlsx = out_dir / f"deckdb_ROBOCZE_{label}.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as writer:
        for name, df in sheets.items():
            (df if not df.empty else pd.DataFrame([{"Info": "brak wierszy"}])).to_excel(writer, sheet_name=name,
                                                                                         index=False)
        pd.DataFrame(GLOSSARY, columns=["Pojęcie", "Definicja"]).to_excel(writer, sheet_name="Slownik", index=False)
        pd.DataFrame(OMITTED_MATERIALS, columns=["Materiał trybu dotychczasowego", "Powód pominięcia"]).to_excel(
            writer, sheet_name="Pominiete", index=False)
    written.append(xlsx)
    for name, df in csvs.items():
        p = out_dir / f"{name}_ROBOCZE_{label}.csv"
        _stamp_cols(df, export).to_csv(p, index=False, encoding="utf-8-sig")
        written.append(p)
    gl = out_dir / "SLOWNIK.md"
    gl.write_text(
        "# Słownik pojęć (tryb deckdb, ROBOCZE)\n\n"
        f"DeckDB reguły `{export.rules_version}`, eksport `{export.path.name}`, sha256 `{export.sha256}`. "
        "Każdy plik CSV w tym katalogu ma te same kolumny `rules_version` i `export_sha256`.\n\n"
        + "\n".join(f"- **{t}** — {d}" for t, d in GLOSSARY) + "\n",
        encoding="utf-8",
    )
    written.append(gl)
    return written


def build_week(export: di.DeckdbExport, as_of: date, trend_weeks: int, root: Path) -> List[Path]:
    weeks = di.complete_iso_weeks(as_of, max(1, trend_weeks))
    ws, we = weeks[-1]
    rows = di.window_rows(export, ws, we)
    cov = di.coverage(rows)
    label = di.iso_week_label(ws)
    out = root / f"tydzien_{label}"
    decks = di.share_table(rows, "material_deck", "Talia", trophies=False)
    decks.insert(1, "Archetyp", decks["Talia"].map(di.deck_archetype_map(rows)))
    archs = di.share_table(rows, "material_archetype", "Archetyp", trophies=False)
    archs.insert(1, "Talie", archs["Archetyp"].map(di.archetype_decks(rows)).astype(int))
    trend_d = di.weekly_trend(export, weeks, "material_deck", "Talia")
    trend_a = di.weekly_trend(export, weeks, "material_archetype", "Archetyp")
    status = _status_rows(export, f"tydzień ISO {label}", ws, we, rows, cov, [
        ("Trend", f"{len(weeks)} rozłącznych tygodni ISO, ostatni {label}"),
    ])
    written = _write_level(out, label, export, {
        "STATUS": status, "Talie": decks, "Archetypy": archs,
        "Bez_talii_rozbicie": di.no_deck_split(rows, trophies=False), "Eventy": di.event_table(rows),
        "Trend_talie": trend_d, "Trend_archetypy": trend_a,
    }, {"talie": decks, "archetypy": archs, "udzial_top32_historia_tygodnie": trend_d})
    stamp = _stamp(export, "tydzień ISO " + label, ws, we, cov)
    terms = _glossary_terms("udział", "obecność", "Top 8", "tydzień")
    n_ev = cov["events"]
    written.append(chart_share(decks, "Talia", n_ev, out / "udzial_top32_talie.png",
                               f"Talie, tydzień {label}: {SHARE_LABEL}, obecność, Top 8", f"{stamp}\n{terms}"))
    written.append(chart_share(archs, "Archetyp", n_ev, out / "udzial_top32_archetypy.png",
                               f"Archetypy, tydzień {label}: {SHARE_LABEL}, obecność, Top 8", f"{stamp}\n{terms}"))
    tstamp = _stamp(export, "trend", weeks[0][0], we, di.coverage(di.window_rows(export, weeks[0][0], we)))
    tterms = _glossary_terms("udział", "tydzień")
    written.append(chart_trend(trend_d, decks, "Talia", out / f"trend_udzial_talie_last{len(weeks)}w.png",
                               f"Talie: {SHARE_LABEL}", f"{tstamp}\n{tterms}"))
    written.append(chart_trend(trend_a, archs, "Archetyp", out / f"trend_udzial_archetypy_last{len(weeks)}w.png",
                               f"Archetypy: {SHARE_LABEL}", f"{tstamp}\n{tterms}"))
    return written


def build_month(export: di.DeckdbExport, as_of: date, root: Path) -> List[Path]:
    ms, me, complete = di.month_period(as_of)
    rows = di.window_rows(export, ms, me)
    cov = di.coverage(rows)
    label = ms.strftime("%Y-%m") + ("" if complete else "_niepelny")
    out = root / f"miesiac_{label}"
    decks = di.share_table(rows, "material_deck", "Talia", trophies=True)
    decks.insert(1, "Archetyp", decks["Talia"].map(di.deck_archetype_map(rows)))
    archs = di.share_table(rows, "material_archetype", "Archetyp", trophies=True)
    archs.insert(1, "Talie", archs["Archetyp"].map(di.archetype_decks(rows)).astype(int))
    note = "" if complete else f" (miesiąc niepełny: eksport kończy się {export.last_event_date})"
    status = _status_rows(export, f"miesiąc {ms.strftime('%Y-%m')}", ms, me, rows, cov, [
        ("Kompletność", "pełny" if complete else f"NIEPEŁNY — tylko robocze; dane do {me.isoformat()}"),
        ("Warianty (D2)", dict(GLOSSARY)["warianty"]),
    ])
    written = _write_level(out, label, export, {
        "STATUS": status, "Talie": decks, "Archetypy": archs, "Warianty": di.variant_table(rows),
        "Bez_talii_rozbicie": di.no_deck_split(rows, trophies=True), "Eventy": di.event_table(rows),
    }, {"talie": decks, "archetypy": archs, "finalisci": di.finalists_table(rows)})
    stamp = _stamp(export, "miesiąc", ms, me, cov, note)
    terms = _glossary_terms("udział", "obecność", "Top 8", "miesiąc")
    n_ev = cov["events"]
    written.append(chart_share(decks, "Talia", n_ev, out / "udzial_top32_talie.png",
                               f"Talie, {label}: {SHARE_LABEL}, obecność, Top 8", f"{stamp}\n{terms}"))
    written.append(chart_share(archs, "Archetyp", n_ev, out / "udzial_top32_archetypy.png",
                               f"Archetypy, {label}: {SHARE_LABEL}, obecność, Top 8", f"{stamp}\n{terms}"))
    return written


def build_format_period(export: di.DeckdbExport, start: date, event_key: str, as_of: date, root: Path) -> List[Path]:
    rows = di.window_rows(export, start, as_of)
    cov = di.coverage(rows)
    baseline = di.top8_baseline(rows)
    label = f"{start.isoformat()}_to_{as_of.isoformat()}"
    out = root / f"okres_formatu_{label}"
    decks = di.conversion_table(rows, "material_deck", "Talia", pilots=True)
    decks.insert(1, "Archetyp", decks["Talia"].map(di.deck_archetype_map(rows)))
    archs = di.conversion_table(rows, "material_archetype", "Archetyp", pilots=False)
    archs.insert(1, "Talie", archs["Archetyp"].map(di.archetype_decks(rows)).astype(int))
    pilot_cols = ["Talia", "Listy", "Top8", "Piloci_Top32", "Piloci_Top8", "Najcz_pilot_listy",
                  "Najcz_pilot_udzial_Top32_pct", "Najcz_pilot_Top8", "Najcz_pilot_udzial_Top8_pct",
                  "Najcz_pilot_konwersja_pct", "Bez_najcz_pilota_listy", "Bez_najcz_pilota_konwersja_pct",
                  "Remis_najcz_pilota"]
    pilots = decks[decks["Talia"] != di.NO_DECK_LABEL][pilot_cols].reset_index(drop=True)
    deck_cols = ["Talia", "Archetyp", "Listy", "Top8", "Konwersja_pct", "Przedzial_dol_pct", "Przedzial_gora_pct",
                 "Przedzial_info", "Etykieta_pilotow", "Trofea", "Piloci_Top32"]
    status = _status_rows(export, "okres formatu", start, as_of, rows, cov, [
        ("Początek okresu formatu", f"{start.isoformat()} (effective_mtgo, wpis {event_key} w rules/format_events.yaml)"),
        ("Baza wspólna konwersji (listy Top 8 / listy okresu), %", round(baseline, 3)),
        ("  listy Top 8 w okresie", int(rows["top8"].sum())),
        ("Przedział konwersji", dict(GLOSSARY)["przedział"] + f" Ziarno {di.BOOT_SEED}."),
        ("Miary pilotów", "tylko dla talii (nie dla archetypów i nie dla „bez talii”); arkusz Piloci"),
    ])
    written = _write_level(out, label, export, {
        "STATUS": status, "Talie": decks[deck_cols], "Archetypy": archs, "Piloci": pilots,
        "Bez_talii_rozbicie": di.no_deck_split(rows, trophies=True), "Eventy": di.event_table(rows),
    }, {"talie": decks[deck_cols], "archetypy": archs, "piloci": pilots, "finalisci": di.finalists_table(rows)})
    stamp = _stamp(export, "okres formatu", start, as_of, cov)
    written.append(chart_conversion(
        decks, "Talia", baseline, out / "konwersja_talie.png",
        f"Talie, okres formatu od {start.isoformat()}: konwersja Top 32 → Top 8 i piloci (top {TOP_N_CHART} wg list)",
        f"{stamp}\n{_glossary_terms('konwersja', 'przedział', 'etykiety pilotów', 'najczęstszy pilot', 'okres formatu')}",
        pilots=True))
    written.append(chart_conversion(
        archs, "Archetyp", baseline, out / "konwersja_archetypy.png",
        f"Archetypy, okres formatu od {start.isoformat()}: konwersja Top 32 → Top 8",
        f"{stamp}\n{_glossary_terms('konwersja', 'przedział', 'etykiety pilotów', 'okres formatu')}", pilots=False))
    return written


def chart_list_changes(tested: pd.DataFrame, path: Path, title: str, footer: str) -> Optional[Path]:
    """List changes: the within-variant component with its bootstrap interval, total change as a dot."""
    rep_ = tested[tested["Kategoria"] == dc.KIND_LIST].copy()
    if rep_.empty:
        return None
    rep_["_abs"] = rep_["Wewnatrz_wariantow_pp"].abs()
    rep_ = rep_.sort_values("_abs", ascending=False).head(30).sort_values("Wewnatrz_wariantow_pp")
    labels = [f"{r.Talia}: {r.Karta} ({'MD' if r.Strefa == 'main' else 'SB'}) · {r.Piloci_z_karta_1}→"
              f"{r.Piloci_z_karta_2} pil." for r in rep_.itertuples()]
    y = np.arange(len(rep_))
    fig, ax = plt.subplots(figsize=(13, 0.36 * len(rep_) + 4))
    w = rep_["Wewnatrz_wariantow_pp"].to_numpy()
    ax.barh(y, w, height=0.5, color="#2a6fb0", label="zmiana list: składowa wewnątrz wariantów (pp)")
    ax.errorbar(w, y, xerr=[w - rep_["Wewnatrz_dol_pp"], rep_["Wewnatrz_gora_pp"] - w], fmt="none",
                ecolor="#1b3d61", capsize=2, label="przedział bootstrap składowej")
    ax.plot(rep_["Zmiana_pp"], y, "o", color="#e3a346", label="zmiana obecności ogółem (pp)")
    ax.axvline(0, color="#333333", linewidth=1)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=7.5)
    ax.set_xlabel("pp")
    ax.set_title(title, fontsize=10)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.08), ncol=3, fontsize=8, frameon=False)
    ax.grid(axis="x", linestyle="--", alpha=0.3)
    return _save(fig, path, footer)


def chart_composition_changes(tested: pd.DataFrame, path: Path, title: str, footer: str) -> Optional[Path]:
    """Composition changes: reported total change split into within / proportions (stacked)."""
    rep_ = tested[tested["Kategoria"] == dc.KIND_COMPOSITION].copy()
    if rep_.empty:
        return None
    rep_["_abs"] = rep_["Zmiana_pp"].abs()
    rep_ = rep_.sort_values("_abs", ascending=False).head(30).sort_values("Zmiana_pp")
    labels = [f"{r.Talia}: {r.Karta} ({'MD' if r.Strefa == 'main' else 'SB'})" for r in rep_.itertuples()]
    y = np.arange(len(rep_))
    fig, ax = plt.subplots(figsize=(13, 0.36 * len(rep_) + 4))
    within = rep_["Wewnatrz_wariantow_pp"].to_numpy()
    mix = rep_["Proporcje_wariantow_pp"].to_numpy()
    ax.barh(y, mix, height=0.5, color="#e3a346", label="proporcje wariantów (pp)")
    ax.barh(y, within, left=mix, height=0.5, color="#9cc3e4", label="wewnątrz wariantów, nieistotna (pp)")
    ax.errorbar(rep_["Zmiana_pp"], y, xerr=[rep_["Zmiana_pp"] - rep_["Przedzial_dol_pp"],
                                             rep_["Przedzial_gora_pp"] - rep_["Zmiana_pp"]],
                fmt="o", color="#111111", ecolor="#555555", capsize=2, label="zmiana [przedział bootstrap]")
    ax.axvline(0, color="#333333", linewidth=1)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=7.5)
    ax.set_xlabel("zmiana obecności (pp)")
    ax.set_title(title, fontsize=10)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.08), ncol=3, fontsize=8, frameon=False)
    ax.grid(axis="x", linestyle="--", alpha=0.3)
    return _save(fig, path, footer)


def build_card_layer(export: di.DeckdbExport, cards: "dc.CardLayer", as_of: date, period_start: date,
                     root: Path) -> List[Path]:
    m2s, m2e, complete = di.month_period(as_of)
    m1s, m1e = di.previous_month(m2s)
    label = f"{m1s:%Y-%m}_vs_{m2s:%Y-%m}" + ("" if complete else "_niepelny")
    out = root / f"karty_{label}"
    t = dc.change_tables(export, cards, (m1s, m1e), (m2s, m2e))
    first = dc.first_appearances(export, cards, m2s, m2e)
    pilots = dc.pilot_list_changes(export, cards, period_start, as_of)
    tested = t["tested"]
    outside = t["outside"]
    rows = pd.concat([di.window_rows(export, m1s, m1e), di.window_rows(export, m2s, m2e)])
    cov = di.coverage(rows)
    n_tested = len(tested)
    kind = tested["Kategoria"] if n_tested else pd.Series(dtype=str)
    list_changes = tested[kind == dc.KIND_LIST] if n_tested else tested
    composition = tested[kind == dc.KIND_COMPOSITION] if n_tested else tested
    gone = outside[outside["Sciezka"].str.startswith("zniknięcie")] if not outside.empty else outside
    new = outside[outside["Sciezka"].str.startswith("nowa")] if not outside.empty else outside

    def _n(df: pd.DataFrame) -> str:
        return f"{int(df['Zgloszona'].sum())} zgłoszonych, {int((df['Zgloszona'] == 0).sum())} obserwacji" \
            if not df.empty else "0"

    status = _status_rows(export, f"warstwa kart, miesiące {m1s:%Y-%m} i {m2s:%Y-%m}", m1s, m2e, rows, cov, [
        ("Porównanie", f"{m1s} … {m1e} vs {m2s} … {m2e}"
                       + ("" if complete else " — miesiąc 2 NIEPEŁNY, porównanie tylko robocze")),
        ("Plik kart", str(cards.path)),
        ("sha256 pliku kart", cards.sha256),
        ("name_match_version / scryfall_dump_id", f"{cards.name_match_version} / {cards.scryfall_dump_id}"),
        ("data_version (karty = przypisania)", cards.data_version),
        ("Wiersze lądów z nadtypem Basic (wyłączone)", cards.basic_rows),
        ("Talie testowane (≥ 30 list i ≥ 10 pilotów w obu miesiącach)",
         int(t["eligible"]["Testowana"].sum()) if not t["eligible"].empty else 0),
        ("Testowane pary talia–strefa–karta", n_tested),
        ("Rodziny BH (każda q = 0,10)", f"dwie: testy zmiany ogółem ({n_tested}) i testy składowej wewnątrz "
                                         f"wariantów ({n_tested}); FDR kontrolowany osobno w każdej kategorii"),
        ("Zmiany obecności zgłoszone (|zmiana| ≥ 10 pp, BH q = 0,10)",
         int(tested["Raportowana"].sum()) if n_tested else 0),
        (f"  w tym „{dc.KIND_COMPOSITION}”", len(composition)),
        (f"„{dc.KIND_LIST}” (|wewnątrz| ≥ 10 pp, BH q = 0,10 w rodzinie składowych wewnątrz wariantów)",
         len(list_changes)),
        (f"  w tym bez zgłoszonej zmiany ogółem", int((list_changes["Raportowana"] == 0).sum()) if n_tested else 0),
        ("Zniknięcia (0% w miesiącu 2)", _n(gone)),
        ("Nowe karty (0% w miesiącu 1)", _n(new)),
        ("Pierwsze pojawienia w miesiącu 2", _n(first)),
        ("Zastrzeżenie: warianty", VARIANT_SPLIT_NOTE),
        ("Bootstrap",f"{di.BOOT_DRAWS} losowań, ziarno {di.BOOT_SEED}, piloci niezależnie w każdym miesiącu; "
                      "składowa wewnątrz wariantów liczona na tych samych losowaniach"),
    ])
    sheets = {
        "STATUS": status,
        "Zmiany_list": list_changes,
        "Zmiany_skladu_talii": composition,
        "Znikniecia": gone,
        "Nowe_karty": new,
        "Pierwsze_pojawienia": first,
        "Testowane": tested,
        "Talie_kwalifikacja": t["eligible"],
        f"Obecnosc_{m1s:%Y-%m}": t["presence_1"],
        f"Obecnosc_{m2s:%Y-%m}": t["presence_2"],
        "Listy_pilotow": pilots,
    }
    out.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    xlsx = out / f"deckdb_karty_ROBOCZE_{label}.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as writer:
        for name, df in sheets.items():
            (df if not df.empty else pd.DataFrame([{"Info": "brak wierszy"}])).to_excel(writer, sheet_name=name[:31],
                                                                                         index=False)
        pd.DataFrame(GLOSSARY + CARD_GLOSSARY, columns=["Pojęcie", "Definicja"]).to_excel(
            writer, sheet_name="Slownik", index=False)
    written.append(xlsx)
    reported_only = {
        "zmiany_list": list_changes,
        "zmiany_skladu_talii": composition,
        "znikniecia": gone[gone["Zgloszona"] == 1] if not gone.empty else gone,
        "nowe_karty": new[new["Zgloszona"] == 1] if not new.empty else new,
        "pierwsze_pojawienia": first[first["Zgloszona"] == 1] if not first.empty else first,
        "listy_pilotow": pilots,
    }
    for name, df in reported_only.items():
        pth = out / f"{name}_ROBOCZE_{label}.csv"
        st = _stamp_cols(df, export)
        st["cards_sha256"] = cards.sha256
        if name in ("zmiany_list", "zmiany_skladu_talii"):
            st["uwaga_warianty"] = VARIANT_SPLIT_NOTE
        st.to_csv(pth, index=False, encoding="utf-8-sig")
        written.append(pth)
    gl = out / "SLOWNIK.md"
    gl.write_text(
        "# Słownik pojęć — warstwa kart (tryb deckdb, ROBOCZE)\n\n"
        f"DeckDB reguły `{export.rules_version}`, eksport przypisań sha256 `{export.sha256}`, eksport kart "
        f"`{cards.path.name}` sha256 `{cards.sha256}`.\n\n"
        + "\n".join(f"- **{a}** — {b}" for a, b in GLOSSARY + CARD_GLOSSARY) + "\n",
        encoding="utf-8",
    )
    written.append(gl)
    g = dict(CARD_GLOSSARY)
    stamp = (_stamp(export, "karty", m1s, m2e, cov, "" if complete else " (miesiąc 2 niepełny)")
             + f" · karty sha256 {cards.sha256}\n")
    ch = chart_list_changes(
        tested, out / "zmiany_list.png",
        f"Zmiany list {m1s:%Y-%m} → {m2s:%Y-%m}: {len(list_changes)} istotnych wewnątrz wariantów "
        f"z {n_tested} testowanych (top 30 wg |składowej|)\npiloci z kartą w miesiącu 1 → 2",
        stamp + "\n".join([*(f"{k}: {g[k]}" for k in ("zmiana obecności", dc.KIND_LIST, "rozkład zmiany")),
                           VARIANT_SPLIT_NOTE]))
    if ch is not None:
        written.append(ch)
    ch = chart_composition_changes(
        tested, out / "zmiany_skladu_talii.png",
        f"Zmiany składu talii {m1s:%Y-%m} → {m2s:%Y-%m}: {len(composition)} zgłoszonych zmian obecności bez "
        "istotnej zmiany list\n(top 30 wg |zmiany|)",
        stamp + "\n".join([*(f"{k}: {g[k]}" for k in ("raportowanie zmiany", dc.KIND_COMPOSITION, "rozkład zmiany")),
                           VARIANT_SPLIT_NOTE]))
    if ch is not None:
        written.append(ch)
    return written


def build_deckdb_materials(export: di.DeckdbExport, cfg: di.DeckdbConfig, as_of: date, outputs_base: Path,
                           trend_weeks: int = 12, cache_dir: Optional[Path] = None,
                           log: Callable[[str], None] = print) -> List[Path]:
    start, key = di.format_period_start(cfg.format_events_path, as_of)
    # validate every period before writing anything
    weeks = di.complete_iso_weeks(as_of, max(1, trend_weeks))
    for ws, we in weeks:
        di.window_rows(export, ws, we)
    ms, me, _ = di.month_period(as_of)
    month_rows = di.window_rows(export, ms, me)
    di.window_rows(export, start, as_of)
    di.window_rows(export, *di.previous_month(ms))
    # the card file (versions, data_version, list keys) is checked before writing anything, too
    cards = dc.load_cards(cfg.cards_path, export) if cfg.cards_path is not None else None
    root = root_dir(outputs_base, as_of)
    written = build_week(export, as_of, trend_weeks, root)
    written += build_month(export, as_of, root)
    written += build_format_period(export, start, key, as_of, root)
    if cards is not None:
        written += build_card_layer(export, cards, as_of, start, root)
    if cache_dir is not None:
        cache = di.match_mtgo_cache(month_rows, cache_dir, ms, me)
        log(f"[deckdb] cache MTGO (kontrola, miesiąc {ms:%Y-%m}): dopasowane {cache['matched']}, eksport bez cache'u "
            f"{cache['export_not_in_cache']}, cache bez eksportu {cache['cache_not_in_export']}")
    log(f"[deckdb] stan {as_of}: reguły {export.rules_version}, sha256 {export.sha256}; okres formatu od {start} "
        f"({key})")
    for p in written:
        log(f"[deckdb] zapisano {p}")
    return written
