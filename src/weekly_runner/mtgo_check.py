"""External check of the week's events against the monthly pages of mtgo.com/decklists (runner step 5).

The parser is the runner's own (no DeckDB code). DeckDB's backfill-raw reads the same monthly pages, so the check
catches events missing from the import or the export, not events missing from mtgo.com itself."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .common import EXIT_MTGO, Stop, iso_label, months_between

MTGO = "https://www.mtgo.com/decklists/{y:04d}/{m:02d}"
HREF_RE = re.compile(r'href="/decklist/([A-Za-z0-9-]+)"')
LINK_RE = re.compile(r"^(?P<slug>[a-z0-9-]+?)-(?P<date>\d{4}-\d{2}-\d{2})(?P<id>\d+)$")
CHALLENGE_RE = re.compile(r"^modern-challenge-(?P<size>\d+)$")


@dataclass
class Listed:
    event_id: str
    date: str
    size: int


@dataclass
class Verification:
    rows: List[dict] = field(default_factory=list)     # one per event of the window (either source)
    before: Optional[dict] = None                       # last Challenge before the window
    after: Optional[dict] = None                        # first Challenge after the window
    warnings: List[str] = field(default_factory=list)
    months: List[str] = field(default_factory=list)


def fetch_month(y: int, m: int, today: date, get: Callable = None, pauses: tuple = (15, 30, 60),
                sleep: Callable = time.sleep) -> tuple[str, List[Listed]]:
    """HTML and Challenges of one monthly page. mtgo.com now and then serves a cut page (title, no decklist links),
    so an HTTP error or a page parse_month refuses is tried again after each pause; the reason of the last attempt
    is the Stop."""
    import requests

    get = get or (lambda url: requests.get(url, timeout=60))
    url = MTGO.format(y=y, m=m)
    for k in range(len(pauses) + 1):
        try:
            r = get(url)
            if r.status_code != 200:
                raise Stop(EXIT_MTGO, f"HTTP {r.status_code}")
            return r.text, parse_month(r.text, y, m, today)
        except requests.RequestException as e:
            last = f"brak sieci: {type(e).__name__}: {e}"
        except Stop as e:
            last = e.message
        if k < len(pauses):
            print(f"   mtgo.com {y:04d}-{m:02d}: {last.splitlines()[0]}; ponowna próba za {pauses[k]} s", flush=True)
            sleep(pauses[k])
    raise Stop(EXIT_MTGO, f"mtgo.com: {url} po {len(pauses) + 1} próbach: {last}")


def parse_month(html: str, y: int, m: int, today: date) -> List[Listed]:
    """Every Modern Challenge of the page. A page without decklist links, with links that do not follow the
    '<slug>-<YYYY-MM-DD><id>' pattern, or ending before the last day it must show = changed structure: Stop."""
    key = f"{y:04d}-{m:02d}"
    links = HREF_RE.findall(html)
    if not links:
        raise Stop(EXIT_MTGO, f"mtgo.com {key}: brak linków /decklist/ na stronie (zmiana struktury strony?)")
    parsed = [LINK_RE.match(s) for s in links]
    bad = [s for s, p in zip(links, parsed) if p is None]
    if bad:
        raise Stop(EXIT_MTGO, f"mtgo.com {key}: {len(bad)} linków poza wzorcem <slug>-<RRRR-MM-DD><id>, np. {bad[:3]} "
                              "(zmiana struktury strony?)")
    in_month = sorted(p["date"] for p in parsed if p["date"].startswith(key))
    month_end = (date(y, m, 1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    must_reach = min(month_end, today - timedelta(days=1))
    if not in_month or in_month[-1] < must_reach.isoformat():
        raise Stop(EXIT_MTGO, f"mtgo.com {key}: strona kończy się na {in_month[-1] if in_month else 'braku dat'}, "
                              f"oczekiwano co najmniej {must_reach} (strona niepełna?)")
    out = {}
    for p in parsed:
        c = CHALLENGE_RE.match(p["slug"])
        if c and p["date"].startswith(key):
            out[p["id"]] = Listed(p["id"], p["date"], int(c["size"]))
    if not out:
        raise Stop(EXIT_MTGO, f"mtgo.com {key}: brak Modern Challenge na stronie (zmiana struktury strony?)")
    return list(out.values())


def fetch_listings(start: date, end: date, snap_dir: Path, today: date, get: Callable = None) -> tuple[
        Dict[str, Listed], List[str]]:
    """Pages of every month of [start - 1 day, end + 1 day] (both months at a month boundary), saved as
    mtgo_snapshot_<YYYY-MM>.html; the Challenges of all of them by event id."""
    listed, months = {}, []
    for y, m in months_between(start - timedelta(days=1), end + timedelta(days=1)):
        if date(y, m, 1) > today:
            continue
        html, events = fetch_month(y, m, today, get)
        snap_dir.mkdir(parents=True, exist_ok=True)
        (snap_dir / f"mtgo_snapshot_{y:04d}-{m:02d}.html").write_text(html, encoding="utf-8")
        months.append(f"{y:04d}-{m:02d}")
        for e in events:
            listed[e.event_id] = e
    return listed, months


def verify(start: date, end: date, listed: Dict[str, Listed], export_events: Dict[str, str]) -> Verification:
    """Compares the ids of the window both ways. *export_events*: event_id -> event_date of the whole export.
    Stop on any id on one side only; a date one day off is a warning naming the week the event went to; more = Stop."""
    lo, hi = start.isoformat(), end.isoformat()
    v = Verification()
    in_window_mtgo = {i for i, e in listed.items() if lo <= e.date <= hi}
    in_window_file = {i for i, d in export_events.items() if lo <= d <= hi}
    missing, extra, far = [], [], []
    for i in sorted(in_window_mtgo | in_window_file, key=lambda i: (listed[i].date if i in listed
                                                                     else export_events[i], i)):
        e, fd = listed.get(i), export_events.get(i)
        row = {"event_id": i, "in_file": i in in_window_file, "on_mtgo": e is not None,
               "date_file": fd, "date_mtgo": e.date if e else None, "size": e.size if e else None}
        v.rows.append(row)
        if e is None:
            extra.append(i)
            continue
        if fd is None:
            missing.append(i)
            continue
        diff = abs((date.fromisoformat(fd) - date.fromisoformat(e.date)).days)
        if diff > 1:
            far.append(f"{i} (plik {fd}, mtgo.com {e.date})")
        elif diff == 1:
            v.warnings.append(f"event {i}: data DeckDB {fd}, mtgo.com {e.date}; w eksporcie należy do tygodnia "
                              f"{iso_label(date.fromisoformat(fd))}"
                              + ("" if lo <= fd <= hi else " (poza tym tygodniem)"))
    errors = []
    if missing:
        errors.append(f"na mtgo.com, brak w eksporcie: {', '.join(missing)}")
    if extra:
        errors.append(f"w eksporcie, brak na mtgo.com: {', '.join(extra)}")
    if far:
        errors.append(f"data różna o więcej niż 1 dzień: {', '.join(far)}")
    if errors:
        raise Stop(EXIT_MTGO, f"weryfikacja mtgo.com {lo} .. {hi}: " + "; ".join(errors))
    # neighbours: outside the window on both sides (an event one day off and counted in the window is a row above)
    order = sorted((e for e in listed.values() if e.event_id not in in_window_file),
                   key=lambda e: (e.date, int(e.event_id)))
    before = [e for e in order if e.date < lo]
    after = [e for e in order if e.date > hi]
    for name, e in (("before", before[-1] if before else None), ("after", after[0] if after else None)):
        if e is not None:
            fd = export_events.get(e.event_id)
            setattr(v, name, {"event_id": e.event_id, "date_mtgo": e.date, "size": e.size, "date_file": fd,
                              "outside": not (lo <= e.date <= hi) and not (fd and lo <= fd <= hi)})
    return v
