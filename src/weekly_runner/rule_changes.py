"""DeckDB rule changes of one month for the monthly package (notes.md, section "DeckDB rule changes this month").

Source: DeckDB docs/decisions.md (main table: number, decision date, decision; table "Przedmioty wpisów": subject per
entry) and the git history of DeckDB rules/registry.yaml. An entry is listed when

  - its decision date is inside the month,
  - its subject names a deck (``deck:<id>`` / ``decks:<id>``) or the deck priorities (``priorities:...``), and
  - the DeckDB commit that added the entry to docs/decisions.md changed that deck in rules/registry.yaml (new deck,
    name, archetype, signature = every definition key except name / archetype / variants / sources / decisions /
    traits, variants) or changed the deck priorities.

Decided by the data, not by the wording: a rejected signature, a "variant D" (signature unchanged), a queue item or a
freeze is not listed, because the registry did not change for that deck in that commit. No player names: the
registry and the subjects carry deck ids and deck names only. Every error is a Stop (no fallback)."""
from __future__ import annotations

import csv
import re
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from .common import EXIT_MONTHLY, Stop, git_out

DECISIONS_REL = "docs/decisions.md"
REGISTRY_REL = "rules/registry.yaml"
NOT_SIGNATURE = ("id", "name", "archetype", "variants", "sources", "decisions", "traits", "description", "notes")
COLUMNS = ("entries", "date", "deck_ids", "decks", "change")
MAIN_ROW = re.compile(r"^\| (\d+) \| (\d{4}-\d{2}-\d{2}) \| ")
SUBJECT_ROW = re.compile(r"^\| wpis (\d+) \| ([^|]*?) \| ")


def parse_decisions(text: str) -> tuple[Dict[int, str], Dict[int, List[str]]]:
    """(entry -> decision date, entry -> subject items) from docs/decisions.md."""
    dates: Dict[int, str] = {}
    subjects: Dict[int, List[str]] = {}
    for line in text.splitlines():
        m = MAIN_ROW.match(line)
        if m:
            dates[int(m.group(1))] = m.group(2)
            continue
        m = SUBJECT_ROW.match(line)
        if m:
            subjects[int(m.group(1))] = [s.strip() for s in m.group(2).split(",") if s.strip()]
    return dates, subjects


def _decks(reg: dict) -> Dict[str, dict]:
    return {str(d["id"]): d for d in (reg.get("decks") or [])}


def _pairs(reg: dict) -> Dict[tuple, str]:
    return {(str(p["higher"]), str(p["lower"])): str(p.get("decision", "")) for p in (reg.get("priorities") or [])}


def deck_changes(before: dict, after: dict, deck_id: str) -> List[str]:
    """What changed for one deck between two registry states (empty: nothing)."""
    b, a = _decks(before).get(deck_id), _decks(after).get(deck_id)
    if a is None and b is None:
        return []
    if b is None:
        return [f"new deck {a['name']} ({a.get('archetype', '')})"]
    if a is None:
        return [f"deck {b['name']} removed"]
    out = []
    if a["name"] != b["name"]:
        out.append(f"renamed {b['name']} -> {a['name']}")
    if a.get("archetype") != b.get("archetype"):
        out.append(f"archetype {b.get('archetype')} -> {a.get('archetype')}")
    sig = lambda d: {k: v for k, v in d.items() if k not in NOT_SIGNATURE}  # noqa: E731
    if sig(a) != sig(b):
        out.append("signature changed")
    if a.get("variants") != b.get("variants"):
        n_b = len((b.get("variants") or {}).get("items") or [])
        n_a = len((a.get("variants") or {}).get("items") or [])
        out.append(f"variants changed ({n_b} -> {n_a} variants)" if n_b != n_a else "variants changed")
    return out


