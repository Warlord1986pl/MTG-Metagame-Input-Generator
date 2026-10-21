"""Card layer of input mode "deckdb" (docs/deckdb_metodologia.md section (g), accepted 2026-10-21).

Input: the ``deckdb export-cards`` file (``cards_path`` in docs/deckdb_config.csv; DeckDB
``docs/export.md``): one row per (event_id, decktournamentid, zone, oracle_id), qty summed over the
oracle_id, ``is_basic`` from the supertype Basic. Lands with ``is_basic`` = 1 are excluded from every
measure. The file must cover exactly the lists of the assignment export (unmatched keys are counted
both ways and refuse the run) and carry one name-match version and one ``data_version``, both equal
to the assignment export's (an assignment export without ``data_version`` is refused).

Measures, for deck D, month T, zone z and card c (oracle_id), on D's lists in T:

  presence p      = lists with >= 1 copy of c in z / lists of D
  mean copies m   = copies of c in z / lists with >= 1 copy
  change          = p_T2 - p_T1 (pp), consecutive calendar months, for decks with >= 30 lists and
                    >= 10 pilots in BOTH months; interval = bootstrap over pilots drawn
                    independently in each month (no pairing), 2000 draws, percentiles 2.5 / 97.5;
                    p-value two-sided from the normal approximation z = change / bootstrap SE;
                    Benjamini-Hochberg q = 0.10 in the family of total-change tests (below); reported
                    when |change| >= 10 pp and BH rejects. A card with 0% presence in one of the months is not tested.
  decomposition   = change within variants + change of variant proportions (symmetric, sums to
                    the change exactly; all export variant labels, empty -> "(bez wariantu)"). The
                    within-variant component has its own bootstrap (same draws). TWO BH families, each
                    q = 0.10 over every tested (deck, zone, card) of all decks: total-change tests and
                    within-variant tests -- FDR is controlled separately in each category (the single
                    joint family of 2026-10-21 f was reverted before the full-October run).
                    "zmiana list" = |within| >= 10 pp and significant in its family; a reported total
                    change that is not one is "zmiana składu talii" (variant proportions). The split depends on
                    the current variant definitions; a deck without variants has within = total.
  gone card       = 0% in month 2: reported when (1 - share of D's month-1 pilots playing it) ^
                    (D's month-2 pilots) < 0.05 and >= 3 pilots played it in month 1; otherwise an
                    observation (order of 2026-10-21 f: on pilots, not lists).
  new card        = 0% in month 1: reported with >= 2 pilots in month 2; otherwise an observation.
  first appearance = earliest event_date of a list of D (status assigned) with >= 1 copy of c in z,
                    over the whole export; lists and pilots of D with c in the 30 days from it;
                    "przed początkiem danych" within 30 days of the export's first date; reported
                    with >= 2 pilots in those 30 days, otherwise an observation.
  pilot list changes = consecutive lists of the same pilot (format period), same deck: sum of
                    |copy differences| per zone, cards added / removed, moves main <-> sideboard;
                    a next list of another deck counts as a deck change. Only distributions per deck.

Pilots are the export's ``player`` only; no identifier reaches a material.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

try:
    import deckdb_input as di
except ModuleNotFoundError:
    from . import deckdb_input as di

MIN_LISTS = 30
MIN_PILOTS = 10
MIN_CHANGE_PP = 10.0
BH_Q = 0.10
FIRST_SEEN_DAYS = 30
NO_VARIANT = "(bez wariantu)"
# a "list change" is a within-variant change significant in the BH family of within-variant tests; a
# reported total change (BH family of total-change tests) that is not one is a "change of the deck's
# composition" (variant proportions). FDR is controlled separately in each of the two families.
KIND_LIST = "zmiana list"
KIND_COMPOSITION = "zmiana składu talii"
GONE_P = 0.05            # a gone card is reported when P(0 pilots in month 2 | month-1 pilot share) < GONE_P
MIN_PILOTS_GONE = 3      # ... and >= 3 pilots played it in month 1
MIN_PILOTS_NEW = 2       # a new card / first appearance is reported with >= 2 pilots
CARD_COLUMNS = ("event_id", "decktournamentid", "zone", "oracle_id", "canonical_name", "qty", "is_basic",
                "name_match_version", "scryfall_dump_id", "data_version")


class CardLayer:
    def __init__(self, path: Path, sha256: str, rows: pd.DataFrame, name_match_version: str,
                 scryfall_dump_id: str, data_version: str, basic_rows: int):
        self.path = path
        self.sha256 = sha256
        self.rows = rows
        self.name_match_version = name_match_version
        self.scryfall_dump_id = scryfall_dump_id
        self.data_version = data_version
        self.basic_rows = basic_rows


def load_cards(path: Optional[Path], export: di.DeckdbExport) -> CardLayer:
    if path is None or not path.exists():
        raise di.DeckdbInputError(f"Brak pliku eksportu kart (cards_path): {path}")
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")
    missing = [c for c in CARD_COLUMNS if c not in df.columns]
    if missing:
        raise di.DeckdbInputError(f"{path}: brak kolumn {missing}")
    one = {}
    for col in ("name_match_version", "scryfall_dump_id", "data_version"):
        vals = sorted(set(df[col]))
        if len(vals) != 1:
            raise di.DeckdbInputError(f"{path}: więcej niż jedna wartość {col}: {vals}")
        one[col] = vals[0]
    if one["name_match_version"] != export.name_match_version:
        raise di.DeckdbInputError(
            f"Wersja mapowania nazw: karty {one['name_match_version']}, przypisania {export.name_match_version}")
    # order of 2026-10-21 f: both files must come from the same DeckDB state
    if export.data_version is None:
        raise di.DeckdbInputError(
            f"{export.path.name}: eksport przypisań bez kolumny data_version (sprzed DeckDB 2026-10-21 d) — "
            "nie da się sprawdzić, że pochodzi z tego samego stanu bazy co eksport kart")
    if one["data_version"] != export.data_version:
        raise di.DeckdbInputError(
            f"data_version: karty {one['data_version']}, przypisania {export.data_version}")
    df["event_id"] = df["event_id"].map(di._norm_id)
    df["decktournamentid"] = df["decktournamentid"].map(di._norm_id)
    card_keys = set(zip(df["event_id"], df["decktournamentid"]))
    export_keys = set(zip(export.rows["event_id"], export.rows["decktournamentid"]))
    if card_keys != export_keys:
        raise di.DeckdbInputError(
            f"Listy kart i przypisań nie zgadzają się: {len(export_keys - card_keys)} list przypisań bez kart, "
            f"{len(card_keys - export_keys)} list kart bez przypisania")
    df["qty"] = pd.to_numeric(df["qty"]).astype(int)
    basic = df["is_basic"] == "1"
    return CardLayer(path, di.sha256_of(path), df[~basic].copy(), one["name_match_version"],
                     one["scryfall_dump_id"], one["data_version"], int(basic.sum()))


def _lists_with_cards(export_rows: pd.DataFrame, cards: CardLayer) -> pd.DataFrame:
    """One row per (list, zone, card) for the given assignment rows, with deck/variant/player/date."""
    meta = export_rows[["event_id", "decktournamentid", "event_date", "deck", "variant", "player", "final_rank"]]
    return cards.rows.merge(meta, on=["event_id", "decktournamentid"], how="inner")


def presence_table(lists: pd.DataFrame, long: pd.DataFrame) -> pd.DataFrame:
    """Presence and mean copies per (deck, zone, card) over *lists* (assigned lists of one month)."""
    n_lists = lists.groupby("deck").size()
    n_pilots = lists.groupby("deck")["player"].nunique()
    g = long.groupby(["deck", "zone", "oracle_id", "canonical_name"])
    out = g.agg(Listy_z_karta=("qty", "size"), Kopie=("qty", "sum"), Piloci_z_karta=("player", "nunique")).reset_index()
    out["Listy_talii"] = out["deck"].map(n_lists)
    out["Piloci_talii"] = out["deck"].map(n_pilots)
    out["Obecnosc_pct"] = out["Listy_z_karta"] / out["Listy_talii"] * 100.0
    out["Sr_kopii"] = out["Kopie"] / out["Listy_z_karta"]
    return out.rename(columns={"deck": "Talia", "zone": "Strefa", "oracle_id": "oracle_id",
                               "canonical_name": "Karta"}).drop(columns="Kopie")


def _bh(pvals: np.ndarray, q: float) -> tuple[np.ndarray, np.ndarray]:
    """Benjamini-Hochberg: (reject flags, adjusted q-values)."""
    n = len(pvals)
    if n == 0:
        return np.zeros(0, dtype=bool), np.zeros(0)
    order = np.argsort(pvals)
    ranked = pvals[order] * n / np.arange(1, n + 1)
    adj = np.minimum.accumulate(ranked[::-1])[::-1]
    qvals = np.empty(n)
    qvals[order] = np.minimum(adj, 1.0)
    return qvals <= q, qvals


def _norm_sf2(z: np.ndarray) -> np.ndarray:
    from math import erfc, sqrt
    return np.array([erfc(abs(v) / sqrt(2.0)) for v in z])


def _pilot_variant_arrays(lists: pd.DataFrame, long: pd.DataFrame, keys: List[tuple],
                          variants: List[str]) -> tuple[np.ndarray, np.ndarray]:
    """(lists per pilot x variant, lists-with-card per pilot x variant x card) for one deck and month."""
    pilots = sorted(lists["player"].unique())
    pidx = {p: i for i, p in enumerate(pilots)}
    vidx = {v: i for i, v in enumerate(variants)}
    kidx = {k: j for j, k in enumerate(keys)}
    n = np.zeros((len(pilots), len(variants)))
    for (player, v), c in lists.groupby(["player", "_v"]).size().items():
        n[pidx[player], vidx[v]] = c
    x = np.zeros((len(pilots), len(variants), len(keys)))
    for (player, v, zone, oid), c in long.groupby(["player", "_v", "zone", "oracle_id"]).size().items():
        x[pidx[player], vidx[v], kidx[(zone, oid)]] = c
    return n, x


def _weights(n_pilots: int, draws: int, rng: np.random.Generator) -> np.ndarray:
    """draws x pilots multinomial weights of a draw of pilots with replacement."""
    return np.stack([np.bincount(rng.integers(0, n_pilots, n_pilots), minlength=n_pilots)
                     for _ in range(draws)]).astype(float)


def _components(w1: np.ndarray, n1: np.ndarray, x1: np.ndarray, w2: np.ndarray, n2: np.ndarray,
                x2: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """For weight rows w (draws x pilots): presence 1, presence 2, change within variants and change of
    variant proportions, each draws x cards, in % / pp. A variant absent in one month takes the
    other month's presence (its whole contribution goes to the proportions); absent in both: 0."""
    out = []
    for w, n, x in ((w1, n1, x1), (w2, n2, x2)):
        nv = w @ n                                                        # draws x V
        xv = (w @ x.reshape(x.shape[0], -1)).reshape(len(w), *x.shape[1:])  # draws x V x C
        tot = nv.sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            q = xv / nv[:, :, None]
        out.append((xv.sum(axis=1) / tot[:, None], q, nv / tot[:, None]))
    (p1, q1, s1), (p2, q2, s2) = out
    q1f = np.where(np.isnan(q1), q2, q1)
    q2f = np.where(np.isnan(q2), q1, q2)
    q1f, q2f = np.nan_to_num(q1f), np.nan_to_num(q2f)
    within = (((s1 + s2) / 2)[:, :, None] * (q2f - q1f)).sum(axis=1)
    mix = (((q1f + q2f) / 2) * (s2 - s1)[:, :, None]).sum(axis=1)
    return p1 * 100.0, p2 * 100.0, within * 100.0, mix * 100.0


