"""The monthly package monthly/<YYYY-MM>/ of the week runner (order of 2026-11-03, docs/runner.md "Paczka miesięczna"):
README_metagame_info.md (docs/monthly_package_rules.md with a header), the checks of the package against the export
(every mismatch is a Stop, no fallback) and run_report_monthly.md."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from .common import EXIT_MONTHLY, REPO, Stop, sha256

RULES_REL = "docs/monthly_package_rules.md"
README_NAME = "README_metagame_info.md"
REPORT_NAME = "run_report_monthly.md"
M_SHEETS = ("m_deck_results", "m_results_bands", "m_list_changes")
PACKAGE_SHEETS = M_SHEETS + ("monthly_metrics", "variants_month")
PACKAGE_PNGS = ("m01_deck_results.png", "m02_list_changes.png")
NO_DECK = "Rogue & unclassified"
MEASURE_FLAG = {"Mean_place_percentile": "Percentile_BH_rejects", "Top8_conversion_pct": "Conversion_BH_rejects"}
HEADER_LINES = 4  # blockquote, empty, ---, empty


def closing_month(week_sunday: date) -> Optional[str]:
    """'YYYY-MM' of the month whose monthly material belongs to the ISO week ending on *week_sunday* (the week that
    contains the first day of the next month; deckdb_folder.month_for), None for any other week."""
    first = week_sunday.replace(day=1)
    if not week_sunday - timedelta(days=6) <= first <= week_sunday:
        return None
    return f"{first - timedelta(days=1):%Y-%m}"


def month_bounds(month: str) -> tuple[date, date]:
    try:
        ms = date.fromisoformat(f"{month}-01")
    except ValueError:
        raise Stop(2, f"--month {month!r}: oczekiwany format RRRR-MM (np. 2026-10)") from None
    return ms, (ms + timedelta(days=32)).replace(day=1) - timedelta(days=1)


def readme_header(generated: datetime, commit: str, rules_version: str) -> str:
    return (f"> Generated: {generated:%Y-%m-%d %H:%M:%S} · pipeline commit `{commit}` · DeckDB rules version "
            f"`{rules_version}`\n\n---\n\n")


def write_readme(dest: Path, generated: datetime, commit: str, rules_version: str, rules_path: Path) -> Path:
    if not rules_path.exists():
        raise Stop(EXIT_MONTHLY, f"brak {rules_path} (zasady paczki miesięcznej dla metagame.info)")
    p = dest / README_NAME
    p.write_text(readme_header(generated, commit, rules_version) + rules_path.read_text(encoding="utf-8"),
                 encoding="utf-8")
    return p


# --- checks ------------------------------------------------------------------------------------------------------

class Checks:
    def __init__(self) -> None:
        self.lines: List[str] = []
        self.facts: Dict[str, object] = {}

    def ok(self, text: str) -> None:
        self.lines.append(f"- OK: {text}")

    def fail(self, text: str) -> None:
        self.lines.append(f"- BŁĄD: {text}")
        raise Stop(EXIT_MONTHLY, "kontrola paczki miesięcznej: " + text)


def _read_xlsx(path: Path) -> Dict[str, pd.DataFrame]:
    return pd.read_excel(path, sheet_name=None, dtype=object)


def _export(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df["final_rank"] = pd.to_numeric(df["final_rank"]).astype(int)
    df["event_date"] = df["event_date"].str.strip()
    return df


def check_package(folder: Path, month: str, export_path: Path, rules_version: str, export_sha: str,
                  data_version: str, rules_path: Path, week_folder: Optional[Path] = None,
                  checks: Optional[Checks] = None) -> Checks:
    """The checks of the order of 2026-11-03, points 4 and 8, on the files of *folder* against the export file."""
    c = checks or Checks()
    ms, me = month_bounds(month)
    nxt = me + timedelta(days=1)
    xl = _read_xlsx(folder / "data.xlsx")
    ex = _export(export_path)
    in_month = ex[(ex["event_date"] >= ms.isoformat()) & (ex["event_date"] <= me.isoformat())]

    # files
    missing = [n for n in ("data.xlsx", "notes.md", README_NAME, "m01_deck_results.png") if not (folder / n).exists()]
    if missing:
        c.fail(f"brak plików w {folder}: {missing}")
    sheets = [s for s in xl if s != "contents"]
    if list(xl)[:1] != ["contents"] or sheets != list(PACKAGE_SHEETS):
        c.fail(f"arkusze data.xlsx {list(xl)}, oczekiwane contents + {list(PACKAGE_SHEETS)}")
    c.ok(f"pliki i arkusze: {', '.join(sorted(p.name for p in folder.iterdir() if p.is_file()))}; arkusze "
         f"contents + {', '.join(sheets)}")

    # stamps
    for name in sheets:
        df = xl[name]
        if list(df.columns) == ["Info"]:
            continue
        for col, want in (("rules_version", rules_version), ("export_sha256", export_sha),
                          ("data_version", data_version)):
            vals = set(df[col].astype(str)) if col in df.columns else {"<brak kolumny>"}
            if vals != {want}:
                c.fail(f"{name}: {col} {sorted(vals)[:3]} zamiast {want}")
    c.ok(f"każdy arkusz ma rules_version {rules_version}, export_sha256 {export_sha[:12]}…, data_version "
         f"{data_version}")

    # 4a: end date = last day of the month, no list of the next month
    for name in M_SHEETS:
        df = xl[name]
        if list(df.columns) == ["Info"]:
            continue
        ends = set(df["Period_end"].astype(str))
        if ends != {me.isoformat()}:
            c.fail(f"{name}: Period_end {sorted(ends)} zamiast {me}")
    res = xl["m_deck_results"]
    fstart = str(res["Period_start"].iloc[0])
    period = ex[(ex["event_date"] >= fstart) & (ex["event_date"] <= me.isoformat())]
    if int(pd.to_numeric(res["Lists"]).sum()) != len(period):
        c.fail(f"m_deck_results: {int(pd.to_numeric(res['Lists']).sum())} list, eksport {fstart} .. {me}: "
               f"{len(period)} (lista spoza okresu?)")
    mm = xl["monthly_metrics"]
    if str(mm["month_to"].astype(str).max()) > me.isoformat():
        c.fail(f"monthly_metrics: miesiąc do {mm['month_to'].max()}, po {me}")
    if not (mm.loc[mm["month"].astype(str) == month, "complete_month"].astype(int) == 1).all():
        c.fail(f"monthly_metrics: {month} nie jest oznaczony jako pełny miesiąc")
    c.ok(f"data końcowa arkuszy m_* = {me}; m_deck_results {len(period)} list = eksport {fstart} .. {me}; "
         f"monthly_metrics do {mm['month_to'].astype(str).max()}; żadnej listy z {nxt} i później")

    # 4b: events and lists of the month in notes.md = the export
    notes = (folder / "notes.md").read_text(encoding="utf-8")
    m = re.search(r"^- Monthly material: (\S+) \.\. (\S+), complete month \((\d+) events, (\d+) lists\)$", notes, re.M)
    if not m:
        c.fail("notes.md: brak linii '- Monthly material: <od> .. <do>, complete month (<n> events, <n> lists)'")
    want = (ms.isoformat(), me.isoformat(), int(in_month["event_id"].nunique()), len(in_month))
    got = (m.group(1), m.group(2), int(m.group(3)), int(m.group(4)))
    if got != want:
        c.fail(f"notes.md: miesiąc {got}, eksport {want}")
    if not re.search(rf"^- Month: {month}, {ms} \.\. {me}, complete month$", notes, re.M):
        c.fail(f"notes.md: brak linii '- Month: {month}, {ms} .. {me}, complete month'")
    fm = re.search(r"^- Format period \(deck results, m01\): (\S+) \.\. (\S+); events (\d+), Challenge Top 32 lists "
                   r"(\d+)$", notes, re.M)
    if not fm or (fm.group(1), fm.group(2), int(fm.group(3)), int(fm.group(4))) != (
            fstart, me.isoformat(), int(period["event_id"].nunique()), len(period)):
        c.fail(f"notes.md: linia okresu formatu {fm.group(0) if fm else 'brak'} różna od eksportu "
               f"({fstart} .. {me}, {period['event_id'].nunique()} eventów, {len(period)} list)")
    c.ok(f"notes.md: {want[2]} eventów i {want[3]} list miesiąca = eksport; okres formatu {fstart} .. {me}: "
         f"{period['event_id'].nunique()} eventów, {len(period)} list = eksport; 'complete month'")
    c.facts.update(events=want[2], lists=want[3], period_start=fstart, period_events=int(period["event_id"].nunique()),
                   period_lists=len(period))

    # 4c: the m_* sheets equal the week folder's (same run)
    if week_folder is not None:
        wx = _read_xlsx(week_folder / "data.xlsx")
        for name in M_SHEETS:
            if name not in wx:
                c.fail(f"folder tygodnia {week_folder.name}: brak arkusza {name}")
            try:
                pd.testing.assert_frame_equal(xl[name], wx[name], check_exact=True)
            except AssertionError as e:
                c.fail(f"{name}: różny od arkusza folderu tygodnia {week_folder.name}: {str(e).splitlines()[0]}")
        for name in PACKAGE_PNGS:
            a, b = folder / name, week_folder / name
            if a.exists() != b.exists() or (a.exists() and sha256(a) != sha256(b)):
                c.fail(f"{name}: różny od pliku folderu tygodnia {week_folder.name}")
        c.ok(f"arkusze {', '.join(M_SHEETS)} identyczne z folderem tygodnia {week_folder.name} (komórka po komórce); "
             f"{', '.join(n for n in PACKAGE_PNGS if (folder / n).exists())} identyczne bajtowo")

    # 4d: variants_month sums = the deck's lists, Top 8, trophies
    v = xl["variants_month"]
    a = in_month[in_month["status"] == "assigned"]
    with_v = sorted(set(a.loc[a["variant"] != "", "deck"]))
    if list(v.columns) == ["Info"]:
        if with_v:
            c.fail(f"variants_month pusty, a talie z wariantami w miesiącu: {with_v}")
        summed = pd.DataFrame()
    else:
        for col in ("Lists", "Top8", "Trophies"):
            v[col] = pd.to_numeric(v[col]).astype(int)
        summed = v.groupby("Deck")[["Lists", "Top8", "Trophies"]].sum()
        if sorted(summed.index) != with_v:
            c.fail(f"variants_month: talie {sorted(summed.index)}, w eksporcie talie z wariantami {with_v}")
        sub = a[a["deck"].isin(with_v)]
        deck = pd.DataFrame({"Lists": sub.groupby("deck").size(),
                             "Top8": sub.assign(t=sub["final_rank"] <= 8).groupby("deck")["t"].sum().astype(int),
                             "Trophies": sub.assign(t=sub["final_rank"] == 1).groupby("deck")["t"].sum().astype(int)})
        bad = [d for d in with_v if tuple(summed.loc[d]) != tuple(int(x) for x in deck.loc[d])]
        if bad:
            c.fail(f"variants_month: suma wariantów różna od talii (eksport) dla {bad}")
    c.ok(f"variants_month: {len(summed)} talii z wariantami, suma wariantów = listy, Top 8 i trofea talii z eksportu")

    # 4e: m01 colour = the sheet's BH flags
    bands = xl["m_results_bands"]
    res_i = res.set_index("Deck")
    shown = res[res["Deck"] != NO_DECK].assign(L=pd.to_numeric(res["Lists"])).sort_values(
        ["L", "Deck"], ascending=[False, True])
    for r in bands.itertuples():
        if r.Deck == NO_DECK:
            continue
        coloured = r.Colour_geometry != "grey"
        flag = int(res_i.loc[r.Deck, MEASURE_FLAG[r.Measure]])
        if coloured != bool(int(r.Flag_sheet)) or int(r.Flag_sheet) != flag:
            c.fail(f"m01 {r.Deck} {r.Measure}: kolor {r.Colour_geometry}, Flag_sheet {r.Flag_sheet}, "
                   f"m_deck_results {MEASURE_FLAG[r.Measure]} = {flag}")
    if list(dict.fromkeys(bands["Deck"])) != list(shown["Deck"].head(len(set(bands["Deck"])))):
        c.fail("m_results_bands: talie wykresu m01 to nie talie z największą liczbą list m_deck_results")
    tested = res[(res["Deck"] != NO_DECK) & pd.to_numeric(res["Percentile_band_low"], errors="coerce").notna()]
    rej = {k: int(pd.to_numeric(res[col]).sum()) for k, col in (("percentile", "Percentile_BH_rejects"),
                                                               ("conversion", "Conversion_BH_rejects"))}
    c.ok(f"m01: kolor słupków = BH_rejects arkusza dla {len(set(bands['Deck']))} talii x 2 miar "
         f"({int((bands['Colour_geometry'] != 'grey').sum())} kolorowych)")
    c.facts.update(decks=int((res["Deck"] != NO_DECK).sum()), decks_tested=len(tested), bh_rejects=rej)

    # 8: README = docs/monthly_package_rules.md apart from the header
    lines = (folder / README_NAME).read_text(encoding="utf-8").split("\n")
    body = "\n".join(lines[HEADER_LINES:])
    if not lines[0].startswith("> Generated: ") or lines[2] != "---" or body != rules_path.read_text(encoding="utf-8"):
        c.fail(f"{README_NAME} różny od {RULES_REL} poza nagłówkiem")
    c.ok(f"{README_NAME} obecny, identyczny z {RULES_REL} poza nagłówkiem ({lines[0][2:]})")
    return c


def report(month: str, ok: bool, stop: Optional[Stop], facts: dict, checks: Checks, steps: list,
           versions: Dict[str, str], triage: Optional[dict], source: str) -> str:
    ms, me = month_bounds(month)
    out = [f"# Raport paczki miesięcznej {month}", "",
           f"- Miesiąc: {month}, {ms} .. {me}; źródło: {source}; wynik: "
           + ("OK" if ok else f"STOP (kod {stop.code})" if stop else "STOP"),
           f"- Uruchomienie: {datetime.now():%Y-%m-%d %H:%M:%S}"]
    if stop is not None:
        out += ["", "## Błąd", "", "```", stop.message, "```"]
    if "pipeline_commit" in facts:
        out += [f"- Commity: pipeline `{facts['pipeline_commit']}`, DeckDB `{facts['deckdb_commit']}`"]
    if "rules_version" in facts:
        out += [f"- Wersja reguł DeckDB: `{facts['rules_version']}`; data_version `{facts['data_version']}`",
                f"- Eksport przypisań: `{facts['export']['assignments'].name}`, sha256 "
                f"`{facts['export_sha']['assignments']}`",
                f"- Eksport kart: `{facts['export']['cards'].name}`, sha256 `{facts['export_sha']['cards']}`"]
    if triage:
        out += [f"- Triage (DeckDB K43, z tego przebiegu): {triage['status']}, {triage['pending_groups']} grup "
                f"czekających ({triage['waiting_share_of_week']:.1%} list {triage['week']['iso_week']})"]
    cf = checks.facts
    if "decks" in cf:
        out += [f"- m_deck_results: {cf['decks']} talii, {cf['decks_tested']} z testem (>= 10 pilotów); odrzucenia "
                f"BH: percentyl {cf['bh_rejects']['percentile']}, konwersja {cf['bh_rejects']['conversion']}"]
    if "rule_changes" in facts:
        out += [f"- Zmiany reguł DeckDB w miesiącu (notes.md): {facts['rule_changes']} linii"]
    out += ["", "## Kontrole", ""] + (checks.lines or ["- (nie wykonane)"])
    out += ["", "## Kroki", "", "| krok | czas | wynik |", "|---|---|---|"]
    out += [f"| {n} | {s:.0f} s | {r} |" for n, s, r in steps]
    if versions:
        out += ["", "Wersje: " + ", ".join(f"{k} {v}" for k, v in versions.items())]
    if "pipeline_cmd" in facts:
        out += ["", f"Komenda pipeline'u: `{facts['pipeline_cmd']}`"]
    return "\n".join(out) + "\n"


def rules_path() -> Path:
    return REPO / RULES_REL
