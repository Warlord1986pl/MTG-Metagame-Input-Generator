"""Retroactive application of the TEMPORARY Broodscale card rule (see broodscale_rule.py) and of
the Challenge-based Meta (see challenge_meta.py).

    # 1. relabel challenge/premier history from the cached mtgo.com JSON -- dry-run by default
    python src/broodscale_rule_cli.py apply [--apply]

    # 2. after removing the "Eldrazi" -> Broodscale Combo name alias from docs/*.csv: relabel the
    #    entries that alias had put into Broodscale Combo without a Basking Broodscale in the main,
    #    the way the normal pipeline labels any list -- MTGGoldfish's submitted name through the
    #    current mappings/aliases, the nearest-neighbor classifier where MTGGoldfish has no row
    #    (collision events). Dry-run by default; needs network (MTGGoldfish pages).
    python src/broodscale_rule_cli.py reverse-alias [--apply]

    # 3. rebuild every already-generated run dir whose window ends on/after --since: Challenge-
    #    based Meta input, challenge_statistics.xlsx + PNGs + finalists, broodscale_override log,
    #    premier_events, per-event decklist/vs_metagame csvs, and statistics/metagame (deck_analysis*,
    #    record_probabilities, encounter + trend PNGs) from the Challenge-based Meta.
    python src/broodscale_rule_cli.py recompute --since 2026-07-13 --trend-weeks 12

Steps 1 and 3 never re-fetch a decklist (cached JSON only); step 3 touches the network only for
verify_registry_against_history's registry-index fetch.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from broodscale_rule import (  # noqa: E402
    BROODSCALE_CARD,
    BROODSCALE_DECK,
    _norm_id,
    append_reverse_ledger,
    apply_broodscale_rule_to_history,
    ledger_path_for,
    reverse_ledger_path_for,
    write_broodscale_override_log,
)
from challenge_history_engine import backup_history_file, run_challenge_statistics  # noqa: E402
from challenge_meta import (  # noqa: E402
    TREND_CAPTION,
    build_challenge_meta_input,
    challenge_history_path_for,
    week_index_for,
)
from challenge_mtgo_source import (  # noqa: E402
    NEEDS_MANUAL_REVIEW,
    MtgoEventDeck,
    classify_deck,
    parse_mtgo_event,
    refresh_premier_events_csv,
    verify_registry_against_history,
)
from metagame_input_generator import (  # noqa: E402
    _format_slug_for_url,
    _parse_single_challenge,
    _safe_to_csv,
    build_challenge_vs_metagame_report,
    canonicalize_deck_name,
    classify_archetype,
    load_aliases,
    load_rules,
    load_user_deck_mappings,
    mapping_lookup,
    normalize_name,
    scan_run_dirs,
)
from statistics_engine import run_statistics  # noqa: E402


def _paths(outputs_base: Path, format_name: str):
    slug = _format_slug_for_url(format_name)
    return (
        outputs_base / f"challenge_history_{slug}.csv",
        outputs_base / f"premier_history_{slug}.csv",
        outputs_base / "cache" / "mtgo_json" / slug,
        ledger_path_for(outputs_base, slug),
    )


def _read_hist(path: Path) -> pd.DataFrame:
    hist = pd.read_csv(path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    hist["EventID"] = hist["EventID"].map(_norm_id)
    return hist


def cmd_apply(args: argparse.Namespace) -> int:
    outputs_base = Path(args.outputs_base)
    challenge_csv, premier_csv, cache_dir, ledger_csv = _paths(outputs_base, args.format_name)
    for kind, path in (("challenge", challenge_csv), ("premier", premier_csv)):
        added = apply_broodscale_rule_to_history(
            path, cache_dir, ledger_csv, kind, dry_run=not args.apply, log=print
        )
        if not added.empty:
            moved = added[added["SubmittedDeck"] != BROODSCALE_DECK]
            print(f"  {kind}: {len(added)} entry(ies) with Basking Broodscale, {len(moved)} from another name:")
            print(moved.groupby("SubmittedDeck").size().to_string() if not moved.empty else "    (none)")
    if not args.apply:
        print("DRY-RUN: nothing written. Re-run with --apply.")
    return 0


def _load_mappings(args: argparse.Namespace):
    """Same Challenge mapping merge as run_generation: user mapping first, challenge mapping only
    adds raw names the user mapping doesn't already have."""
    base = load_user_deck_mappings(Path(args.user_mapping_file))
    known = {normalize_name(m.raw_name) for m in base if normalize_name(m.raw_name)}
    for m in load_user_deck_mappings(Path(args.challenge_mapping_file)):
        key = normalize_name(m.raw_name)
        if key and key not in known:
            base.append(m)
            known.add(key)
    return mapping_lookup(base)


