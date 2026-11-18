"""Full Scryfall card images of the week's new cards (order of 2026-11-18): weekly/<week>/cards/<card>.png, used by
the short renderer of build/ (build_short.py, "NEW CARD ALERT").

For every distinct card name of the sheet new_cards (column Card) of the week's data.xlsx: the card from
``https://api.scryfall.com/cards/named?exact=<name>`` (User-Agent MetagameBreakdown/1.0, Accept application/json, at
least 100 ms between requests, 500 ms before a card lookup: Scryfall answers 429 to faster /cards/named; a 429 waits
its Retry-After, at most 10 s, before the second attempt), its ``image_uris.png`` (a double-faced card without image_uris:
``card_faces[0].image_uris.png``), the file saved byte for byte, never cropped, scaled or re-encoded. Images already in
the local cache ``data/card_images/<scryfall_id>.png`` (gitignored, metadata in ``<scryfall_id>.json``) are copied
from it. Characters a Windows file name cannot hold (``<>:"/\\|?*``) become ``_`` in the file name (``A // B`` ->
``A __ B.png``).

No fallback: a card missing on Scryfall, an HTTP error after 2 attempts, a file that is not a PNG of 745x1040 or
744x1040 or a count of files different from the count of cards is a CardImageError naming the card.
"""
from __future__ import annotations

import json
import re
import shutil
import struct
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pandas as pd

from .common import REPO

API = "https://api.scryfall.com/cards/named"
HEADERS = {"User-Agent": "MetagameBreakdown/1.0", "Accept": "application/json"}
MIN_INTERVAL = 0.1  # seconds between two requests to Scryfall (the order: at least 100 ms)
API_INTERVAL = 0.5  # /cards/named: Scryfall allows about 2 requests per second there (HTTP 429 at 100 ms)
ATTEMPTS = 2
SIZES = ((745, 1040), (744, 1040))
CACHE_DIR = REPO / "data" / "card_images"
NOTES_LINE = "- cards/: full Scryfall card images (png) for cards in new_cards; source: Scryfall"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class CardImageError(Exception):
    pass


def card_names(xlsx: Path) -> List[str]:
    """Distinct card names of the sheet new_cards (column Card), sorted; [] for a sheet without rows."""
    try:
        df = pd.read_excel(xlsx, sheet_name="new_cards", dtype=str)
    except ValueError as e:
        raise CardImageError(f"{xlsx}: brak arkusza new_cards ({e})") from None
    if "Card" not in df.columns:
        if list(df.columns) == ["Info"]:  # "no rows" / no card export configured
            return []
        raise CardImageError(f"{xlsx} new_cards: brak kolumny Card ({list(df.columns)})")
    names = df["Card"].dropna().str.strip()
    if (names == "").any():
        raise CardImageError(f"{xlsx} new_cards: pusta nazwa karty")
    return sorted(set(names))