def _p_two_sided(est: np.ndarray, boot: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    se = boot.std(axis=0, ddof=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        z = np.where(se > 0, est / se, np.where(est == 0, 0.0, np.inf))
    return se, np.where(np.isinf(z), 0.0, _norm_sf2(np.nan_to_num(z, posinf=0.0)))


def change_tables(export: di.DeckdbExport, cards: CardLayer, m1: tuple[date, date], m2: tuple[date, date],
                  draws: int = di.BOOT_DRAWS, seed: int = di.BOOT_SEED) -> Dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    out_rows: List[dict] = []
    outside: List[dict] = []
    eligible: List[dict] = []
    lists = {}
    long = {}
    for tag, (s, e) in (("1", m1), ("2", m2)):
        r = di.window_rows(export, s, e)
        r = r[r["status"] == di.STATUS_ASSIGNED].assign(_v=lambda d: d["variant"].replace("", NO_VARIANT))
        lists[tag] = r
        long[tag] = _lists_with_cards(r, cards).assign(_v=lambda d: d["variant"].replace("", NO_VARIANT))
    for deck in sorted(set(lists["1"]["deck"]) & set(lists["2"]["deck"])):
        l1, l2 = lists["1"][lists["1"]["deck"] == deck], lists["2"][lists["2"]["deck"] == deck]
        ok = (len(l1) >= MIN_LISTS and len(l2) >= MIN_LISTS and l1["player"].nunique() >= MIN_PILOTS
              and l2["player"].nunique() >= MIN_PILOTS)
        eligible.append({"Talia": deck, "Listy_1": len(l1), "Piloci_1": l1["player"].nunique(), "Listy_2": len(l2),
                         "Piloci_2": l2["player"].nunique(), "Testowana": int(ok)})
        if not ok:
            continue
        c1, c2 = long["1"][long["1"]["deck"] == deck], long["2"][long["2"]["deck"] == deck]
        names = pd.concat([c1, c2]).drop_duplicates(["zone", "oracle_id"]).set_index(["zone", "oracle_id"])[
            "canonical_name"]
        keys = list(names.index)
        variants = sorted(set(l1["_v"]) | set(l2["_v"]))
        n1, x1 = _pilot_variant_arrays(l1, c1, keys, variants)
        n2, x2 = _pilot_variant_arrays(l2, c2, keys, variants)
        e1, e2 = np.ones((1, len(n1))), np.ones((1, len(n2)))
        p1, p2, within, mix = (a[0] for a in _components(e1, n1, x1, e2, n2, x2))
        bp1, bp2, bwithin, _bmix = _components(_weights(len(n1), draws, rng), n1, x1,
                                               _weights(len(n2), draws, rng), n2, x2)
        change = p2 - p1
        bchange = bp2 - bp1
        se, pv = _p_two_sided(change, bchange)
        se_w, pv_w = _p_two_sided(within, bwithin)
        lo, hi = np.percentile(bchange, [2.5, 97.5], axis=0)
        lo_w, hi_w = np.percentile(bwithin, [2.5, 97.5], axis=0)
        pilots1 = c1.groupby(["zone", "oracle_id"])["player"].nunique()
        pilots2 = c2.groupby(["zone", "oracle_id"])["player"].nunique()
        copies1 = c1.groupby(["zone", "oracle_id"])["qty"].mean()
        copies2 = c2.groupby(["zone", "oracle_id"])["qty"].mean()
        for j, k in enumerate(keys):
            rec = {"Talia": deck, "Strefa": k[0], "oracle_id": k[1], "Karta": names[k],
                   "Obecnosc_1_pct": p1[j], "Obecnosc_2_pct": p2[j],
                   "Piloci_z_karta_1": int(pilots1.get(k, 0)), "Piloci_z_karta_2": int(pilots2.get(k, 0)),
                   "Listy_talii_1": len(l1), "Listy_talii_2": len(l2)}
            if p1[j] == 0:
                rec["Sciezka"] = "nowa karta (0% w miesiącu 1)"
                rec["Zgloszona"] = int(rec["Piloci_z_karta_2"] >= MIN_PILOTS_NEW)
                outside.append(rec)
                continue
            if p2[j] == 0:
                # on pilots (order of 2026-10-21 f): share of D's month-1 pilots playing the card,
                # to the power of D's month-2 pilots
                prob0 = (1.0 - rec["Piloci_z_karta_1"] / l1["player"].nunique()) ** l2["player"].nunique()
                rec["Sciezka"] = "zniknięcie (0% w miesiącu 2)"
                rec["P_zero_list"] = prob0
                rec["Zgloszona"] = int(prob0 < GONE_P and rec["Piloci_z_karta_1"] >= MIN_PILOTS_GONE)
                outside.append(rec)
                continue
            rec.update({
                "Sr_kopii_1": float(copies1.get(k, math.nan)), "Sr_kopii_2": float(copies2.get(k, math.nan)),
                "Zmiana_pp": change[j], "Przedzial_dol_pp": lo[j], "Przedzial_gora_pp": hi[j],
                "SE_bootstrap_pp": se[j], "p": pv[j],
                "Wewnatrz_wariantow_pp": within[j], "Wewnatrz_dol_pp": lo_w[j], "Wewnatrz_gora_pp": hi_w[j],
                "SE_wewnatrz_pp": se_w[j], "p_wewnatrz": pv_w[j],
                "Proporcje_wariantow_pp": mix[j],
                "Piloci_talii_1": int(l1["player"].nunique()), "Piloci_talii_2": int(l2["player"].nunique()),
            })
            out_rows.append(rec)
    tested = pd.DataFrame(out_rows)
    if not tested.empty:
        # two families, FDR controlled separately in each (total change; within-variant component)
        reject, qv = _bh(tested["p"].to_numpy(), BH_Q)
        reject_w, qv_w = _bh(tested["p_wewnatrz"].to_numpy(), BH_Q)
        tested["q_BH"] = qv
        tested["BH_odrzuca"] = reject.astype(int)
        tested["q_BH_wewnatrz"] = qv_w
        tested["BH_wewnatrz_odrzuca"] = reject_w.astype(int)
        tested["Raportowana"] = ((tested["Zmiana_pp"].abs() >= MIN_CHANGE_PP) & reject).astype(int)
        list_change = (tested["Wewnatrz_wariantow_pp"].abs() >= MIN_CHANGE_PP) & reject_w
        tested["Kategoria"] = np.where(list_change, KIND_LIST,
                                       np.where(tested["Raportowana"] == 1, KIND_COMPOSITION, ""))
        tested = tested.sort_values(["Kategoria", "Wewnatrz_wariantow_pp"], ascending=[False, True]).reset_index(
            drop=True)
    return {
        "tested": tested,
        "outside": pd.DataFrame(outside),
        "eligible": pd.DataFrame(eligible),
        "presence_1": presence_table(lists["1"], long["1"]),
        "presence_2": presence_table(lists["2"], long["2"]),
    }


def first_appearances(export: di.DeckdbExport, cards: CardLayer, since: date, until: date) -> pd.DataFrame:
    """Cards whose first list in a deck (whole export) falls in since..until."""
    rows = export.rows[export.rows["status"] == di.STATUS_ASSIGNED]
    long = _lists_with_cards(rows, cards).sort_values(["event_date", "event_id", "decktournamentid"])
    first = long.drop_duplicates(["deck", "zone", "oracle_id"])
    first = first[(first["event_date"] >= since.isoformat()) & (first["event_date"] <= until.isoformat())]
    start_data = date.fromisoformat(export.first_event_date)
    out = []
    for r in first.itertuples():
        d0 = date.fromisoformat(r.event_date)
        d1 = d0 + timedelta(days=FIRST_SEEN_DAYS - 1)
        later = long[(long["deck"] == r.deck) & (long["zone"] == r.zone) & (long["oracle_id"] == r.oracle_id)
                     & (long["event_date"] >= r.event_date) & (long["event_date"] <= d1.isoformat())]
        out.append({
            "Talia": r.deck, "Strefa": r.zone, "oracle_id": r.oracle_id, "Karta": r.canonical_name,
            "Pierwsza_data": r.event_date, "Miejsce_pierwszej_listy": int(r.final_rank),
            f"Listy_z_karta_{FIRST_SEEN_DAYS}d": len(later), f"Piloci_z_karta_{FIRST_SEEN_DAYS}d": int(later["player"].nunique()),
            "Okno_30d_niepelne": int(d1.isoformat() > export.last_event_date),
            "Przed_poczatkiem_danych": int(d0 < start_data + timedelta(days=FIRST_SEEN_DAYS)),
            "Zgloszona": int(later["player"].nunique() >= MIN_PILOTS_NEW),
        })
    return pd.DataFrame(out).sort_values(["Talia", "Pierwsza_data", "Karta"]).reset_index(drop=True) if out else pd.DataFrame()


def pilot_list_changes(export: di.DeckdbExport, cards: CardLayer, start: date, end: date) -> pd.DataFrame:
    """Per deck: distribution of differences between consecutive lists of the same pilot."""
    rows = di.window_rows(export, start, end)
    rows = rows[rows["status"] == di.STATUS_ASSIGNED].sort_values(["player", "event_date", "event_id"])
    content: Dict[tuple, Dict[tuple, int]] = {}
    for (eid, dtid), g in _lists_with_cards(rows, cards).groupby(["event_id", "decktournamentid"]):
        content[(eid, dtid)] = dict(zip(zip(g["zone"], g["oracle_id"]), g["qty"]))
    per_deck: Dict[str, dict] = {}
    for _, g in rows.groupby("player"):
        prev = None
        for r in g.itertuples():
            if prev is not None:
                acc = per_deck.setdefault(prev.deck, {"pairs": [], "deck_changes": 0, "gaps": []})
                if r.deck != prev.deck:
                    acc["deck_changes"] += 1
                else:
                    a = content.get((prev.event_id, prev.decktournamentid), {})
                    b = content.get((r.event_id, r.decktournamentid), {})
                    diff = {k: b.get(k, 0) - a.get(k, 0) for k in set(a) | set(b)}
                    main = sum(abs(v) for (z, _), v in diff.items() if z == "main")
                    side = sum(abs(v) for (z, _), v in diff.items() if z != "main")
                    added = sum(1 for k in diff if k not in a)
                    removed = sum(1 for k in diff if k not in b)
                    # a move: the same card lost in one zone and gained in the other
                    moves = sum(min(abs(v), abs(diff.get(("sideboard", oid), 0)))
                                for (z, oid), v in diff.items()
                                if z == "main" and v * diff.get(("sideboard", oid), 0) < 0)
                    acc["pairs"].append((main, side, added, removed, moves))
                    acc["gaps"].append((date.fromisoformat(r.event_date) - date.fromisoformat(prev.event_date)).days)
            prev = r
    out = []
    for deck, acc in sorted(per_deck.items()):
        p = np.array(acc["pairs"]) if acc["pairs"] else np.zeros((0, 5))
        rec = {"Talia": deck, "Pary_list": len(p), "Zmiany_talii": acc["deck_changes"]}
        if len(p):
            total = p[:, 0] + p[:, 1]
            rec.update({
                "Bez_zmian_pct": float((total == 0).mean() * 100.0),
                "Main_mediana": float(np.median(p[:, 0])), "Main_Q1": float(np.percentile(p[:, 0], 25)),
                "Main_Q3": float(np.percentile(p[:, 0], 75)),
                "Side_mediana": float(np.median(p[:, 1])), "Side_Q1": float(np.percentile(p[:, 1], 25)),
                "Side_Q3": float(np.percentile(p[:, 1], 75)),
                "Dodane_karty_mediana": float(np.median(p[:, 2])), "Usuniete_karty_mediana": float(np.median(p[:, 3])),
                "Przeniesienia_main_side_srednio": float(p[:, 4].mean()),
                "Odstep_dni_mediana": float(np.median(acc["gaps"])),
            })
        out.append(rec)
    return pd.DataFrame(out).sort_values("Pary_list", ascending=False).reset_index(drop=True)
