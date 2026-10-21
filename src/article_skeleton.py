"""Monthly article skeleton (`--skeleton monthly --period YYYY-MM`).

Writes outputs/articles/article_skeleton_<YYYY-MM>.md: a self-contained Markdown file meant to be
handed to a separate writing session that has no access to this repo or its data, so every number
the article could need is inside the file, each with its n (and a CI for every proportion).

Data only, no prose: the file holds headings, data blockquotes and a methodology block. Text outside
the blockquotes is left for the author. No adjectives, no conclusions, change direction as a signed
number ("+2,7 pp").

Scope: Modern Challenges only (challenge_history_<format>.csv). Premier and paper events are a
separate product and never read here. An event is a distinct EventID; EventSlug is NOT an event
key (mtgo.com-sourced rows from 2026-08-16 on carry the tier slug, e.g. "modern-challenge-64").

Deck labels are re-resolved in memory through the current mapping pipeline
(user_deck_mapping + challenge_deck_mapping -> deck_aliases), for both the reported month and the
previous one, so a merge applies retroactively and both months use the same mapping. The history
CSV itself is never written (stored Deck values are sticky by design -- see CLAUDE.md).
"""

from __future__ import annotations

import calendar
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import pandas as pd

try:
    from metagame_input_generator import (
        canonicalize_deck_name,
        load_aliases,
        load_user_deck_mappings,
        mapping_lookup,
        normalize_name,
    )
    from challenge_history_engine import _identity_key
except ModuleNotFoundError:
    from .metagame_input_generator import (
        canonicalize_deck_name,
        load_aliases,
        load_user_deck_mappings,
        mapping_lookup,
        normalize_name,
    )
    from .challenge_history_engine import _identity_key

Z95 = 1.959963984540054
TOP_N = 10

MONTHS_PL = {
    1: "styczeń", 2: "luty", 3: "marzec", 4: "kwiecień", 5: "maj", 6: "czerwiec",
    7: "lipiec", 8: "sierpień", 9: "wrzesień", 10: "październik", 11: "listopad", 12: "grudzień",
}


class SkeletonError(RuntimeError):
    """A precondition failed (coverage mismatch, missing data) -- nothing is written."""


# --------------------------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------------------------

def wilson_ci(k: int, n: int, z: float = Z95) -> Tuple[float, float]:
    """95% Wilson score interval for k/n. n == 0 -> (0, 0): no interval, not a fake one."""
    if n <= 0:
        return 0.0, 0.0
    p = k / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def newcombe_diff_ci(k1: int, n1: int, k2: int, n2: int, z: float = Z95) -> Tuple[float, float, float]:
    """p1 - p2 with Newcombe's hybrid score interval (method 10, built from two Wilson intervals).
    Returns (diff, lo, hi)."""
    p1 = k1 / n1 if n1 else 0.0
    p2 = k2 / n2 if n2 else 0.0
    l1, u1 = wilson_ci(k1, n1, z)
    l2, u2 = wilson_ci(k2, n2, z)
    d = p1 - p2
    lo = d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    hi = d + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return d, lo, hi


# --------------------------------------------------------------------------------------------
# Formatting (Polish conventions: decimal comma, thin-space-free "1 884" thousands)
# --------------------------------------------------------------------------------------------