def priority_changes(before: dict, after: dict, entry: int) -> tuple[List[str], List[str]]:
    """(texts, deck ids) of the priority pairs added or removed between two registry states; when some changed pairs
    carry ``decision: wpis <entry>``, only those (a commit may record several entries)."""
    pb, pa = _pairs(before), _pairs(after)
    added = [p for p in pa if p not in pb]
    removed = [p for p in pb if p not in pa]
    own = [p for p in added if pa[p] == f"wpis {entry}"]
    if own:
        added, removed = own, []
    names = {**{k: v["name"] for k, v in _decks(before).items()}, **{k: v["name"] for k, v in _decks(after).items()}}
    texts = ([f"priority {names.get(h, h)} over {names.get(lo, lo)} added" for h, lo in added]
             + [f"priority {names.get(h, h)} over {names.get(lo, lo)} removed" for h, lo in removed])
    ids = sorted({x for p in added + removed for x in p})
    return texts, ids


class _Repo:
    """git reads of the DeckDB repo, cached per commit."""

    def __init__(self, deckdb_dir: Path):
        self.dir = deckdb_dir
        self._reg: Dict[str, dict] = {}

    def commit_of(self, entry: int, day: str) -> str:
        out = git_out(self.dir, "log", "--format=%H", "--reverse", "-S", f"| {entry} | {day} | ", "--",
                      DECISIONS_REL).split()
        if not out:
            raise Stop(EXIT_MONTHLY, f"DeckDB: nie znaleziono commitu, który dodał wpis {entry} ({day}) do {DECISIONS_REL}")
        return out[0]

    def registry(self, rev: str) -> dict:
        if rev not in self._reg:
            try:
                text = git_out(self.dir, "show", f"{rev}:{REGISTRY_REL}")
            except Stop:
                text = ""  # the registry did not exist yet at that revision
            self._reg[rev] = (yaml.safe_load(text) or {}) if text else {}
        return self._reg[rev]


def month_rule_changes(deckdb_dir: Path, ms: date, me: date) -> List[dict]:
    """Rows (COLUMNS) of the deck-changing DeckDB entries decided in ms .. me, by entry number. Consecutive entries of
    the same commit with the same decks and the same change (an apply-decisions batch) are one row ('48-52')."""
    path = deckdb_dir / DECISIONS_REL
    if not path.exists():
        raise Stop(EXIT_MONTHLY, f"brak {path}")
    dates, subjects = parse_decisions(path.read_text(encoding="utf-8"))
    repo = _Repo(deckdb_dir)
    rows = []
    for entry in sorted(dates):
        day = dates[entry]
        if not ms.isoformat() <= day <= me.isoformat():
            continue
        items = subjects.get(entry, [])
        deck_ids = [s.split(":", 1)[1] for s in items if s.startswith(("deck:", "decks:"))]
        prio = any(s.startswith("priorities:") for s in items)
        if not deck_ids and not prio:
            continue
        commit = repo.commit_of(entry, day)
        before, after = repo.registry(f"{commit}^"), repo.registry(commit)
        texts: List[str] = []
        ids: List[str] = []
        for d in deck_ids:
            ch = deck_changes(before, after, d)
            if ch:
                texts += ch
                ids.append(d)
        if prio:
            pt, pids = priority_changes(before, after, entry)
            texts += pt
            ids += [i for i in pids if i not in ids]
        if not texts:
            continue
        names = {**{k: v["name"] for k, v in _decks(before).items()}, **{k: v["name"] for k, v in _decks(after).items()}}
        row = {"entries": [entry], "date": day, "deck_ids": ";".join(ids),
               "decks": ";".join(names.get(i, i) for i in ids), "change": "; ".join(texts), "_commit": commit}
        last = rows[-1] if rows else None
        if last and all(last[k] == row[k] for k in ("_commit", "date", "deck_ids", "change")):
            last["entries"].append(entry)  # one commit, one deck, one change: the entries cannot be told apart
        else:
            rows.append(row)
    for r in rows:
        r["entries"] = entry_label(r.pop("entries"))
        r.pop("_commit")
    return rows


def entry_label(nums: List[int]) -> str:
    """[48, 49, 50, 52] -> '48-50, 52'."""
    out, start, prev = [], nums[0], nums[0]
    for n in nums[1:] + [None]:
        if n is not None and n == prev + 1:
            prev = n
            continue
        out.append(str(start) if start == prev else f"{start}-{prev}")
        if n is not None:
            start = prev = n
    return ", ".join(out)


def write_csv(rows: List[dict], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return path