def cmd_reverse_alias(args: argparse.Namespace) -> int:
    outputs_base = Path(args.outputs_base)
    challenge_csv, premier_csv, cache_dir, _ledger = _paths(outputs_base, args.format_name)
    reverse_ledger = reverse_ledger_path_for(outputs_base, _format_slug_for_url(args.format_name))
    aliases = load_aliases(Path(args.aliases_file))
    rules = load_rules(Path(args.rules_file))
    mapping_by_raw = _load_mappings(args)

    def canonical(raw: str) -> Tuple[str, str]:
        m = mapping_by_raw.get(normalize_name(raw))
        deck = m.canonical_name if m is not None and m.canonical_name else canonicalize_deck_name(raw, aliases)
        arch = m.archetype if m is not None and m.archetype else classify_archetype(deck, rules)
        return deck, arch

    if canonical(args.alias_name)[0] == BROODSCALE_DECK:
        print(f"REFUSED: {args.alias_name!r} still resolves to {BROODSCALE_DECK!r} through docs/*.csv -- "
              "remove that alias/mapping first (with the GUI closed).")
        return 2

    hists = {"challenge": _read_hist(challenge_csv), "premier": _read_hist(premier_csv)}
    decks: Dict[Tuple[str, str], MtgoEventDeck] = {}
    for hist in hists.values():
        for eid in hist.loc[hist["EventID"] != "", "EventID"].unique():
            path = cache_dir / f"{eid}.json"
            if path.exists():
                for d in parse_mtgo_event(json.loads(path.read_text(encoding="utf-8"))):
                    decks[(eid, _norm_id(d.loginid))] = d

    targets: List[Tuple[str, int]] = []
    for kind, hist in hists.items():
        for idx, r in hist.iterrows():
            d = decks.get((r["EventID"], _norm_id(r["LoginID"])))
            if r["Deck"] == BROODSCALE_DECK and d is not None and d.signature.get(BROODSCALE_CARD, 0) == 0:
                targets.append((kind, idx))
    print(f"{len(targets)} {BROODSCALE_DECK!r} entry(ies) without {BROODSCALE_CARD!r} in the main")

    # Pass 1: MTGGoldfish's submitted name (premier events have no MTGGoldfish page).
    goldfish: Dict[Tuple[str, int], object] = {}
    decisions: Dict[Tuple[str, int], dict] = {}
    for kind, idx in targets:
        r = hists[kind].loc[idx]
        if kind != "challenge":
            continue
        key = (r["EventDate"], int(r["Tier"]))
        if key not in goldfish:
            try:
                _, goldfish[key] = _parse_single_challenge(
                    {"challenge_size": key[1], "event_date": key[0], "mtgo_url": ""}, args.format_name
                )
            except Exception as exc:
                goldfish[key] = exc
        roster = goldfish[key]
        if isinstance(roster, Exception):
            continue
        hit = roster[roster["Pilot"].map(normalize_name) == normalize_name(r["Pilot"])]
        if not hit.empty:
            raw = str(hit.iloc[0]["Deck"])
            deck, arch = canonical(raw)
            decisions[(kind, idx)] = {"NewDeck": deck, "Archetype": arch, "Method": "mtggoldfish_name", "SubmittedName": raw, "Similarity": ""}

    # Pass 2: classifier for the rest, against every other labeled list in history -- including
    # the ones pass 1 just relabeled, never the still-unresolved targets themselves.
    unresolved = {t for t in targets if t not in decisions}
    library = []
    for kind, hist in hists.items():
        for idx, r in hist.iterrows():
            if (kind, idx) in unresolved or r["Deck"] == NEEDS_MANUAL_REVIEW:
                continue
            d = decks.get((r["EventID"], _norm_id(r["LoginID"])))
            if d is not None:
                label = decisions[(kind, idx)]["NewDeck"] if (kind, idx) in decisions else r["Deck"]
                library.append((label, d.signature))
    for kind, idx in sorted(unresolved):
        r = hists[kind].loc[idx]
        res = classify_deck(decks[(r["EventID"], _norm_id(r["LoginID"]))], library)
        if res.needs_review:
            decisions[(kind, idx)] = {"NewDeck": NEEDS_MANUAL_REVIEW, "Archetype": NEEDS_MANUAL_REVIEW,
                                      "Method": f"classifier_review: {res.reason}", "SubmittedName": "",
                                      "Similarity": f"{res.nearest_sim:.3f}", "DeckGuess": res.nearest_label or ""}
        else:
            deck, arch = canonical(res.predicted_label)
            decisions[(kind, idx)] = {"NewDeck": deck, "Archetype": arch, "Method": "classifier",
                                      "SubmittedName": "", "Similarity": f"{res.nearest_sim:.3f}"}

    ledger_rows = []
    for (kind, idx), dec in sorted(decisions.items()):
        r = hists[kind].loc[idx]
        print(f"  {kind} {r['EventDate']} {r['EventID']} T{r['Tier']} #{r['Place']} {r['Pilot']}: "
              f"{r['Deck']} -> {dec['NewDeck']} [{dec['Method']}{', sim ' + dec['Similarity'] if dec['Similarity'] else ''}"
              f"{', submitted ' + repr(dec['SubmittedName']) if dec['SubmittedName'] else ''}]")
        ledger_rows.append({
            "Kind": kind, "EventID": r["EventID"], "EventDate": r["EventDate"], "Tier": r["Tier"],
            "Pilot": r["Pilot"], "LoginID": _norm_id(r["LoginID"]), "Place": r["Place"],
            "PreviousDeck": r["Deck"], "NewDeck": dec["NewDeck"], "Method": dec["Method"],
            "SubmittedName": dec["SubmittedName"], "Similarity": dec["Similarity"],
            "RecordedOn": date.today().isoformat(),
        })
    if not args.apply:
        print("DRY-RUN: nothing written. Re-run with --apply.")
        return 0

    for kind, path in (("challenge", challenge_csv), ("premier", premier_csv)):
        mine = {idx: dec for (k, idx), dec in decisions.items() if k == kind}
        if not mine:
            continue
        hist = hists[kind]
        for idx, dec in mine.items():
            hist.at[idx, "Deck"] = dec["NewDeck"]
            hist.at[idx, "Archetype"] = dec["Archetype"]
            hist.at[idx, "DeckGuess"] = dec.get("DeckGuess", "")
        backup_history_file(path, log=print)
        hist.to_csv(path, index=False, encoding="utf-8-sig")
        print(f"[reverse-alias] {len(mine)} row(s) written -> {path}")
    append_reverse_ledger(reverse_ledger, ledger_rows)
    print(f"[reverse-alias] ledger -> {reverse_ledger}")
    return 0


