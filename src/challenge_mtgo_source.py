"""mtgo.com-first Challenge event registry, decklist fetch, and Deck-label classifier.

Source of truth for "what tournaments happened" is https://www.mtgo.com/decklists -- event_id
(the numeric suffix on a /decklist/... URL) is the only valid event identity / dedup key.
MTGGoldfish is used only to recover human-assigned Deck labels via an (event, pilot) join.
Where MTGGoldfish's date+size URL cannot disambiguate two same-day/same-tier tournaments (a
confirmed, permanent limitation of that site), a maindeck-signature nearest-neighbor classifier
fills the gap for the unresolved event; anything the classifier cannot confidently label is
surfaced as NEEDS_MANUAL_REVIEW rather than guessed.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd

try:
    from metagame_input_generator import (
        _fetch_text,
        MTGO_BASE,
        _format_slug_for_url,
        normalize_name,
        canonicalize_deck_name,
        classify_archetype,
        _parse_single_challenge,
        mapping_lookup,
        DeckAliasRule,
        ArchetypeRule,
        UserDeckMapping,
    )
except ModuleNotFoundError:
    from .metagame_input_generator import (
        _fetch_text,
        MTGO_BASE,
        _format_slug_for_url,
        normalize_name,
        canonicalize_deck_name,
        classify_archetype,
        _parse_single_challenge,
        mapping_lookup,
        DeckAliasRule,
        ArchetypeRule,
        UserDeckMapping,
    )

BASIC_LAND_NAMES = {"plains", "island", "swamp", "mountain", "forest", "wastes"}

PREMIER_SUFFIXES = {
    "showcase-challenge",
    "showcase-qualifier",
    "rc-super-qualifier",
    "super-qualifier",
    "qualifier",
    "champions-showcase",
}
IGNORED_SUFFIXES = {"league", "last-chance", "preliminary"}

NEEDS_MANUAL_REVIEW = "NEEDS_MANUAL_REVIEW"


class ChallengeSourceError(RuntimeError):
    """Raised for conditions the pipeline must stop and report, not work around."""


class ChallengePendingError(ChallengeSourceError):
    """Raised when mtgo.com has posted the bracket (final_rank) for an event but not yet the
    decklists themselves -- a timing condition, not malformed data. Caught per-event so it never
    aborts the rest of the registry; the event is retried on a later run.
    """


def _fetch_text_retry(url: str, timeout: int = 45, attempts: int = 5, log: Optional[Callable[[str], None]] = None) -> str:
    last_exc: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            return _fetch_text(url, timeout=timeout)
        except Exception as exc:
            last_exc = exc
            if log:
                log(f"[mtgo-fetch] retry {attempt + 1}/{attempts} for {url}: {exc}")
            time.sleep(2 * (attempt + 1))
    raise ChallengeSourceError(f"Failed to fetch {url} after {attempts} attempts: {last_exc}")


@dataclass
class MtgoRegistryEvent:
    event_id: str
    date: str
    slug: str
    kind: str  # "challenge" | "premier" | "ignored"
    size: Optional[int]
    url: str


def _months_between(start_date: date, end_date: date) -> List[Tuple[int, int]]:
    """Every (year, month) touched by the closed [start_date, end_date] window, inclusive."""
    months: List[Tuple[int, int]] = []
    y, m = start_date.year, start_date.month
    while (y, m) <= (end_date.year, end_date.month):
        months.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return months


_DECKLIST_HREF_PROBE_RE = re.compile(r'href="/decklist/', re.IGNORECASE)
_DECKLIST_DATE_RE = re.compile(r'href="/decklist/[a-z0-9-]+-(\d{4}-\d{2}-\d{2})\d+"', re.IGNORECASE)

_DEFAULT_MONTH_LINK_COUNT_CACHE = Path(__file__).resolve().parents[1] / "outputs" / "cache" / "mtgo_month_link_counts.json"


def _last_day_of_month(year: int, month: int) -> date:
    next_first = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return next_first - timedelta(days=1)


def _max_decklist_date_on_page(html: str) -> Optional[date]:
    best: Optional[date] = None
    for date_str in _DECKLIST_DATE_RE.findall(html):
        try:
            d = date.fromisoformat(date_str)
        except ValueError:
            continue
        if best is None or d > best:
            best = d
    return best


def _load_month_link_count_cache(path: Path) -> Dict[str, int]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_month_link_count_cache(path: Path, cache: Dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")


def _fetch_month_page_with_content_check(
    url: str,
    year: int,
    month: int,
    log: Optional[Callable[[str], None]] = None,
    count_cache_path: Optional[Path] = None,
    today: Optional[date] = None,
) -> str:
    """mtgo.com's decklists index has been observed live to intermittently return a truncated
    response instead of the real listing -- a genuinely successful fetch by every network-level
    signal (_fetch_text_retry sees no exception), yet the content is silently wrong. Two
    independent, cumulative checks guard against this for any past/current month (a future month
    is accepted as-is on the first try -- it legitimately has zero events and there is nothing yet
    to compare against):

    1. Zero decklist links at all. MTGO runs Leagues daily, so a month that has already started
       can never legitimately have zero /decklist/ links.

    2. The page's latest decklist date falls short of that month's actual coverage: for a fully
       past month, short of that month's last calendar day; for the current month, short of
       yesterday. This catches a page that returns SOME links but stops partway through the
       month -- a suffix of the window silently missing, not the whole month -- which check #1
       alone cannot see.

    A third, cross-run check applies only to a fully closed past month: the raw decklist-link
    count for each (year, month) is persisted to a small JSON cache (count_cache_path, default
    outputs/cache/mtgo_month_link_counts.json). Past months are immutable, so a re-fetch returning
    FEWER links than a previously recorded count for that month can never be a legitimate content
    change -- it is always a fetch artifact -- and raises ChallengeSourceError immediately, even
    though checks #1/#2 above may have both passed.

    Byte length and parsed link count are logged on every attempt (success or not) so a future
    incident is diagnosable from logs alone.
    """
    cache_path = count_cache_path or _DEFAULT_MONTH_LINK_COUNT_CACHE
    today = today or date.today()
    is_past_or_current = (year, month) <= (today.year, today.month)
    is_closed_past = (year, month) < (today.year, today.month)
    month_key = f"{year:04d}-{month:02d}"

    if is_closed_past:
        min_date_threshold: Optional[date] = _last_day_of_month(year, month)
    elif is_past_or_current:
        min_date_threshold = today - timedelta(days=1)
    else:
        min_date_threshold = None

    last_len = 0
    last_n_links = 0
    for attempt in range(5):
        html = _fetch_text_retry(url, log=log)
        last_len = len(html)
        last_n_links = len(_DECKLIST_HREF_PROBE_RE.findall(html))
        max_date = _max_decklist_date_on_page(html) if last_n_links else None

        if log:
            log(f"[mtgo-registry] {url}: page_len={last_len} link_count={last_n_links} max_date={max_date}")

        if not is_past_or_current:
            return html

        if last_n_links == 0:
            truncation_reason = "0 decklist links found in a past/current month"
        elif min_date_threshold is not None and (max_date is None or max_date < min_date_threshold):
            truncation_reason = (
                f"latest decklist date on the page is {max_date}, short of expected coverage "
                f"through {min_date_threshold}"
            )
        else:
            truncation_reason = None

        if truncation_reason is None:
            if is_closed_past:
                cache = _load_month_link_count_cache(cache_path)
                cached_count = cache.get(month_key)
                if cached_count is not None and last_n_links < cached_count:
                    raise ChallengeSourceError(
                        f"mtgo.com decklists page for {year}-{month:02d} ({url}) returned "
                        f"{last_n_links} decklist link(s), fewer than the {cached_count} previously "
                        f"recorded for this already-closed month (page_len={last_len}). Past months "
                        "are immutable -- a shrink is always a fetch artifact, never a real content "
                        "change; refusing to use this page."
                    )
                cache[month_key] = max(last_n_links, cached_count or 0)
                _save_month_link_count_cache(cache_path, cache)
            else:
                cache = _load_month_link_count_cache(cache_path)
                cache[month_key] = max(last_n_links, cache.get(month_key, 0))
                _save_month_link_count_cache(cache_path, cache)
            return html

        if log:
            log(
                f"[mtgo-registry] SUSPECT fetch for {url}: {truncation_reason} "
                f"(page_len={last_len}, link_count={last_n_links}) -- looks truncated, not a "
                f"genuinely empty/short month; retry {attempt + 1}/5"
            )
        time.sleep(2 * (attempt + 1))

    raise ChallengeSourceError(
        f"mtgo.com decklists page for {year}-{month:02d} ({url}) still looks truncated after 5 "
        f"attempts (last page_len={last_len}, link_count={last_n_links}, threshold={min_date_threshold}). "
        "Refusing to silently use a partial page."
    )


def build_mtgo_registry(
    format_name: str,
    start_date: date,
    end_date: date,
    log: Optional[Callable[[str], None]] = None,
    count_cache_path: Optional[Path] = None,
    today: Optional[date] = None,
) -> List[MtgoRegistryEvent]:
    """Registry of every event on mtgo.com/decklists for *format_name* within the closed window.

    mtgo.com/decklists with no parameters serves the CURRENT month only (confirmed via its own
    Year/Month picker markup and the "Go" button's URL-building JS: `${origin}/decklists/${year}/${month}`,
    zero-padded month -- e.g. https://www.mtgo.com/decklists/2026/07). A window is fetched month-by-month
    for every (year, month) it touches and merged/deduped by EventID, so a window crossing a month
    boundary (like a 14-day window spanning end-of-month) doesn't silently lose the earlier month.

    Every {format}-prefixed slug must classify into challenge/premier/ignored; anything else
    raises ChallengeSourceError (no silent drop).
    """
    format_slug = _format_slug_for_url(format_name)

    href_re = re.compile(r'href="/decklist/([a-z0-9-]+)"', re.IGNORECASE)
    split_re = re.compile(r"^(?P<slug>.+)-(?P<date>\d{4}-\d{2}-\d{2})(?P<id>\d+)$")
    challenge_re = re.compile(rf"^{re.escape(format_slug)}-challenge-(\d+)$")

    registry: Dict[str, MtgoRegistryEvent] = {}
    for year, month in _months_between(start_date, end_date):
        url = f"{MTGO_BASE}/decklists/{year}/{month:02d}"
        if log:
            log(f"[mtgo-registry] fetching {url}")
        html = _fetch_month_page_with_content_check(
            url, year, month, log=log, count_cache_path=count_cache_path, today=today
        )

        for m in href_re.finditer(html):
            rest = m.group(1)
            sm = split_re.match(rest)
            if not sm:
                continue
            slug, date_str, event_id = sm.group("slug"), sm.group("date"), sm.group("id")
            if not slug.startswith(format_slug + "-"):
                continue
            try:
                ev_date = date.fromisoformat(date_str)
            except ValueError:
                continue
            if not (start_date <= ev_date <= end_date):
                continue

            suffix = slug[len(format_slug) + 1:]
            cm = challenge_re.match(slug)
            if cm:
                kind, size = "challenge", int(cm.group(1))
            elif suffix in PREMIER_SUFFIXES:
                kind, size = "premier", None
            elif suffix in IGNORED_SUFFIXES:
                kind, size = "ignored", None
            else:
                raise ChallengeSourceError(
                    f"Unrecognized {format_slug}-prefixed slug on mtgo.com: '{slug}' "
                    f"(event_id={event_id}, url=/decklist/{rest}). Refusing to silently drop it -- "
                    "classify it into challenge/premier/ignored or fix the pattern."
                )

            registry[event_id] = MtgoRegistryEvent(
                event_id=event_id,
                date=date_str,
                slug=slug,
                kind=kind,
                size=size,
                url=f"{MTGO_BASE}/decklist/{rest}",
            )
    return list(registry.values())


def normalize_card_name(name: str) -> str:
    return re.sub(r"\s+", " ", str(name).strip()).lower()


def fetch_mtgo_event_json(
    event_id: str, url: str, cache_dir: Path, log: Optional[Callable[[str], None]] = None
) -> dict:
    """Fetch (or load from durable cache) the raw window.MTGO.decklists.data JSON blob for one event.

    A blob with an empty "decklists" list (final_rank posted, decklists not yet published) is
    deliberately NOT written to the durable cache -- caching it would permanently freeze the event
    as empty even after MTGO later publishes the real decklists, defeating the "retried on a later
    run" contract for PENDING events. Everything else (including a genuinely malformed blob) is
    cached as before.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{event_id}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    last_err = None
    for attempt in range(5):
        html = _fetch_text_retry(url, log=log)
        m = re.search(r"window\.MTGO\.decklists\.data\s*=\s*(\{.*?\});", html, flags=re.DOTALL)
        if m:
            data = json.loads(m.group(1))
            if data.get("decklists"):
                cache_path.write_text(json.dumps(data), encoding="utf-8")
            elif log:
                log(f"[mtgo-fetch] event {event_id}: 0 decklists published yet, not caching (will retry live next run)")
            return data
        last_err = f"blob not found (page len={len(html)}, attempt {attempt + 1}/5)"
        if log:
            log(f"[mtgo-fetch] {last_err} for {url}")
        time.sleep(2 * (attempt + 1))
    raise ChallengeSourceError(f"Could not find decklist JSON blob on {url} after retries: {last_err}")


@dataclass
class MtgoEventDeck:
    loginid: str
    player: str
    place: int
    signature: Dict[str, int]


def parse_mtgo_event(data: dict) -> List[MtgoEventDeck]:
    """Parse one event's JSON blob into per-player rows with signature (all maindeck cards
    except basic lands, with quantities) and Place from final_rank.

    Raises ChallengeSourceError if the rank set is not exactly {1..32}.
    """
    event_id = data.get("event_id")
    rank_by_login = {r["loginid"]: int(r["rank"]) for r in data.get("final_rank", [])}
    ranks_set = set(rank_by_login.values())
    if ranks_set != set(range(1, 33)):
        raise ChallengeSourceError(
            f"final_rank is not exactly {{1..32}} for mtgo.com event_id={event_id}: got {sorted(ranks_set)}"
        )

    decks: List[MtgoEventDeck] = []
    for d in data.get("decklists", []):
        loginid = d["loginid"]
        place = rank_by_login.get(loginid)
        sig: Dict[str, int] = {}
        for c in d.get("main_deck", []):
            attrs = c.get("card_attributes", {})
            name = normalize_card_name(attrs.get("card_name", ""))
            if not name or name in BASIC_LAND_NAMES:
                continue
            qty = int(c.get("qty", 1))
            sig[name] = sig.get(name, 0) + qty
        decks.append(MtgoEventDeck(loginid=loginid, player=d.get("player", ""), place=place, signature=sig))

    if len(decks) != 32:
        if len(decks) == 0:
            raise ChallengePendingError(
                f"mtgo.com event_id={event_id} has final_rank posted but 0 decklists published yet "
                "-- not yet available, not malformed."
            )
        raise ChallengeSourceError(
            f"mtgo.com event_id={event_id} has {len(decks)} decklists, expected exactly 32."
        )
    return decks


def multiset_similarity(a: Dict[str, int], b: Dict[str, int]) -> float:
    keys = set(a) | set(b)
    if not keys:
        return 0.0
    inter = sum(min(a.get(k, 0), b.get(k, 0)) for k in keys)
    union = sum(max(a.get(k, 0), b.get(k, 0)) for k in keys)
    return inter / union if union else 0.0


def resolve_ambiguous_event(
    mtggoldfish_pilots: List[str],
    candidate_event_ids: List[str],
    mtgo_decks_by_event: Dict[str, List[MtgoEventDeck]],
    min_overlap_fraction: float = 0.85,
    min_separation: int = 8,
) -> str:
    """Given a MTGGoldfish roster's pilot set for a (date,tier) collision and the 2+ candidate
    mtgo.com event_ids for that (date,tier), return the event_id whose pilot set the roster
    actually belongs to, based on pilot-set overlap. Raises ChallengeSourceError if the match
    isn't clearly decisive (no guessing).
    """
    mg_set = {normalize_name(p) for p in mtggoldfish_pilots}
    scored = []
    for eid in candidate_event_ids:
        mtgo_set = {normalize_name(d.player) for d in mtgo_decks_by_event[eid]}
        overlap = len(mg_set & mtgo_set)
        scored.append((overlap, eid))
    scored.sort(key=lambda x: -x[0])
    best_overlap, best_eid = scored[0]
    second_overlap = scored[1][0] if len(scored) > 1 else 0

    if best_overlap < int(min_overlap_fraction * len(mg_set)):
        raise ChallengeSourceError(
            f"Cannot resolve which event the MTGGoldfish roster belongs to: best overlap "
            f"{best_overlap}/{len(mg_set)} pilots against candidates {candidate_event_ids} "
            f"(scores={scored})."
        )
    if best_overlap - second_overlap < min_separation:
        raise ChallengeSourceError(
            f"Ambiguous match resolving MTGGoldfish roster to an event_id: top two candidates "
            f"too close ({scored})."
        )
    return best_eid


PLACE_THRESHOLDS = [
    (1, 1, None),      # place 1 -> always manual, no auto-threshold
    (2, 8, 0.95),
    (9, 32, 0.90),
]


def _threshold_for_place(place: int) -> Optional[float]:
    for lo, hi, thr in PLACE_THRESHOLDS:
        if lo <= place <= hi:
            return thr
    raise ValueError(f"place {place} out of range 1..32")


@dataclass
class ClassificationResult:
    deck: MtgoEventDeck
    predicted_label: Optional[str]
    nearest_label: Optional[str]
    nearest_sim: float
    second_label: Optional[str]
    second_sim: float
    needs_review: bool
    reason: str = ""


def classify_deck(
    target: MtgoEventDeck, library: List[Tuple[str, Dict[str, int]]]
) -> ClassificationResult:
    scored = sorted(
        ((multiset_similarity(target.signature, sig), label) for label, sig in library),
        key=lambda x: -x[0],
    )
    nearest_sim, nearest_label = scored[0]
    second_sim, second_label = scored[1]

    if target.place == 1:
        return ClassificationResult(
            deck=target, predicted_label=None, nearest_label=nearest_label, nearest_sim=nearest_sim,
            second_label=second_label, second_sim=second_sim, needs_review=True,
            reason="place=1 always requires manual confirmation",
        )

    threshold = _threshold_for_place(target.place)
    if nearest_label != second_label:
        return ClassificationResult(
            deck=target, predicted_label=None, nearest_label=nearest_label, nearest_sim=nearest_sim,
            second_label=second_label, second_sim=second_sim, needs_review=True,
            reason=f"nearest neighbors disagree ({nearest_label!r} vs {second_label!r})",
        )
    if nearest_sim < threshold:
        return ClassificationResult(
            deck=target, predicted_label=None, nearest_label=nearest_label, nearest_sim=nearest_sim,
            second_label=second_label, second_sim=second_sim, needs_review=True,
            reason=f"similarity {nearest_sim:.3f} below threshold {threshold} for place {target.place}",
        )
    return ClassificationResult(
        deck=target, predicted_label=nearest_label, nearest_label=nearest_label, nearest_sim=nearest_sim,
        second_label=second_label, second_sim=second_sim, needs_review=False,
    )


@dataclass
class ChallengeEventRow:
    place: int
    deck: str
    archetype: str
    pilot: str
    loginid: str


@dataclass
class ReviewItem:
    event_id: str
    date: str
    size: int
    place: int
    pilot: str
    reason: str
    nearest_label: Optional[str]
    nearest_sim: float
    second_label: Optional[str]
    second_sim: float
    key_cards: List[Tuple[str, int]]
    url: str = ""
    format_name: str = ""
    kind: str = "challenge"  # "challenge" or "premier" -- which persistent history file it belongs to


@dataclass
class SkippedEvent:
    """One registry event that could not be fetched/parsed this run -- excluded from
    challenge_events/premier_events and from event_rows/premier_rows, but still accounted for so
    the caller can build a completeness gate instead of silently narrowing the window.
    """
    event_id: str
    date: str
    size: Optional[int]
    kind: str  # "challenge" or "premier"
    url: str
    status: str  # "PENDING" or "ERROR"
    reason: str


@dataclass
class ChallengeDatasetResult:
    challenge_events: List[MtgoRegistryEvent]
    premier_events: List[MtgoRegistryEvent]
    event_rows: Dict[str, List[ChallengeEventRow]]
    premier_rows: Dict[str, List[ChallengeEventRow]]
    review_items: List[ReviewItem]
    tier_counts: Dict[int, int]
    skipped_events: List[SkippedEvent]
    window_start: Optional[date] = None
    window_end: Optional[date] = None


def _load_resolved_lookup(history_csv: Optional[Path]) -> Dict[Tuple[str, str], Tuple[str, str]]:
    """(event_id, pilot) -> (Deck, Archetype) for every already-resolved row in *history_csv*.

    A deck a human already confirmed in a past run's Review Queue (or that the classifier
    auto-resolved) must never be re-flagged just because a rule like "place=1 always requires
    manual confirmation" would otherwise apply again on every fresh run.
    """
    if history_csv is None or not history_csv.exists():
        return {}
    try:
        hist = pd.read_csv(history_csv, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    except Exception:
        return {}
    if hist.empty or "EventID" not in hist.columns:
        return {}
    resolved = hist[hist["Deck"] != NEEDS_MANUAL_REVIEW]
    out: Dict[Tuple[str, str], Tuple[str, str]] = {}
    for _, r in resolved.iterrows():
        event_id = str(r["EventID"]).strip()
        if not event_id:
            continue
        out[(event_id, str(r["Pilot"]).strip())] = (str(r["Deck"]), str(r["Archetype"]))
    return out


def _classify_event_decks(
    c: MtgoRegistryEvent,
    decks: List[MtgoEventDeck],
    library: List[Tuple[str, Dict[str, int]]],
    archetype_for: Callable[[str, str], str],
    review_items: List[ReviewItem],
    resolved: Optional[Dict[Tuple[str, str], Tuple[str, str]]] = None,
    format_name: str = "",
    kind: str = "challenge",
) -> List[ChallengeEventRow]:
    """Classify every deck of one event against *library*; anything not confidently
    classifiable is appended to *review_items* and rendered as NEEDS_MANUAL_REVIEW.

    *resolved* -- already-confirmed (event_id, pilot) -> (Deck, Archetype) pairs (see
    _load_resolved_lookup) -- take priority over classification entirely, so a deck a human
    already named in a past run's Review Queue is applied silently instead of being re-flagged.
    """
    resolved = resolved or {}
    rows: List[ChallengeEventRow] = []
    for d in sorted(decks, key=lambda x: x.place):
        known = resolved.get((str(c.event_id).strip(), str(d.player).strip()))
        if known is not None:
            deck_label, archetype = known
            rows.append(ChallengeEventRow(place=d.place, deck=deck_label, archetype=archetype, pilot=d.player, loginid=d.loginid))
            continue
        result = classify_deck(d, library)
        if result.needs_review:
            top_cards = sorted(d.signature.items(), key=lambda kv: -kv[1])[:8]
            review_items.append(
                ReviewItem(
                    event_id=c.event_id, date=c.date, size=c.size or 0, place=d.place, pilot=d.player,
                    reason=result.reason, nearest_label=result.nearest_label, nearest_sim=result.nearest_sim,
                    second_label=result.second_label, second_sim=result.second_sim, key_cards=top_cards,
                    url=c.url, format_name=format_name, kind=kind,
                )
            )
            rows.append(ChallengeEventRow(
                place=d.place, deck=NEEDS_MANUAL_REVIEW, archetype=NEEDS_MANUAL_REVIEW,
                pilot=d.player, loginid=d.loginid,
            ))
        else:
            deck_label = result.predicted_label or NEEDS_MANUAL_REVIEW
            archetype = archetype_for(deck_label, deck_label)
            rows.append(ChallengeEventRow(place=d.place, deck=deck_label, archetype=archetype, pilot=d.player, loginid=d.loginid))
    return rows


def build_challenge_dataset(
    format_name: str,
    start_date: date,
    end_date: date,
    aliases: List[DeckAliasRule],
    rules: List[ArchetypeRule],
    challenge_mappings: List[UserDeckMapping],
    cache_dir: Path,
    log: Optional[Callable[[str], None]] = None,
    challenge_history_csv: Optional[Path] = None,
    premier_history_csv: Optional[Path] = None,
) -> ChallengeDatasetResult:
    """Build the full labeled Challenge dataset for a window, mtgo.com-first.

    Every mtgo.com event_id in the window is fetched. Challenge events with a unique (tier,date)
    get their Deck label transferred from MTGGoldfish by (event,pilot) join. For (tier,date)
    collisions -- MTGGoldfish can only ever expose one roster per (tier,date) -- the roster is
    matched to whichever event_id's pilot set it actually belongs to (ChallengeSourceError if
    that match isn't decisive); the other event_id(s) in the collision are labeled by a
    maindeck-signature nearest-neighbor classifier against the rest of the window's labeled decks,
    with anything not confidently classifiable reported as NEEDS_MANUAL_REVIEW rather than guessed.

    *challenge_history_csv* / *premier_history_csv*, if given, seed a lookup of already-resolved
    (event_id, pilot) -> (Deck, Archetype) pairs from the persisted history -- a deck a human
    already confirmed in a past run's Review Queue is applied directly and never re-flagged, even
    for rules (like "place=1 always requires manual confirmation") that would otherwise trigger on
    every single run regardless of history.
    """
    def emit(msg: str) -> None:
        if log:
            log(msg)

    resolved_challenge = _load_resolved_lookup(challenge_history_csv)
    resolved_premier = _load_resolved_lookup(premier_history_csv)
    if resolved_challenge or resolved_premier:
        emit(
            f"[mtgo-dataset] loaded {len(resolved_challenge)} already-resolved challenge deck(s) and "
            f"{len(resolved_premier)} already-resolved premier deck(s) from persisted history"
        )

    mapping_by_raw = mapping_lookup(challenge_mappings)

    def canonical_deck(raw_name: str) -> str:
        key = normalize_name(raw_name)
        mapping = mapping_by_raw.get(key)
        if mapping is not None and mapping.canonical_name:
            return mapping.canonical_name
        return canonicalize_deck_name(raw_name, aliases)

    def archetype_for(deck_label: str, raw_name: str) -> str:
        if deck_label == NEEDS_MANUAL_REVIEW:
            return NEEDS_MANUAL_REVIEW
        key = normalize_name(raw_name)
        mapping = mapping_by_raw.get(key)
        if mapping is not None and mapping.archetype:
            return mapping.archetype
        return classify_archetype(deck_label, rules)

    registry = build_mtgo_registry(format_name, start_date, end_date, log=log)
    registry_challenge_events = [r for r in registry if r.kind == "challenge"]
    registry_premier_events = [r for r in registry if r.kind == "premier"]

    skipped_events: List[SkippedEvent] = []

    # Fetch+parse every challenge event individually. A single bad event (not-yet-published
    # decklists, or genuinely malformed data) must never abort the rest of the registry -- it is
    # recorded in *skipped_events* and excluded from challenge_events/event_rows, and everything
    # else in the window still gets processed and persisted.
    mtgo_decks_by_event: Dict[str, List[MtgoEventDeck]] = {}
    for c in registry_challenge_events:
        try:
            data = fetch_mtgo_event_json(c.event_id, c.url, cache_dir, log=log)
            mtgo_decks_by_event[c.event_id] = parse_mtgo_event(data)
        except ChallengePendingError as exc:
            skipped_events.append(SkippedEvent(c.event_id, c.date, c.size, "challenge", c.url, "PENDING", str(exc)))
            emit(f"[mtgo-dataset] PENDING C{c.size} {c.date} ({c.event_id}): {exc}")
        except ChallengeSourceError as exc:
            skipped_events.append(SkippedEvent(c.event_id, c.date, c.size, "challenge", c.url, "ERROR", str(exc)))
            emit(f"[mtgo-dataset] ERROR C{c.size} {c.date} ({c.event_id}): {exc}")

    # Only events that actually fetched are eligible for tier/date grouping. This also means a
    # (tier, date) pair that mtgo.com lists as a collision but where one side failed to fetch is
    # correctly treated as a single clean event for whichever side DID fetch -- never silently
    # merged, never silently dropped.
    challenge_events = [c for c in registry_challenge_events if c.event_id in mtgo_decks_by_event]
    tier_counts = Counter(c.size for c in challenge_events)

    by_tier_date: Dict[Tuple[int, str], List[MtgoRegistryEvent]] = {}
    for c in challenge_events:
        by_tier_date.setdefault((c.size, c.date), []).append(c)
    ambiguous = {k: v for k, v in by_tier_date.items() if len(v) > 1}
    clean = [v[0] for k, v in by_tier_date.items() if len(v) == 1]

    emit(
        f"[mtgo-dataset] registry: {len(registry_challenge_events)} challenge event(s) in window "
        f"({len(challenge_events)} fetched, {sum(1 for s in skipped_events if s.kind == 'challenge')} skipped), "
        f"{len(registry_premier_events)} premier event(s) in window "
        f"({len(clean)} clean tier/date, {len(ambiguous)} collision group(s))"
    )

    library: List[Tuple[str, Dict[str, int]]] = []
    event_rows: Dict[str, List[ChallengeEventRow]] = {}

    def ingest_labeled_event(c: MtgoRegistryEvent, mg_df: pd.DataFrame, mtgo_decks: List[MtgoEventDeck]) -> None:
        mtgo_by_pilot = {normalize_name(d.player): d for d in mtgo_decks}
        rows: List[ChallengeEventRow] = []
        for _, row in mg_df.iterrows():
            raw_deck = str(row.get("Deck") or "")
            pilot = str(row.get("Pilot") or "")
            place = int(row.get("Place"))
            deck_label = canonical_deck(raw_deck)
            archetype = archetype_for(deck_label, raw_deck)
            mtgo_deck = mtgo_by_pilot.get(normalize_name(pilot))
            loginid = mtgo_deck.loginid if mtgo_deck else ""
            rows.append(ChallengeEventRow(place=place, deck=deck_label, archetype=archetype, pilot=pilot, loginid=loginid))
            if mtgo_deck is not None:
                library.append((deck_label, mtgo_deck.signature))
        event_rows[c.event_id] = rows

    for c in clean:
        challenge_info = {"slug": c.slug, "challenge_size": c.size, "event_date": c.date, "mtgo_url": c.url}
        _event_info, mg_df = _parse_single_challenge(challenge_info, format_name)
        ingest_labeled_event(c, mg_df, mtgo_decks_by_event[c.event_id])
        emit(f"[mtgo-dataset] clean C{c.size} {c.date} ({c.event_id}): labeled from MTGGoldfish")

    unresolved_events: List[MtgoRegistryEvent] = []
    for (size, dt), evs in ambiguous.items():
        event_ids = [e.event_id for e in evs]
        challenge_info = {"slug": evs[0].slug, "challenge_size": size, "event_date": dt, "mtgo_url": evs[0].url}
        _event_info, mg_df = _parse_single_challenge(challenge_info, format_name)
        mg_pilots = mg_df["Pilot"].tolist()
        winner_eid = resolve_ambiguous_event(mg_pilots, event_ids, mtgo_decks_by_event)
        winner_event = next(e for e in evs if e.event_id == winner_eid)
        ingest_labeled_event(winner_event, mg_df, mtgo_decks_by_event[winner_eid])
        emit(
            f"[mtgo-dataset] collision C{size} {dt}: MTGGoldfish roster resolved to event {winner_eid}; "
            f"remaining {[eid for eid in event_ids if eid != winner_eid]} need classification"
        )
        for e in evs:
            if e.event_id != winner_eid:
                unresolved_events.append(e)

    review_items: List[ReviewItem] = []
    for c in unresolved_events:
        rows = _classify_event_decks(
            c, mtgo_decks_by_event[c.event_id], library, archetype_for, review_items, resolved_challenge, format_name
        )
        event_rows[c.event_id] = rows
        classified = sum(1 for r in rows if r.deck != NEEDS_MANUAL_REVIEW)
        emit(f"[mtgo-dataset] classified event {c.event_id}: {classified}/32 auto, {32 - classified}/32 review")

    # Premier events never touch Challenge stats/history CSV, but they get their own parallel
    # premier_history file (see sync_challenge_history_window call site) so manual review
    # corrections have somewhere to persist. Labeled via the same classifier + reference library
    # (built purely from Challenge events), never mixed back into that library.
    #
    # Same per-event resilience as the challenge loop above: a single bad premier event is recorded
    # in skipped_events and excluded from premier_events/premier_rows, never aborts the others.
    premier_rows: Dict[str, List[ChallengeEventRow]] = {}
    premier_events: List[MtgoRegistryEvent] = []
    for c in registry_premier_events:
        try:
            data = fetch_mtgo_event_json(c.event_id, c.url, cache_dir, log=log)
            decks = parse_mtgo_event(data)
        except ChallengePendingError as exc:
            skipped_events.append(SkippedEvent(c.event_id, c.date, c.size, "premier", c.url, "PENDING", str(exc)))
            emit(f"[mtgo-dataset] PENDING premier {c.date} ({c.event_id}): {exc}")
            continue
        except ChallengeSourceError as exc:
            skipped_events.append(SkippedEvent(c.event_id, c.date, c.size, "premier", c.url, "ERROR", str(exc)))
            emit(f"[mtgo-dataset] ERROR premier {c.date} ({c.event_id}): {exc}")
            continue
        rows = _classify_event_decks(
            c, decks, library, archetype_for, review_items, resolved_premier, format_name, kind="premier"
        )
        premier_rows[c.event_id] = rows
        premier_events.append(c)
        classified = sum(1 for r in rows if r.deck != NEEDS_MANUAL_REVIEW)
        emit(f"[mtgo-dataset] classified premier event {c.event_id} ({c.slug}): {classified}/32 auto, {32 - classified}/32 review")

    return ChallengeDatasetResult(
        challenge_events=challenge_events,
        premier_events=premier_events,
        event_rows=event_rows,
        premier_rows=premier_rows,
        review_items=review_items,
        tier_counts=dict(tier_counts),
        skipped_events=skipped_events,
        window_start=start_date,
        window_end=end_date,
    )


def _humanize_premier_slug(slug: str, format_slug: str) -> str:
    suffix = slug[len(format_slug) + 1:] if slug.startswith(format_slug + "-") else slug
    words = suffix.split("-")
    return " ".join(w.upper() if w.lower() == "rc" else w.capitalize() for w in words)


def _format_top8(rows: List[ChallengeEventRow]) -> str:
    top8 = sorted((r for r in rows if r.place <= 8), key=lambda r: r.place)
    return "; ".join(f"{r.place}:{r.deck} ({r.pilot})" for r in top8)


def write_challenge_manifest(dataset: ChallengeDatasetResult, path: Path, log: Optional[Callable[[str], None]] = None) -> Path:
    """One row per Challenge event: event_id,date,tier,slug,url,n_decklists,winner_deck,winner_pilot,top8_decks."""
    rows = []
    for c in sorted(dataset.challenge_events, key=lambda e: (e.date, e.size, e.event_id)):
        ev_rows = dataset.event_rows.get(c.event_id, [])
        winner = next((r for r in ev_rows if r.place == 1), None)
        rows.append({
            "event_id": c.event_id,
            "date": c.date,
            "tier": c.size,
            "slug": c.slug,
            "url": c.url,
            "n_decklists": len(ev_rows),
            "winner_deck": winner.deck if winner else "",
            "winner_pilot": winner.pilot if winner else "",
            "top8_decks": _format_top8(ev_rows),
        })
    df = pd.DataFrame(
        rows,
        columns=["event_id", "date", "tier", "slug", "url", "n_decklists", "winner_deck", "winner_pilot", "top8_decks"],
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")

    if log:
        log(f"[manifest] {len(rows)} event(s) -> {path}")
        tier_counts = Counter(r["tier"] for r in rows)
        log(f"[manifest] per-tier: {dict(sorted(tier_counts.items()))}")
        same_tier_dates = Counter((r["tier"], r["date"]) for r in rows)
        dupes = {k: v for k, v in same_tier_dates.items() if v > 1}
        if dupes:
            log(f"[manifest] date(s) with >1 event of the same tier: {dupes}")
    return path


def write_premier_events_csv(
    dataset: ChallengeDatasetResult, format_slug: str, path: Path, log: Optional[Callable[[str], None]] = None
) -> Path:
    """One row per premier event: event_id,date,event_type,url,winner_deck,winner_pilot,top8_decks.

    Written with a header even when *dataset.premier_events* is empty.
    """
    rows = []
    for c in sorted(dataset.premier_events, key=lambda e: (e.date, e.event_id)):
        ev_rows = dataset.premier_rows.get(c.event_id, [])
        winner = next((r for r in ev_rows if r.place == 1), None)
        rows.append({
            "event_id": c.event_id,
            "date": c.date,
            "event_type": _humanize_premier_slug(c.slug, format_slug),
            "url": c.url,
            "winner_deck": winner.deck if winner else "",
            "winner_pilot": winner.pilot if winner else "",
            "top8_decks": _format_top8(ev_rows),
        })
    df = pd.DataFrame(
        rows, columns=["event_id", "date", "event_type", "url", "winner_deck", "winner_pilot", "top8_decks"]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    if log:
        log(f"[premier] {len(rows)} event(s) -> {path}")
    return path


@dataclass
class CompletenessSummary:
    """Fail-closed completeness signal for one Challenge window.

    *complete* is only ever True when registry_count is a positive, real integer, fetched_count
    equals it exactly, and missing is empty -- see the `complete` property notes at each
    construction site. registry_count/fetched_count are Optional[int] specifically so a caller
    that could not even build the registry (see unavailable_completeness_summary) can report
    "unknown", never "0" and never "complete".

    premier_checked/premier_count/premier_events/premier_note are a second, independent signal:
    premier events never affect registry_count/fetched_count/complete. premier_checked is True
    whenever the premier registry scan actually ran (even if it found zero events -- "zero found"
    and "never checked" must never collapse into the same falsy value). premier_count is None
    only when premier_checked is False.
    """
    registry_count: Optional[int]
    fetched_count: Optional[int]
    complete: bool
    missing: List[SkippedEvent]
    tier_counts_registry: Dict[int, int] = field(default_factory=dict)
    tier_counts_fetched: Dict[int, int] = field(default_factory=dict)
    premier_checked: bool = False
    premier_count: Optional[int] = None
    premier_events: List[Tuple[str, str, str]] = field(default_factory=list)
    premier_note: str = "Premier scan did not complete"


def unavailable_completeness_summary(
    window_start: date, window_end: date, reason: str
) -> CompletenessSummary:
    """The fail-closed CompletenessSummary for "the registry/premier scan itself never ran or
    threw" -- e.g. build_mtgo_registry() raised before any events could be classified. Every count
    is None (not 0), complete is False, and premier_checked is False with a null premier_count --
    this is the exact same failure shape as the old bug (`complete: true, registry_count: null`),
    just inverted to be honest about it instead of defaulting to true.
    """
    return CompletenessSummary(
        registry_count=None,
        fetched_count=None,
        complete=False,
        missing=[SkippedEvent(event_id="", date="", size=None, kind="registry", url="", status="ERROR", reason=reason)],
        tier_counts_registry={},
        tier_counts_fetched={},
        premier_checked=False,
        premier_count=None,
        premier_events=[],
        premier_note="Premier scan did not complete",
    )


def _build_premier_note(
    premier_events: List[Tuple[str, str, str]], format_name: str, window_start: date, window_end: date
) -> str:
    if not premier_events:
        return (
            f"No {format_name} premier events in window "
            f"{window_start.isoformat()}..{window_end.isoformat()} (registry checked)"
        )
    return "; ".join(f"{ev_date} {ev_type} ({eid})" for eid, ev_type, ev_date in sorted(premier_events, key=lambda t: t[2]))


def write_event_status_manifest(
    dataset: ChallengeDatasetResult, path: Path, log: Optional[Callable[[str], None]] = None,
    format_name: str = "",
) -> CompletenessSummary:
    """One row per registry event (challenge AND premier) this run discovered: event_id, date,
    tier, kind, status (OK/PENDING/ERROR), reason. A pure audit trail -- OK rows come from
    dataset.challenge_events/premier_events, PENDING/ERROR rows come from dataset.skipped_events.
    Never narrows the declared window; a missing event is reported, not hidden.
    """
    rows = []
    for c in dataset.challenge_events:
        rows.append({"event_id": c.event_id, "date": c.date, "tier": c.size, "kind": "challenge", "status": "OK", "reason": ""})
    for c in dataset.premier_events:
        rows.append({"event_id": c.event_id, "date": c.date, "tier": c.size or "", "kind": "premier", "status": "OK", "reason": ""})
    for s in dataset.skipped_events:
        rows.append({"event_id": s.event_id, "date": s.date, "tier": s.size or "", "kind": s.kind, "status": s.status, "reason": s.reason})

    df = pd.DataFrame(rows, columns=["event_id", "date", "tier", "kind", "status", "reason"])
    if not df.empty:
        df = df.sort_values(["kind", "date", "event_id"]).reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")

    registry_count = len(dataset.challenge_events) + sum(1 for s in dataset.skipped_events if s.kind == "challenge")
    fetched_count = len(dataset.challenge_events)
    missing = [s for s in dataset.skipped_events if s.kind == "challenge"]
    complete = registry_count > 0 and fetched_count == registry_count and not missing

    tier_counts_registry = dict(Counter(
        [c.size for c in dataset.challenge_events]
        + [s.size for s in dataset.skipped_events if s.kind == "challenge"]
    ))
    tier_counts_fetched = dict(Counter(c.size for c in dataset.challenge_events))

    format_slug = _format_slug_for_url(format_name) if format_name else ""
    premier_events = [
        (c.event_id, _humanize_premier_slug(c.slug, format_slug), c.date) for c in dataset.premier_events
    ]
    window_start = dataset.window_start
    window_end = dataset.window_end
    premier_note = (
        _build_premier_note(premier_events, format_name or "Modern", window_start, window_end)
        if window_start is not None and window_end is not None
        else "; ".join(f"{ev_date} {ev_type} ({eid})" for eid, ev_type, ev_date in premier_events) or "No premier events found"
    )

    if log:
        log(f"[event-status] {len(rows)} row(s) -> {path}")
        if missing:
            log(
                f"[event-status] INCOMPLETE window: {fetched_count}/{registry_count} challenge event(s) fetched; "
                f"missing: {[(m.event_id, m.date, m.status) for m in missing]}"
            )
        else:
            log(f"[event-status] complete window: {fetched_count}/{registry_count} challenge event(s) fetched")
        log(f"[event-status] premier: {premier_note}")

    return CompletenessSummary(
        registry_count=registry_count,
        fetched_count=fetched_count,
        complete=complete,
        missing=missing,
        tier_counts_registry=tier_counts_registry,
        tier_counts_fetched=tier_counts_fetched,
        premier_checked=True,
        premier_count=len(premier_events),
        premier_events=premier_events,
        premier_note=premier_note,
    )


def verify_registry_against_history(
    format_name: str,
    start_date: date,
    end_date: date,
    history_csv: Path,
    log: Optional[Callable[[str], None]] = None,
) -> CompletenessSummary:
    """Build the mtgo.com registry for the window and diff it against what EventIDs are already
    present in *history_csv* -- one cheap registry-index fetch (no per-event decklist fetch), so
    this is safe to call from a "recompute stats only" path (GUI or --verify-only CLI) that must
    never claim complete=true just because it never bothered to check.
    """
    try:
        registry = build_mtgo_registry(format_name, start_date, end_date, log=log)
    except Exception as exc:
        if log:
            log(f"[verify] registry build failed, reporting UNAVAILABLE (not complete=true): {exc}")
        return unavailable_completeness_summary(start_date, end_date, str(exc))

    registry_challenge = [r for r in registry if r.kind == "challenge"]
    registry_premier = [r for r in registry if r.kind == "premier"]

    present_ids: set = set()
    if history_csv.exists():
        hist = pd.read_csv(history_csv, dtype=str, encoding="utf-8-sig", keep_default_na=False)
        if not hist.empty and "EventID" in hist.columns:
            hist["EventID"] = hist["EventID"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
            if "Format" in hist.columns:
                fmt_norm = normalize_name(format_name)
                hist = hist[hist["Format"].astype(str).apply(normalize_name) == fmt_norm]
            present_ids = set(hist["EventID"].unique())

    fetched = [r for r in registry_challenge if r.event_id in present_ids]
    missing = [
        SkippedEvent(event_id=r.event_id, date=r.date, size=r.size, kind="challenge", url=r.url,
                     status="MISSING", reason="not present in history_csv")
        for r in registry_challenge if r.event_id not in present_ids
    ]

    registry_count = len(registry_challenge)
    fetched_count = len(fetched)
    complete = registry_count > 0 and fetched_count == registry_count and not missing

    tier_counts_registry = dict(Counter(r.size for r in registry_challenge))
    tier_counts_fetched = dict(Counter(r.size for r in fetched))

    format_slug = _format_slug_for_url(format_name)
    premier_events = [(r.event_id, _humanize_premier_slug(r.slug, format_slug), r.date) for r in registry_premier]
    premier_note = _build_premier_note(premier_events, format_name, start_date, end_date)

    if log:
        log(
            f"[verify] registry={registry_count} present_in_history={fetched_count} "
            f"missing={len(missing)} premier={len(premier_events)}"
        )

    return CompletenessSummary(
        registry_count=registry_count,
        fetched_count=fetched_count,
        complete=complete,
        missing=missing,
        tier_counts_registry=tier_counts_registry,
        tier_counts_fetched=tier_counts_fetched,
        premier_checked=True,
        premier_count=len(premier_events),
        premier_events=premier_events,
        premier_note=premier_note,
    )


def write_review_items_csv(dataset: ChallengeDatasetResult, path: Path, log: Optional[Callable[[str], None]] = None) -> Path:
    """Every NEEDS_MANUAL_REVIEW deck (Challenge or premier), with enough context to resolve it by hand.

    Fill in the "resolved_deck" column (deck name) for whichever rows you've identified, then run
    `python apply_challenge_review.py <this file>` to write your corrections back into the
    persistent Challenge history and rebuild the statistics. Leave "resolved_deck" blank for rows
    you haven't resolved yet -- they're skipped, not treated as errors.
    """
    rows = []
    for item in dataset.review_items:
        rows.append({
            "event_id": item.event_id,
            "date": item.date,
            "format": item.format_name,
            "kind": item.kind,
            "tier": item.size,
            "place": item.place,
            "pilot": item.pilot,
            "reason": item.reason,
            "nearest_label": item.nearest_label,
            "nearest_sim": round(item.nearest_sim, 3),
            "second_label": item.second_label,
            "second_sim": round(item.second_sim, 3),
            "key_cards": "; ".join(f"{name} x{qty}" for name, qty in item.key_cards),
            "event_url": item.url,
            "resolved_deck": "",
        })
    df = pd.DataFrame(
        rows,
        columns=[
            "event_id", "date", "format", "kind", "tier", "place", "pilot", "reason", "nearest_label", "nearest_sim",
            "second_label", "second_sim", "key_cards", "event_url", "resolved_deck",
        ],
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    if log:
        log(f"[review] {len(rows)} item(s) needing manual review -> {path}")
        log(f"[review] fill in the 'resolved_deck' column, then run: python apply_challenge_review.py \"{path}\"")
    return path


def _build_library_from_history_files(
    history_csvs: List[Path], cache_dir: Path, log: Optional[Callable[[str], None]] = None
) -> List[Tuple[str, Dict[str, int]]]:
    """Reference library of (Deck, signature) built from every already-labeled row across the
    given persisted history files -- not just one run's window. History can span many months and
    hundreds of events, most never fetched through the mtgo.com pipeline at all (older,
    MTGGoldfish-only data) -- hitting the network for every single one would be extremely slow and
    mostly futile, so this ONLY uses events already present in the JSON cache. That is intentional:
    the library is best-effort and grows over time as more windows get processed through the
    mtgo.com pipeline; it never blocks on network I/O for historical reference data.
    """
    library: List[Tuple[str, Dict[str, int]]] = []
    skipped_uncached = 0
    for history_csv in history_csvs:
        if not history_csv.exists():
            continue
        hist = pd.read_csv(history_csv, dtype=str, encoding="utf-8-sig", keep_default_na=False)
        hist["EventID"] = hist["EventID"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
        if hist.empty:
            continue
        labeled = hist[(hist["Deck"] != NEEDS_MANUAL_REVIEW) & (hist["EventID"] != "")]
        for event_id, group in labeled.groupby("EventID"):
            cache_path = cache_dir / f"{event_id}.json"
            if not cache_path.exists():
                skipped_uncached += 1
                continue
            try:
                data = json.loads(cache_path.read_text(encoding="utf-8"))
                decks = parse_mtgo_event(data)
            except Exception as exc:
                if log:
                    log(f"[rescan] library: could not load cached event {event_id}: {exc}")
                continue
            decks_by_pilot = {normalize_name(d.player): d for d in decks}
            for _, row in group.iterrows():
                deck_obj = decks_by_pilot.get(normalize_name(str(row["Pilot"])))
                if deck_obj is not None:
                    library.append((str(row["Deck"]), deck_obj.signature))
    if log and skipped_uncached:
        log(f"[rescan] library: skipped {skipped_uncached} event(s) not in the local JSON cache (no network fetch attempted)")
    return library


def rescan_history_for_unresolved(
    history_csv: Path,
    cache_dir: Path,
    kind: str,
    format_name: str,
    rules: List[ArchetypeRule],
    challenge_mappings: List[UserDeckMapping],
    library: List[Tuple[str, Dict[str, int]]],
    log: Optional[Callable[[str], None]] = None,
) -> List[ReviewItem]:
    """Re-attempt classification for every Deck==NEEDS_MANUAL_REVIEW row currently persisted in
    *history_csv*, regardless of which run (or migration script) put it there. Anything that now
    clears the same confidence bar as a normal run is written straight back to *history_csv*
    (identical classify_deck() logic -- no relaxed threshold just because it's a rescan). Anything
    still short is returned as a ReviewItem for the GUI/CLI review queue.
    """
    def emit(msg: str) -> None:
        if log:
            log(msg)

    if not history_csv.exists():
        return []
    hist = pd.read_csv(history_csv, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    hist["EventID"] = hist["EventID"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    unresolved = hist[hist["Deck"] == NEEDS_MANUAL_REVIEW]
    if unresolved.empty:
        return []

    mapping_by_raw = mapping_lookup(challenge_mappings)

    def archetype_for(deck_label: str, raw_name: str) -> str:
        if deck_label == NEEDS_MANUAL_REVIEW:
            return NEEDS_MANUAL_REVIEW
        key = normalize_name(raw_name)
        mapping = mapping_by_raw.get(key)
        if mapping is not None and mapping.archetype:
            return mapping.archetype
        return classify_archetype(deck_label, rules)

    review_items: List[ReviewItem] = []
    auto_resolved = 0

    for event_id, group in unresolved.groupby("EventID"):
        if not event_id:
            continue
        row0 = group.iloc[0]
        slug, event_date, tier_raw = str(row0["EventSlug"]), str(row0["EventDate"]), str(row0["ChallengeSize"])
        try:
            size = int(tier_raw)
        except ValueError:
            size = 0
        url = f"{MTGO_BASE}/decklist/{slug}-{event_date}{event_id}"
        try:
            data = fetch_mtgo_event_json(event_id, url, cache_dir, log=log)
            decks = parse_mtgo_event(data)
        except Exception as exc:
            emit(f"[rescan] could not fetch event {event_id} ({url}): {exc}")
            continue
        decks_by_pilot = {normalize_name(d.player): d for d in decks}

        for idx, row in group.iterrows():
            pilot = str(row["Pilot"])
            deck_obj = decks_by_pilot.get(normalize_name(pilot))
            if deck_obj is None:
                emit(f"[rescan] event {event_id}: pilot {pilot!r} not found in mtgo.com roster, skipping")
                continue

            result = classify_deck(deck_obj, library)
            if result.needs_review:
                top_cards = sorted(deck_obj.signature.items(), key=lambda kv: -kv[1])[:8]
                review_items.append(
                    ReviewItem(
                        event_id=event_id, date=event_date, size=size, place=deck_obj.place, pilot=pilot,
                        reason=result.reason, nearest_label=result.nearest_label, nearest_sim=result.nearest_sim,
                        second_label=result.second_label, second_sim=result.second_sim, key_cards=top_cards,
                        url=url, format_name=format_name, kind=kind,
                    )
                )
            else:
                deck_label = result.predicted_label or NEEDS_MANUAL_REVIEW
                hist.loc[idx, "Deck"] = deck_label
                hist.loc[idx, "Archetype"] = archetype_for(deck_label, deck_label)
                auto_resolved += 1
                emit(f"[rescan] event {event_id} pilot={pilot!r}: auto-resolved -> {deck_label!r}")

    if auto_resolved:
        hist["EventID"] = hist["EventID"].astype(str).str.replace(r"\.0$", "", regex=True)
        hist.to_csv(history_csv, index=False, encoding="utf-8-sig")
        emit(f"[rescan] {auto_resolved} deck(s) auto-resolved and written to {history_csv}")

    return review_items


def scan_all_history_for_review(
    challenge_history_csv: Path,
    premier_history_csv: Optional[Path],
    cache_dir: Path,
    format_name: str,
    rules: List[ArchetypeRule],
    challenge_mappings: List[UserDeckMapping],
    log: Optional[Callable[[str], None]] = None,
) -> List[ReviewItem]:
    """Full rescan entry point: every NEEDS_MANUAL_REVIEW row in either persisted history file,
    from any source (a run, a migration script, a manual CSV edit), reclassified against one
    combined reference library built from everything already labeled in both files.
    """
    history_csvs = [p for p in (challenge_history_csv, premier_history_csv) if p is not None]
    library = _build_library_from_history_files(history_csvs, cache_dir, log=log)
    if log:
        log(f"[rescan] combined reference library: {len(library)} labeled decks")

    items = rescan_history_for_unresolved(
        challenge_history_csv, cache_dir, "challenge", format_name, rules, challenge_mappings, library, log=log
    )
    if premier_history_csv is not None:
        items += rescan_history_for_unresolved(
            premier_history_csv, cache_dir, "premier", format_name, rules, challenge_mappings, library, log=log
        )
    return items
