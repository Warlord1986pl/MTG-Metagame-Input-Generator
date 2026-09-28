"""Card-list gate for deck-name alias merges (see CLAUDE.md's WBG/Devoted Combo case).

A deck-alias merge (e.g. a raw MTGO color-code name like "WBG" being folded into an existing
canonical deck) must never be applied on the strength of the name alone: the name gives no
guarantee that every list filed under it is actually the same deck. This module compares the
maindeck card multiset of every list filed under the *source* name against every list filed
under the *target* name, reusing the same `multiset_similarity` / `MtgoEventDeck.signature`
machinery challenge_mtgo_source.py already uses for its own nearest-neighbor deck classification
-- this is deliberately not a second, parallel comparison mechanism.

Comparison is maindeck-only (basic lands excluded), the same scope `multiset_similarity` already
uses elsewhere in this codebase -- a combo deck's sideboard can differ entirely between builds
without affecting the verdict here.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pandas as pd

try:
    from challenge_mtgo_source import MtgoEventDeck, multiset_similarity, parse_mtgo_event
    from league_matches import load_mtgo_event_json
except ModuleNotFoundError:
    from .challenge_mtgo_source import MtgoEventDeck, multiset_similarity, parse_mtgo_event
    from .league_matches import load_mtgo_event_json

DEFAULT_MIN_SIMILARITY = 0.55
ALIAS_CHECK_CONFIG_COLUMNS = ["key", "value"]


def load_alias_check_threshold(config_csv: Path) -> float:
    """Reads the decision threshold from docs/alias_check_config.csv (config, not code) so the
    cutoff a merge verdict was made under stays inspectable after the fact."""
    if not config_csv.exists():
        return DEFAULT_MIN_SIMILARITY
    try:
        with config_csv.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                if str(row.get("key") or "").strip() == "min_similarity":
                    return float(str(row.get("value") or "").strip())
    except (OSError, ValueError):
        pass
    return DEFAULT_MIN_SIMILARITY


def _normalize_event_id(raw: object) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        as_float = float(text)
        if as_float.is_integer():
            return str(int(as_float))
    except ValueError:
        pass
    return text


@dataclass
class ListResult:
    event_id: str
    event_date: str
    place: str
    pilot: str
    loginid: str
    similarity: Optional[float]
    matched_against_event: Optional[str]
    flag: str  # "match", "outlier", "no_decklist_cached"


@dataclass
class AliasCheckReport:
    source_name: str
    target_name: str
    window_start: date
    window_end: date
    threshold: float
    source_lists: List[ListResult] = field(default_factory=list)
    target_lists_count: int = 0
    verdict: str = "dangerous"  # "safe" | "partial" | "dangerous"
    median_similarity: Optional[float] = None
    min_similarity: Optional[float] = None
    max_similarity: Optional[float] = None
    common_cards: List[str] = field(default_factory=list)
    scope_note: str = (
        "Comparison is maindeck-only (basic lands excluded) -- the same scope "
        "multiset_similarity() uses elsewhere in this codebase. A sideboard-only difference "
        "between builds is invisible to this check."
    )

    @property
    def outliers(self) -> List[ListResult]:
        return [r for r in self.source_lists if r.flag != "match"]


def _event_signatures(cache_dir: Path, event_id: str) -> Dict[str, Dict[str, int]]:
    """loginid -> maindeck signature for one cached mtgo.com event JSON. Empty dict on a cache
    miss or unparseable event (never fetches -- see load_mtgo_event_json's own contract)."""
    data = load_mtgo_event_json(cache_dir, event_id)
    if not data:
        return {}
    try:
        decks: List[MtgoEventDeck] = parse_mtgo_event(data)
    except Exception:
        return {}
    return {d.loginid: d.signature for d in decks if d.signature}


def check_alias(
    history_csv: Path,
    mtgo_json_cache_dir: Path,
    alias_check_config_csv: Path,
    format_name: str,
    source_name: str,
    target_name: str,
    window_start: date,
    window_end: date,
) -> AliasCheckReport:
    threshold = load_alias_check_threshold(alias_check_config_csv)
    report = AliasCheckReport(
        source_name=source_name,
        target_name=target_name,
        window_start=window_start,
        window_end=window_end,
        threshold=threshold,
    )

    if not history_csv.exists():
        return report

    df = pd.read_csv(history_csv, dtype=str).fillna("")
    df["_date"] = pd.to_datetime(df["EventDate"], errors="coerce").dt.date
    fmt_norm = str(format_name or "").strip().lower()
    in_scope = (
        df["Format"].astype(str).str.strip().str.lower().eq(fmt_norm)
        & df["_date"].notna()
        & (df["_date"] >= window_start)
        & (df["_date"] <= window_end)
    )
    window_df = df[in_scope]

    source_rows = window_df[window_df["Deck"] == source_name]
    target_rows = window_df[window_df["Deck"] == target_name]
    report.target_lists_count = len(target_rows)

    # Build the target library: (event_id, loginid) -> signature, for every cached target list.
    target_signatures: Dict[str, Dict[str, int]] = {}
    event_cache: Dict[str, Dict[str, Dict[str, int]]] = {}

    def signatures_for(event_id: str) -> Dict[str, Dict[str, int]]:
        if event_id not in event_cache:
            event_cache[event_id] = _event_signatures(mtgo_json_cache_dir, event_id)
        return event_cache[event_id]

    target_keys: List[str] = []
    for _, row in target_rows.iterrows():
        eid = _normalize_event_id(row.get("EventID"))
        sigs = signatures_for(eid)
        loginid = str(row.get("LoginID") or "").strip()
        sig = sigs.get(loginid) or sigs.get(loginid.split(".")[0])
        if sig:
            key = f"{eid}:{loginid}"
            target_signatures[key] = sig
            target_keys.append(key)

    similarities: List[float] = []
    for _, row in source_rows.iterrows():
        eid = _normalize_event_id(row.get("EventID"))
        sigs = signatures_for(eid)
        loginid = str(row.get("LoginID") or "").strip()
        sig = sigs.get(loginid) or sigs.get(loginid.split(".")[0])

        if not sig:
            report.source_lists.append(
                ListResult(
                    event_id=eid,
                    event_date=str(row.get("EventDate") or ""),
                    place=str(row.get("Place") or ""),
                    pilot=str(row.get("Pilot") or ""),
                    loginid=loginid,
                    similarity=None,
                    matched_against_event=None,
                    flag="no_decklist_cached",
                )
            )
            continue

        best_sim = -1.0
        best_key = None
        for key, target_sig in target_signatures.items():
            sim = multiset_similarity(sig, target_sig)
            if sim > best_sim:
                best_sim = sim
                best_key = key

        if best_key is None:
            report.source_lists.append(
                ListResult(
                    event_id=eid,
                    event_date=str(row.get("EventDate") or ""),
                    place=str(row.get("Place") or ""),
                    pilot=str(row.get("Pilot") or ""),
                    loginid=loginid,
                    similarity=None,
                    matched_against_event=None,
                    flag="no_decklist_cached",
                )
            )
            continue

        similarities.append(best_sim)
        flag = "match" if best_sim >= threshold else "outlier"
        report.source_lists.append(
            ListResult(
                event_id=eid,
                event_date=str(row.get("EventDate") or ""),
                place=str(row.get("Place") or ""),
                pilot=str(row.get("Pilot") or ""),
                loginid=loginid,
                similarity=round(best_sim, 4),
                matched_against_event=best_key.split(":")[0],
                flag=flag,
            )
        )

    if similarities:
        s = sorted(similarities)
        n = len(s)
        report.median_similarity = round(s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2, 4)
        report.min_similarity = round(min(s), 4)
        report.max_similarity = round(max(s), 4)

    all_sig_sets = [set(sig.keys()) for sig in list(target_signatures.values())]
    for r in report.source_lists:
        if r.similarity is not None:
            eid_login = f"{r.event_id}:{r.loginid}"
            sigs = signatures_for(r.event_id)
            sig = sigs.get(r.loginid) or sigs.get(r.loginid.split(".")[0])
            if sig:
                all_sig_sets.append(set(sig.keys()))
    if all_sig_sets:
        common = set.intersection(*all_sig_sets) if all_sig_sets else set()
        report.common_cards = sorted(common)

    evaluated = [r for r in report.source_lists if r.flag in ("match", "outlier")]
    if not evaluated:
        report.verdict = "dangerous"
    elif all(r.flag == "match" for r in evaluated):
        report.verdict = "safe"
    elif any(r.flag == "match" for r in evaluated):
        report.verdict = "partial"
    else:
        report.verdict = "dangerous"

    return report


def check_window_against_baseline(
    history_csv: Path,
    mtgo_json_cache_dir: Path,
    alias_check_config_csv: Path,
    format_name: str,
    canonical_name: str,
    window_start: date,
    window_end: date,
) -> AliasCheckReport:
    """Auto-hook variant of check_alias(): once an alias is live, its raw source name never
    appears as a Deck value again (ingestion resolves it to the canonical name immediately), so
    there is nothing left to compare "source lists" against post-merge. Instead this compares
    THIS WINDOW's lists filed under *canonical_name* against a reference library of
    *canonical_name* lists from BEFORE this window (its established identity) -- catching a build
    that drifted away from the merged deck's card core just as surely as a pre-merge check would,
    without needing the vanished raw label. See check_alias() for the pre-merge (manual, gate-
    before-adding-the-alias) use, which still needs the literal source name and is unaffected."""
    threshold = load_alias_check_threshold(alias_check_config_csv)
    report = AliasCheckReport(
        source_name=f"{canonical_name} (this window)",
        target_name=f"{canonical_name} (baseline, before {window_start.isoformat()})",
        window_start=window_start,
        window_end=window_end,
        threshold=threshold,
    )

    if not history_csv.exists():
        return report

    df = pd.read_csv(history_csv, dtype=str).fillna("")
    df["_date"] = pd.to_datetime(df["EventDate"], errors="coerce").dt.date
    fmt_norm = str(format_name or "").strip().lower()
    same_scope = df["Format"].astype(str).str.strip().str.lower().eq(fmt_norm) & df["_date"].notna() & (df["Deck"] == canonical_name)

    window_rows = df[same_scope & (df["_date"] >= window_start) & (df["_date"] <= window_end)]
    baseline_rows = df[same_scope & (df["_date"] < window_start)]
    report.target_lists_count = len(baseline_rows)

    if window_rows.empty or baseline_rows.empty:
        # Nothing new this window, or no pre-window baseline yet to compare against (e.g. the
        # week the alias first took effect) -- not an error, just nothing to check yet.
        return report

    event_cache: Dict[str, Dict[str, Dict[str, int]]] = {}

    def signatures_for(event_id: str) -> Dict[str, Dict[str, int]]:
        if event_id not in event_cache:
            event_cache[event_id] = _event_signatures(mtgo_json_cache_dir, event_id)
        return event_cache[event_id]

    baseline_signatures: Dict[str, Dict[str, int]] = {}
    for _, row in baseline_rows.iterrows():
        eid = _normalize_event_id(row.get("EventID"))
        sigs = signatures_for(eid)
        loginid = str(row.get("LoginID") or "").strip()
        sig = sigs.get(loginid) or sigs.get(loginid.split(".")[0])
        if sig:
            baseline_signatures[f"{eid}:{loginid}"] = sig

    if not baseline_signatures:
        return report

    similarities: List[float] = []
    for _, row in window_rows.iterrows():
        eid = _normalize_event_id(row.get("EventID"))
        sigs = signatures_for(eid)
        loginid = str(row.get("LoginID") or "").strip()
        sig = sigs.get(loginid) or sigs.get(loginid.split(".")[0])

        if not sig:
            report.source_lists.append(
                ListResult(
                    event_id=eid, event_date=str(row.get("EventDate") or ""), place=str(row.get("Place") or ""),
                    pilot=str(row.get("Pilot") or ""), loginid=loginid, similarity=None,
                    matched_against_event=None, flag="no_decklist_cached",
                )
            )
            continue

        best_sim = -1.0
        best_key = None
        for key, base_sig in baseline_signatures.items():
            sim = multiset_similarity(sig, base_sig)
            if sim > best_sim:
                best_sim, best_key = sim, key

        similarities.append(best_sim)
        flag = "match" if best_sim >= threshold else "outlier"
        report.source_lists.append(
            ListResult(
                event_id=eid, event_date=str(row.get("EventDate") or ""), place=str(row.get("Place") or ""),
                pilot=str(row.get("Pilot") or ""), loginid=loginid, similarity=round(best_sim, 4),
                matched_against_event=best_key.split(":")[0] if best_key else None, flag=flag,
            )
        )

    if similarities:
        s = sorted(similarities)
        n = len(s)
        report.median_similarity = round(s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2, 4)
        report.min_similarity = round(min(s), 4)
        report.max_similarity = round(max(s), 4)

    evaluated = [r for r in report.source_lists if r.flag in ("match", "outlier")]
    if not evaluated:
        report.verdict = "dangerous"
    elif all(r.flag == "match" for r in evaluated):
        report.verdict = "safe"
    elif any(r.flag == "match" for r in evaluated):
        report.verdict = "partial"
    else:
        report.verdict = "dangerous"

    return report


def format_report_text(report: AliasCheckReport) -> str:
    lines = []
    lines.append(
        f"check-alias: source='{report.source_name}' target='{report.target_name}' "
        f"window={report.window_start.isoformat()}..{report.window_end.isoformat()}"
    )
    lines.append(f"threshold (docs/alias_check_config.csv min_similarity) = {report.threshold}")
    lines.append(f"source lists: {len(report.source_lists)}  target lists: {report.target_lists_count}")
    if report.median_similarity is not None:
        lines.append(
            f"similarity: median={report.median_similarity} "
            f"min={report.min_similarity} max={report.max_similarity}"
        )
    lines.append(f"cards common to every compared list: {len(report.common_cards)}")
    if report.common_cards:
        lines.append("  " + ", ".join(report.common_cards))
    lines.append(report.scope_note)
    lines.append(f"VERDICT: {report.verdict.upper()}")
    if report.outliers:
        lines.append("outliers / not-cached lists:")
        for r in report.outliers:
            sim_text = "n/a (no cached decklist)" if r.similarity is None else f"{r.similarity:.3f}"
            lines.append(
                f"  EventID={r.event_id} Place={r.place} Pilot={r.pilot} similarity={sim_text} flag={r.flag}"
            )
    return "\n".join(lines)


def write_alias_report_csv(reports: List[AliasCheckReport], path: Path) -> Path:
    """Writes alias_report_<window>.csv: one row per OUTLIER (below-threshold or uncached) list
    across every alias checked. An empty (header-only) file means every list under every
    currently-active alias matched its target -- see CLAUDE.md point 5."""
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = ["source_name", "target_name", "event_id", "place", "pilot", "similarity", "flag"]
    rows = []
    for report in reports:
        for r in report.outliers:
            rows.append(
                {
                    "source_name": report.source_name,
                    "target_name": report.target_name,
                    "event_id": r.event_id,
                    "place": r.place,
                    "pilot": r.pilot,
                    "similarity": "" if r.similarity is None else f"{r.similarity:.4f}",
                    "flag": r.flag,
                }
            )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return path


def active_aliases(aliases_csv: Path) -> List[tuple[str, str]]:
    """True merges only: alias rows whose pattern differs from its canonical_name (a
    self-mapping is not a merge and has nothing to gate)."""
    if not aliases_csv.exists():
        return []
    out = []
    with aliases_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            pattern = str(row.get("pattern") or "").strip()
            canonical = str(row.get("canonical_name") or "").strip()
            if pattern and canonical and pattern.strip().lower() != canonical.strip().lower():
                out.append((pattern, canonical))
    return out


def run_alias_checks_for_window(
    history_csv: Path,
    mtgo_json_cache_dir: Path,
    aliases_csv: Path,
    alias_check_config_csv: Path,
    format_name: str,
    window_start: date,
    window_end: date,
    report_out_path: Path,
    log: Optional[Callable[[str], None]] = None,
) -> List[AliasCheckReport]:
    """Auto-run gate: checks every currently-active alias (see active_aliases) against this run's
    window and writes alias_report_<window>.csv -- always, even when empty (see CLAUDE.md point
    5's "lista poniżej progu nie ma być cicho wchłonięta")."""

    def emit(msg: str) -> None:
        if log is not None:
            log(msg)

    reports: List[AliasCheckReport] = []
    checked_baseline_for: set = set()
    for pattern, canonical in active_aliases(aliases_csv):
        # Prefer a literal source-vs-target check (covers an alias added this same run, before
        # any resync has rewritten the raw label away) -- fall back to the baseline-drift variant
        # for the steady state, where the raw source label no longer appears anywhere post-merge.
        report = check_alias(
            history_csv=history_csv,
            mtgo_json_cache_dir=mtgo_json_cache_dir,
            alias_check_config_csv=alias_check_config_csv,
            format_name=format_name,
            source_name=pattern,
            target_name=canonical,
            window_start=window_start,
            window_end=window_end,
        )
        if not report.source_lists:
            # Multiple alias patterns often share one canonical target (e.g. several raw Prowess
            # variants) -- the baseline-drift check depends only on the target, so run it once
            # per canonical name, not once per pattern that feeds it.
            if canonical in checked_baseline_for:
                continue
            checked_baseline_for.add(canonical)
            report = check_window_against_baseline(
                history_csv=history_csv,
                mtgo_json_cache_dir=mtgo_json_cache_dir,
                alias_check_config_csv=alias_check_config_csv,
                format_name=format_name,
                canonical_name=canonical,
                window_start=window_start,
                window_end=window_end,
            )

        if report.source_lists:
            reports.append(report)
            if report.outliers:
                emit(
                    f"[alias-check] '{pattern}' -> '{canonical}': {len(report.outliers)} "
                    f"outlier/uncached list(s) out of {len(report.source_lists)} (verdict={report.verdict})"
                )

    write_alias_report_csv(reports, report_out_path)
    emit(f"[alias-check] wrote {report_out_path}")
    return reports