def _refresh_event_csvs(run_dir: Path, hist: pd.DataFrame, metagame_df: pd.DataFrame, aliases, rules) -> int:
    """Rewrite Deck/Archetype of challenge_C<tier>_<date>_<eventid>_decklist.csv (+ its
    *_vs_metagame.csv) from history. Place/Pilot stay exactly as they are in the file."""
    changed = 0
    for path in sorted(run_dir.glob("challenge_C*_*_*_decklist.csv")):
        event_id = path.stem.split("_")[-2]
        ev = hist[hist["EventID"] == event_id]
        if ev.empty:
            continue
        by_place = {str(r.Place): (r.Deck, r.Archetype) for r in ev.itertuples(index=False)}
        df = pd.read_csv(path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
        new = df.copy()
        for i, r in df.iterrows():
            deck_arch = by_place.get(str(r["Place"]))
            if deck_arch is not None:
                new.at[i, "Deck"], new.at[i, "Archetype"] = deck_arch
        if new[["Deck", "Archetype"]].equals(df[["Deck", "Archetype"]]):
            continue
        new.to_csv(path, index=False, encoding="utf-8-sig")
        new["Place"] = pd.to_numeric(new["Place"], errors="coerce")
        cmp_df = build_challenge_vs_metagame_report(new, metagame_df, aliases, rules)
        _safe_to_csv(cmp_df, path.with_name(path.name.replace("_decklist.csv", "_vs_metagame.csv")))
        changed += 1
    return changed


def cmd_recompute(args: argparse.Namespace) -> int:
    outputs_base = Path(args.outputs_base)
    slug = _format_slug_for_url(args.format_name)
    challenge_csv, premier_csv, cache_dir, ledger_csv = _paths(outputs_base, args.format_name)
    reverse_ledger = reverse_ledger_path_for(outputs_base, slug)
    meta_history = challenge_history_path_for(outputs_base, slug)
    videre_history = outputs_base / f"metagame_history_{slug}.csv"
    since = date.fromisoformat(args.since)
    aliases = load_aliases(Path(args.aliases_file))
    rules = load_rules(Path(args.rules_file))
    hist = _read_hist(challenge_csv)

    failures = []
    for start, end, run_dir in scan_run_dirs(outputs_base):
        if start < since:
            continue
        label = f"{start.isoformat()}_to_{end.isoformat()}"
        print(f"\n=== {run_dir.name} ({label}) ===")
        stats_dir = run_dir / "statistics" / "challenges"

        meta_xlsx = build_challenge_meta_input(
            run_dir, challenge_csv, ledger_csv, start, end, args.format_name, args.rogue_threshold, log=print
        )
        completeness = verify_registry_against_history(
            format_name=args.format_name, start_date=start, end_date=end,
            history_csv=challenge_csv, log=print, cache_dir=cache_dir,
        )
        premier_ids = {str(eid) for eid, _t, _d in (completeness.premier_events or [])}
        result = run_challenge_statistics(
            history_csv=challenge_csv, output_dir=stats_dir, format_name=args.format_name,
            week_start=start, week_end=end, log=print, premier_event_ids=premier_ids,
            completeness=completeness,
        )
        if not result.complete or not result.excel_path.exists():
            failures.append(f"{run_dir.name}: challenge_statistics NOT written (complete={result.complete})")
        write_broodscale_override_log(
            stats_dir / f"broodscale_override_{label}.csv", challenge_csv, premier_csv, cache_dir,
            ledger_csv, start, end, log=print, reverse_ledger_csv=reverse_ledger,
        )
        refresh_premier_events_csv(run_dir / f"premier_events_{label}.csv", premier_csv, log=print)
        metagame_csv = run_dir / "metagame_input.csv"
        if metagame_csv.exists():
            n = _refresh_event_csvs(run_dir, hist, pd.read_csv(metagame_csv), aliases, rules)
            print(f"[broodscale-rule] {n} per-event decklist/vs_metagame csv pair(s) rewritten in {run_dir.name}")

        run_statistics(
            input_excel=meta_xlsx,
            output_dir=run_dir / "statistics" / "metagame",
            history_csv=meta_history,
            weeks_back=args.trend_weeks,
            log=print,
            week_index=week_index_for(meta_history, videre_history, start, end),
            trend_caption=TREND_CAPTION,
        )

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--outputs-base", default="outputs")
    p.add_argument("--format", dest="format_name", default="Modern")
    p.add_argument("--aliases-file", default="docs/deck_aliases.csv")
    p.add_argument("--rules-file", default="docs/archetype_rules.csv")
    p.add_argument("--user-mapping-file", default="docs/user_deck_mapping.csv")
    p.add_argument("--challenge-mapping-file", default="docs/challenge_deck_mapping.csv")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("apply")
    a.add_argument("--apply", action="store_true", help="write history + ledger (default: dry-run)")
    rv = sub.add_parser("reverse-alias")
    rv.add_argument("--alias-name", default="Eldrazi", help="the removed raw name (default: Eldrazi)")
    rv.add_argument("--apply", action="store_true", help="write history + reverse ledger (default: dry-run)")
    r = sub.add_parser("recompute")
    r.add_argument("--since", required=True, help="YYYY-MM-DD: rebuild run dirs whose window starts on/after this")
    r.add_argument("--trend-weeks", type=int, default=12)
    r.add_argument("--rogue-threshold", type=float, default=0.5)
    args = p.parse_args(argv)
    return {"apply": cmd_apply, "reverse-alias": cmd_reverse_alias, "recompute": cmd_recompute}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