def fmt_int(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def fmt_pct(x: float, digits: int = 1) -> str:
    return f"{x * 100:.{digits}f}".replace(".", ",") + "%"


def fmt_pp(x: float, digits: int = 1, signed: bool = True) -> str:
    v = round(x * 100, digits)
    if v == 0:
        v = 0.0  # no "-0,0"
    s = f"{v:+.{digits}f}" if signed else f"{v:.{digits}f}"
    return s.replace(".", ",") + " pp"


def fmt_prop(k: int, n: int) -> str:
    """'12,3% (k/n; 95% CI Wilson 10,1–14,8%)'"""
    lo, hi = wilson_ci(k, n)
    p = k / n if n else 0.0
    return (
        f"{fmt_pct(p)} ({fmt_int(k)}/{fmt_int(n)}; 95% CI Wilson "
        f"{fmt_pct(lo).rstrip('%')}–{fmt_pct(hi)})"
    )


def fmt_date(d: date) -> str:
    return d.isoformat()


# --------------------------------------------------------------------------------------------
# Data loading
# --------------------------------------------------------------------------------------------

def month_bounds(period: str) -> Tuple[date, date]:
    y, m = (int(x) for x in period.split("-"))
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def previous_period(period: str) -> str:
    start, _ = month_bounds(period)
    prev_end = start - timedelta(days=1)
    return f"{prev_end.year:04d}-{prev_end.month:02d}"


def _norm_id(x: object) -> str:
    s = str(x or "").strip()
    return s[:-2] if s.endswith(".0") else s


@dataclass
class Relabel:
    month: str
    old: str
    new: str
    rows: int


def load_challenge_month_rows(
    history_csv: Path,
    periods: Sequence[str],
    format_name: str,
    aliases_csv: Path,
    user_mapping_csv: Path,
    challenge_mapping_csv: Path,
) -> Tuple[pd.DataFrame, List[Relabel]]:
    """Challenge rows for *periods*, with Deck re-resolved through the current mapping pipeline.
    Returns (rows, relabels) where relabels lists every stored label the current mapping changes."""
    df = pd.read_csv(history_csv, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    df = df[df["Format"].str.strip().str.lower() == format_name.strip().lower()].copy()
    df["Month"] = df["EventDate"].str[:7]
    df = df[df["Month"].isin(periods)].copy()
    df["EventID"] = df["EventID"].map(_norm_id)
    df["LoginID"] = df.get("LoginID", "").map(_norm_id) if "LoginID" in df.columns else ""
    df["PlaceN"] = pd.to_numeric(df["Place"], errors="coerce")
    df["Date"] = pd.to_datetime(df["EventDate"], errors="coerce").dt.date

    aliases = load_aliases(aliases_csv)
    mappings = []
    for p in (user_mapping_csv, challenge_mapping_csv):
        if p.exists():
            mappings += load_user_deck_mappings(p)
    lookup = mapping_lookup(mappings)

    def resolve(raw: str) -> str:
        m = lookup.get(normalize_name(raw))
        if m is not None and m.canonical_name:
            return m.canonical_name
        return canonicalize_deck_name(raw, aliases)

    df["StoredDeck"] = df["Deck"].str.strip()
    df["Deck"] = df["StoredDeck"].map(resolve)
    changed = df[df["Deck"] != df["StoredDeck"]]
    relabels = [
        Relabel(month=m, old=o, new=n, rows=int(c))
        for (m, o, n), c in changed.groupby(["Month", "StoredDeck", "Deck"]).size().items()
    ]
    df["Identity"] = [_identity_key(l, p) for l, p in zip(df["LoginID"], df["Pilot"])]
    return df, relabels


# --------------------------------------------------------------------------------------------
# Coverage
# --------------------------------------------------------------------------------------------

@dataclass
class CoverageReport:
    period: str
    events_in_data: int
    events_validated: Optional[int]
    missing_from_data: List[str] = field(default_factory=list)
    extra_in_data: List[str] = field(default_factory=list)
    events_per_day: Dict[date, int] = field(default_factory=dict)

    @property
    def empty_days(self) -> List[date]:
        return [d for d, n in self.events_per_day.items() if n == 0]

    @property
    def ok(self) -> bool:
        return self.events_validated is not None and not self.missing_from_data and not self.extra_in_data


def coverage_report(rows: pd.DataFrame, period: str, validated_ids: Optional[Set[str]]) -> CoverageReport:
    start, end = month_bounds(period)
    m = rows[rows["Month"] == period]
    ids = set(m["EventID"])
    per_day = m.groupby("Date")["EventID"].nunique().to_dict()
    days = {start + timedelta(days=i): int(per_day.get(start + timedelta(days=i), 0)) for i in range((end - start).days + 1)}
    rep = CoverageReport(period=period, events_in_data=len(ids), events_validated=None, events_per_day=days)
    if validated_ids is not None:
        rep.events_validated = len(validated_ids)
        rep.missing_from_data = sorted(validated_ids - ids)
        rep.extra_in_data = sorted(ids - validated_ids)
    return rep


def mtgo_validated_challenge_ids(
    format_name: str, period: str, log: Optional[Callable[[str], None]] = None
) -> Set[str]:
    """Challenge EventIDs listed on mtgo.com's monthly decklists page -- the same registry the
    weekly completeness validation uses (challenge_mtgo_source.build_mtgo_registry)."""
    try:
        from challenge_mtgo_source import build_mtgo_registry
    except ModuleNotFoundError:
        from .challenge_mtgo_source import build_mtgo_registry
    start, end = month_bounds(period)
    reg = build_mtgo_registry(format_name, start, end, log=log)
    return {e.event_id for e in reg if e.kind == "challenge"}


# --------------------------------------------------------------------------------------------
# Per-deck statistics
# --------------------------------------------------------------------------------------------

@dataclass
class DeckMonth:
    deck: str
    entries: int
    top8: int
    wins: int
    pilots: int
    top3: List[Tuple[str, int]]  # (display name, entries)

    @property
    def top3_entries(self) -> int:
        return sum(n for _, n in self.top3)


def _latest_name(g: pd.DataFrame) -> str:
    # Latest by actual EventDate (CLAUDE.md pitfall: never "last distinct value").
    g = g.sort_values(["Date", "EventID"], kind="mergesort")
    return str(g["Pilot"].iloc[-1]).strip()


def deck_month_stats(month_rows: pd.DataFrame) -> Dict[str, DeckMonth]:
    out: Dict[str, DeckMonth] = {}
    for deck, g in month_rows.groupby("Deck"):
        per_pilot = g.groupby("Identity").size().sort_values(ascending=False, kind="mergesort")
        # deterministic tie-break among equal counts: by display name
        names = {ident: _latest_name(gg) for ident, gg in g.groupby("Identity")}
        ranked = sorted(per_pilot.items(), key=lambda kv: (-kv[1], names[kv[0]].lower()))
        out[deck] = DeckMonth(
            deck=deck,
            entries=len(g),
            top8=int((g["PlaceN"] <= 8).sum()),
            wins=int((g["PlaceN"] == 1).sum()),
            pilots=int(g["Identity"].nunique()),
            top3=[(names[i], int(n)) for i, n in ranked[:3]],
        )
    return out


def rank_by_share(stats: Dict[str, DeckMonth]) -> List[str]:
    """Order by entries desc; tie-break by Top 8 count desc, then deck name (documented in the
    methodology block)."""
    return sorted(stats, key=lambda d: (-stats[d].entries, -stats[d].top8, d.lower()))


def iso_weeks_clipped(start: date, end: date) -> List[Tuple[date, date]]:
    weeks = []
    cur = start
    while cur <= end:
        week_end = min(cur + timedelta(days=6 - cur.weekday()), end)
        weeks.append((cur, week_end))
        cur = week_end + timedelta(days=1)
    return weeks


# --------------------------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------------------------

@dataclass
class GateSummary:
    """Per canonical deck: lists checked / below threshold by the alias gate, per month."""
    period: str
    threshold: float
    checked: Dict[str, int]
    outliers: Dict[str, int]


def render_monthly_skeleton(
    rows: pd.DataFrame,
    period: str,
    prev: str,
    coverage: CoverageReport,
    prev_coverage: CoverageReport,
    relabels: List[Relabel],
    gates: List[GateSummary],
    validated_on: date,
    format_name: str = "Modern",
) -> str:
    start, end = month_bounds(period)
    pstart, pend = month_bounds(prev)
    cur = rows[rows["Month"] == period]
    old = rows[rows["Month"] == prev]
    N, Np = len(cur), len(old)
    E, Ep = cur["EventID"].nunique(), old["EventID"].nunique()
    if N == 0 or Np == 0:
        raise SkeletonError(f"no Challenge rows for {period if N == 0 else prev}")

    cs, ps = deck_month_stats(cur), deck_month_stats(old)
    cur_rank, prev_rank = rank_by_share(cs), rank_by_share(ps)
    top = cur_rank[:TOP_N]
    prev_pos = {d: i + 1 for i, d in enumerate(prev_rank)}
    cur_pos = {d: i + 1 for i, d in enumerate(cur_rank)}
    weeks = iso_weeks_clipped(start, end)
    mname, pname = MONTHS_PL[start.month], MONTHS_PL[pstart.month]

    L: List[str] = []
    L.append(f"# {format_name} Challenge — {mname} {start.year}")
    L.append("")
    L.append(f"> Okres: {fmt_date(start)} – {fmt_date(end)}")
    L.append(f"> Eventy (Modern Challenge, różne EventID): {E}")
    L.append(f"> Wejścia do Top 32: {fmt_int(N)}")
    L.append(f"> Okres porównawczy: {fmt_date(pstart)} – {fmt_date(pend)}; eventy: {Ep}; wejścia do Top 32: {fmt_int(Np)}")
    L.append(f"> Liczba różnych decków w Top 32: {len(cs)} (okres porównawczy: {len(ps)})")
    L.append(
        f"> Łączny udział {TOP_N} pierwszych decków: "
        + fmt_prop(sum(cs[d].entries for d in top), N)
    )
    L.append("")

    for i, d in enumerate(top, 1):
        s = cs[d]
        p = ps.get(d)
        k_prev = p.entries if p else 0
        diff, lo, hi = newcombe_diff_ci(s.entries, N, k_prev, Np)
        L.append(f"### {i}. {d}")
        L.append("")
        L.append(f"> Udział w miesiącu: {fmt_prop(s.entries, N)}")
        L.append(
            f"> Udział w okresie porównawczym ({pname}): {fmt_prop(k_prev, Np)}; "
            f"pozycja: {('#' + str(prev_pos[d])) if d in prev_pos else 'brak w Top 32'}"
        )
        L.append(
            f"> Zmiana udziału wobec okresu porównawczego: {fmt_pp(diff)} "
            f"(95% CI Newcombe {fmt_pp(lo)} do {fmt_pp(hi)})"
        )
        L.append(f"> Konwersja do Top 8 (Top 8 / wejścia do Top 32): {fmt_prop(s.top8, s.entries)}")
        L.append(f"> Konwersja do trofeum (1. miejsce / wejścia do Top 32): {fmt_prop(s.wins, s.entries)}")
        L.append(">")
        L.append("> Udział tydzień po tygodniu (tygodnie ISO przycięte do miesiąca):")
        L.append(">")
        L.append("> | Tydzień | Daty | Eventy | Wejścia decku / wszystkie | Udział | 95% CI Wilson |")
        L.append("> |---|---|---:|---:|---:|---|")
        for ws, we in weeks:
            w = cur[(cur["Date"] >= ws) & (cur["Date"] <= we)]
            n_ev = w["EventID"].nunique()
            n_w = len(w)
            k_w = int((w["Deck"] == d).sum())
            iso_y, iso_w, _ = ws.isocalendar()
            if n_w:
                wl, wh = wilson_ci(k_w, n_w)
                share, ci = fmt_pct(k_w / n_w), f"{fmt_pct(wl).rstrip('%')}–{fmt_pct(wh)}"
            else:
                share, ci = "—", "—"
            L.append(f"> | W{iso_w:02d} | {fmt_date(ws)} – {fmt_date(we)} | {n_ev} | {k_w}/{n_w} | {share} | {ci} |")
        L.append(">")
        L.append(f"> Unikalni piloci: {s.pilots} (n = {s.entries} wejść)")
        L.append(f"> Udział trzech najczęstszych pilotów w wejściach decku: {fmt_prop(s.top3_entries, s.entries)}")
        L.append("> Trzech najczęstszych pilotów (wejścia): " + "; ".join(f"{nm} ({n})" for nm, n in s.top3))
        L.append("")

    dropped = [d for d in prev_rank[:TOP_N] if d not in top]
    L.append(f"### Wypadły z pierwszej dziesiątki wobec okresu porównawczego ({pname} → {mname})")
    L.append("")
    if not dropped:
        L.append("> Brak: pierwsza dziesiątka okresu porównawczego w całości pozostała w pierwszej dziesiątce.")
    else:
        L.append("> | Deck | Pozycja " + pname + " | Udział " + pname + " | Pozycja " + mname + " | Udział " + mname + " | Zmiana (95% CI Newcombe) |")
        L.append("> |---|---:|---|---:|---|---|")
        for d in dropped:
            kp = ps[d].entries
            kc = cs[d].entries if d in cs else 0
            diff, lo, hi = newcombe_diff_ci(kc, N, kp, Np)
            L.append(
                f"> | {d} | #{prev_pos[d]} | {fmt_prop(kp, Np)} | "
                f"{('#' + str(cur_pos[d])) if d in cur_pos else 'brak w Top 32'} | {fmt_prop(kc, N)} | "
                f"{fmt_pp(diff)} ({fmt_pp(lo)} do {fmt_pp(hi)}) |"
            )
    L.append("")

    L.append("### Metodologia")
    L.append("")
    short = cur.groupby("EventID").agg(n=("Place", "size"), date=("EventDate", "first"), tier=("Tier", "first"), pc=("PlayerCount", "first"))
    short = short[short["n"] < 32].sort_values("date")
    L.append(f"> Źródło: oficjalne listy decków MTGO (mtgo.com/decklists), Modern Challenge (rozmiary 16/32/64/96). Wyłącznie Challenge: bez eventów premier (Showcase, Qualifier, Super Qualifier) i bez turniejów papierowych.")
    L.append(
        f"> Eventy: {fmt_date(start)} – {fmt_date(end)}: {E}; {fmt_date(pstart)} – {fmt_date(pend)}: {Ep}. "
        f"Event = jedno EventID. Liczby zgodne z listą eventów na miesięcznych stronach mtgo.com/decklists (sprawdzone {fmt_date(validated_on)}: "
        f"{coverage.events_validated} i {prev_coverage.events_validated}, ten sam zbiór EventID)."
    )
    empty = coverage.empty_days
    L.append(
        f"> Dni bez Modern Challenge w {fmt_date(start)} – {fmt_date(end)}: "
        + (", ".join(fmt_date(d) for d in empty) if empty else "brak")
        + f" ({len(empty)} z {len(coverage.events_per_day)})."
    )
    L.append(
        "> Udziały liczone są na wejściach do Top 32 (miejsca 1–32 każdego eventu), a nie na całym polu graczy; "
        "decki spoza Top 32 nie są widoczne w danych."
    )
    if len(short):
        parts = [
            f"EventID {eid} ({r.date}, Challenge {r.tier}, graczy: {_norm_id(r.pc) or 'brak danych'}, wejść: {r.n})"
            for eid, r in short.iterrows()
        ]
        L.append(
            f"> Eventy z mniej niż 32 graczami w {fmt_date(start)} – {fmt_date(end)} ({len(short)}): "
            + "; ".join(parts)
            + f". Łączna liczba wejść to {fmt_int(N)}, a nie {E} × 32 = {fmt_int(E * 32)}."
        )
    else:
        L.append(f"> Każdy event miał co najmniej 32 graczy: {fmt_int(N)} = {E} × 32 wejść.")
    L.append(f"> Konwersja do Top 8 = liczba miejsc 1–8 / liczba wejść do Top 32 decku; konwersja do trofeum = liczba 1. miejsc / liczba wejść do Top 32 decku.")
    L.append("> Przedziały ufności 95%: Wilson dla proporcji; Newcombe (metoda hybrydowa score, z dwóch przedziałów Wilsona) dla różnicy udziałów między miesiącami. Obliczenia zakładają niezależność wejść; wejścia tego samego pilota w kilku eventach nie są niezależne, więc przedziały są raczej za wąskie niż za szerokie.")
    L.append("> Ranking: liczba wejść do Top 32 w całym miesiącu (malejąco); przy remisie liczba miejsc 1–8, potem nazwa decku.")
    for label, st, rk in ((f"{fmt_date(start)} – {fmt_date(end)}", cs, cur_rank), (f"{fmt_date(pstart)} – {fmt_date(pend)}", ps, prev_rank)):
        if len(rk) > TOP_N and st[rk[TOP_N - 1]].entries == st[rk[TOP_N]].entries:
            k = st[rk[TOP_N - 1]].entries
            tied = [d for d in rk if st[d].entries == k]
            L.append(
                f"> Remis na granicy pierwszej dziesiątki ({label}): "
                + "; ".join(f"{d} (#{rk.index(d) + 1}, wejść: {k}, Top 8: {st[d].top8})" for d in tied)
                + f". Rozstrzygnięcie według reguły powyżej."
            )
    L.append("> Tygodnie: tygodnie ISO (poniedziałek–niedziela) przycięte do granic miesiąca; tygodnie skrajne mogą mieć mniej eventów — liczba eventów podana przy każdym tygodniu.")
    L.append("> Piloci: jeden pilot = jedno konto MTGO (LoginID, z uwzględnieniem ręcznie potwierdzonych połączeń kont); nazwa wyświetlana z ostatniego eventu w miesiącu.")
    L.append(
        f"> Nazwy decków: etykiety ujednolicone jednym, bieżącym mapowaniem (aliasy i scalenia nazw) dla obu miesięcy."
    )
    if relabels:
        L.append(
            "> Etykiety zmienione przez bieżące mapowanie względem zapisanych danych: "
            + "; ".join(f"{r.month}: {r.old} → {r.new} (n = {r.rows})" for r in relabels)
            + "."
        )
    else:
        L.append("> Bieżące mapowanie nie zmieniło żadnej zapisanej etykiety w obu miesiącach.")
    for g in gates:
        flagged = {d: g.outliers.get(d, 0) for d in (top if g.period == period else prev_rank[:TOP_N]) if g.checked.get(d)}
        flagged = {d: n for d, n in flagged.items() if n}
        L.append(
            f"> Kontrola scaleń nazw ({g.period}, podobieństwo listy głównej do wcześniejszych list tego decku, próg {str(g.threshold).replace('.', ',')}): "
            + (
                "listy poniżej progu lub bez pobranej listy, w deckach z pierwszej dziesiątki tego miesiąca: "
                + "; ".join(f"{d}: {n} z {g.checked[d]}" for d, n in flagged.items())
                + ". Te wejścia są wliczone pod etykietą decku."
                if flagged
                else "brak list poniżej progu w deckach z pierwszej dziesiątki tego miesiąca."
            )
        )
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------------

def run_monthly_skeleton(
    period: str,
    format_name: str = "Modern",
    repo_root: Optional[Path] = None,
    out_dir: Optional[Path] = None,
    validated_ids: Optional[Dict[str, Set[str]]] = None,
    run_alias_gate: bool = True,
    log: Callable[[str], None] = print,
) -> Path:
    root = repo_root or Path(__file__).resolve().parents[1]
    slug = format_name.strip().lower()
    history_csv = root / "outputs" / f"challenge_history_{slug}.csv"
    out_dir = out_dir or (root / "outputs" / "articles")
    prev = previous_period(period)
    docs = root / "docs"

    rows, relabels = load_challenge_month_rows(
        history_csv, [prev, period], format_name,
        docs / "deck_aliases.csv", docs / "user_deck_mapping.csv", docs / "challenge_deck_mapping.csv",
    )

    # 1. Coverage: abort only on a monthly EventID mismatch against mtgo.com's monthly page.
    reports = {}
    for p in (prev, period):
        ids = validated_ids.get(p) if validated_ids else mtgo_validated_challenge_ids(format_name, p, log=log)
        rep = coverage_report(rows, p, ids)
        reports[p] = rep
        log(f"[skeleton] coverage {p}: data={rep.events_in_data} mtgo.com={rep.events_validated}")
        log("[skeleton]   events per day: " + ", ".join(f"{d.day:02d}:{n}" for d, n in rep.events_per_day.items()))
        log("[skeleton]   empty days: " + (", ".join(d.isoformat() for d in rep.empty_days) or "none"))
        if not rep.ok:
            for eid in rep.missing_from_data:
                log(f"[skeleton]   MISSING from history: EventID {eid}")
            for eid in rep.extra_in_data:
                log(f"[skeleton]   in history but not on mtgo.com month page: EventID {eid}")
            raise SkeletonError(
                f"{p}: history has {rep.events_in_data} Challenge events, mtgo.com lists "
                f"{rep.events_validated} -- not generating"
            )

    # 2. Retroactive label diff under the current mapping.
    log(f"[skeleton] label diff under current mapping ({prev}, {period}): {sum(r.rows for r in relabels)} row(s)")
    for r in relabels:
        log(f"[skeleton]   {r.month}: {r.old} -> {r.new} ({r.rows})")

    # 3. Alias gate (check_window_against_baseline via run_alias_checks_for_window), per month.
    gates: List[GateSummary] = []
    if run_alias_gate:
        try:
            from alias_check import load_alias_check_threshold, run_alias_checks_for_window
        except ModuleNotFoundError:
            from .alias_check import load_alias_check_threshold, run_alias_checks_for_window
        threshold = load_alias_check_threshold(docs / "alias_check_config.csv")
        out_dir.mkdir(parents=True, exist_ok=True)
        for p in (prev, period):
            ps, pe = month_bounds(p)
            reps = run_alias_checks_for_window(
                history_csv, root / "outputs" / "cache" / "mtgo_json" / slug, docs / "deck_aliases.csv",
                docs / "alias_check_config.csv", format_name, ps, pe,
                out_dir / f"alias_report_{p}.csv", log=log,
            )
            checked: Counter = Counter()
            outl: Counter = Counter()
            for r in reps:
                # baseline-drift report: "<canon> (this window)"; literal pre-merge report:
                # source = raw pattern, target = canonical name
                canon = (
                    r.source_name.replace(" (this window)", "")
                    if r.source_name.endswith(" (this window)")
                    else r.target_name
                )
                checked[canon] = max(checked[canon], len(r.source_lists))
                outl[canon] = max(outl[canon], len(r.outliers))
            gates.append(GateSummary(period=p, threshold=threshold, checked=dict(checked), outliers=dict(outl)))

    text = render_monthly_skeleton(
        rows, period, prev, reports[period], reports[prev], relabels, gates, validated_on=date.today(),
        format_name=format_name,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"article_skeleton_{period}.md"
    path.write_text(text, encoding="utf-8")
    log(f"[skeleton] wrote {path}")
    return path