def file_name(card: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", card) + ".png"


def png_size(path: Path) -> tuple:
    """(width, height) from the PNG header (signature + IHDR); CardImageError for anything else."""
    with path.open("rb") as fh:
        head = fh.read(24)
    if len(head) < 24 or head[:8] != PNG_SIGNATURE or head[12:16] != b"IHDR":
        raise CardImageError(f"{path.name}: to nie jest plik PNG")
    return struct.unpack(">II", head[16:24])


class _Client:
    def __init__(self, get: Callable, sleep: Callable[[float], None], clock: Callable[[], float]):
        self.get, self.sleep, self.clock = get, sleep, clock
        self.last: Optional[float] = None
        self.requests = 0

    def fetch(self, url: str, card: str, params: Optional[dict] = None, what: str = "dane"):
        """The response of a GET with status 200, at most ATTEMPTS attempts; a 404 of the card lookup is final."""
        err = ""
        backoff = 0.0
        for attempt in range(1, ATTEMPTS + 1):
            if self.last is not None:
                wait = max(API_INTERVAL if params is not None else MIN_INTERVAL, backoff) - (self.clock() - self.last)
                if wait > 0:
                    self.sleep(wait)
            try:
                r = self.get(url, params=params, headers=HEADERS, timeout=60)
            except Exception as e:  # noqa: BLE001 -- requests.RequestException and the like: one more attempt
                err = f"{type(e).__name__}: {e}"
                r = None
            finally:
                self.last = self.clock()
                self.requests += 1
            if r is not None:
                if r.status_code == 200:
                    return r
                if r.status_code == 404 and params is not None:
                    raise CardImageError(f"karta {card!r}: brak w Scryfallu (HTTP 404, {url}?exact=...)")
                err = f"HTTP {r.status_code}"
                if r.status_code == 429:
                    try:
                        backoff = min(10.0, float((getattr(r, "headers", None) or {}).get("Retry-After", 1)))
                    except ValueError:
                        backoff = 1.0
        raise CardImageError(f"karta {card!r}: {what} ze Scryfalla - błąd po {ATTEMPTS} próbach ({err})")


def _image_url(card: str, data: dict) -> str:
    uris = data.get("image_uris")
    if uris is None and data.get("card_faces"):
        uris = data["card_faces"][0].get("image_uris")
    url = (uris or {}).get("png")
    if not url:
        raise CardImageError(f"karta {card!r}: brak image_uris.png ani card_faces[0].image_uris.png w Scryfallu")
    return url


def fetch_card_images(names: List[str], dest: Path, cache_dir: Path = CACHE_DIR, get: Optional[Callable] = None,
                      sleep: Callable[[float], None] = time.sleep,
                      clock: Callable[[], float] = time.monotonic) -> Dict[str, object]:
    """*dest* (created, must be empty) gets one PNG per card of *names*. Returns {"cards": [...], "downloaded": n,
    "cached": n}; every card record: card, file, scryfall_id, set, artist, source (download / cache), width, height."""
    if get is None:
        import requests

        get = requests.get
    files = [file_name(n) for n in names]
    if len(set(f.lower() for f in files)) != len(files):
        raise CardImageError(f"dwie karty dają tę samą nazwę pliku: {sorted(files)}")
    dest.mkdir(parents=True, exist_ok=True)
    if any(dest.iterdir()):
        raise CardImageError(f"{dest} nie jest pusty")
    cache_dir.mkdir(parents=True, exist_ok=True)
    client = _Client(get, sleep, clock)
    out: List[dict] = []
    for card, fname in zip(names, files):
        data = client.fetch(API, card, params={"exact": card}).json()
        sid = data.get("id")
        if not sid or not re.fullmatch(r"[0-9a-f-]{36}", sid):
            raise CardImageError(f"karta {card!r}: Scryfall bez poprawnego id ({sid!r})")
        cached = cache_dir / f"{sid}.png"
        source = "cache"
        if not cached.exists():
            body = client.fetch(_image_url(card, data), card, what="obraz").content
            tmp = cached.with_suffix(".part")
            tmp.write_bytes(body)
            try:
                size = png_size(tmp)
            except CardImageError as e:
                tmp.unlink()
                raise CardImageError(f"karta {card!r}: {e}") from None
            if size not in SIZES:
                tmp.unlink()
                raise CardImageError(f"karta {card!r}: obraz {size[0]}x{size[1]}, oczekiwany 745x1040 albo 744x1040")
            tmp.replace(cached)
            source = "download"
        meta = {"name": data.get("name"), "set": data.get("set"), "artist": data.get("artist")
                or ((data.get("card_faces") or [{}])[0].get("artist")), "scryfall_id": sid,
                "png": _image_url(card, data)}
        (cache_dir / f"{sid}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        target = dest / fname
        shutil.copyfile(cached, target)  # byte copy, no re-encoding
        try:
            w, h = png_size(target)
        except CardImageError as e:
            raise CardImageError(f"karta {card!r}: {e} (cache {cached})") from None
        if (w, h) not in SIZES:
            raise CardImageError(f"karta {card!r}: obraz {w}x{h} (cache {cached}), oczekiwany 745x1040 albo 744x1040")
        out.append({"card": card, "file": fname, "scryfall_id": sid, "set": meta["set"], "artist": meta["artist"],
                    "source": source, "width": w, "height": h})
    check_cards_dir(dest, names)
    return {"cards": out, "downloaded": sum(c["source"] == "download" for c in out),
            "cached": sum(c["source"] == "cache" for c in out), "requests": client.requests}


def check_cards_dir(folder: Path, names: List[str]) -> None:
    """Files in *folder* = one PNG per card of *names* (count and names), each 745x1040 or 744x1040."""
    got = sorted(p.name for p in folder.iterdir()) if folder.exists() else []
    want = sorted(file_name(n) for n in names)
    if got != want:
        extra, missing = sorted(set(got) - set(want)), sorted(set(want) - set(got))
        raise CardImageError(f"{folder}: {len(got)} plików, kart w new_cards {len(want)} (brak {missing[:5]}, "
                             f"nadmiarowe {extra[:5]})")
    for name in got:
        size = png_size(folder / name)
        if size not in SIZES:
            raise CardImageError(f"{folder / name}: {size[0]}x{size[1]}, oczekiwany 745x1040 albo 744x1040")


def add_notes_line(notes: Path) -> None:
    """The cards/ line at the end of the file list of the Files section of notes.md (before 'Sheets of')."""
    lines = notes.read_text(encoding="utf-8").split("\n")
    if NOTES_LINE in lines:
        return
    try:
        files = lines.index("## Files")
        sheets = next(i for i in range(files, len(lines)) if lines[i].startswith("Sheets of `data.xlsx`"))
    except (ValueError, StopIteration):
        raise CardImageError(f"{notes}: brak sekcji '## Files' z linią 'Sheets of `data.xlsx`'") from None
    at = sheets - 1 if lines[sheets - 1] == "" else sheets
    lines.insert(at, NOTES_LINE)
    notes.write_text("\n".join(lines), encoding="utf-8")
