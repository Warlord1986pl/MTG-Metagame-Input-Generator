import json
import re
import shutil
import subprocess
import sys
import csv
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from change_model import ChangeModel


REPO_ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = REPO_ROOT / ".preset_cli_state.json"
TEMPLATE_FILES = [
    "archetype_rules.csv",
    "deck_aliases.csv",
    "user_deck_mapping.csv",
]
API_LIMIT = 64
API_BASE = "https://api.videreproject.com"
FORMAT_CANDIDATES = [
    "Modern",
    "Standard",
    "Pioneer",
    "Legacy",
    "Vintage",
    "Pauper",
    "Commander",
    "Duel Commander",
    "Timeless",
    "Explorer",
    "Historic",
    "Brawl",
    "Alchemy",
]


def slugify(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", text.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "preset"


def load_state() -> Dict[str, str]:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: Dict[str, str]) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        return default
    return value if value else default


def ask_choice(prompt: str, options: List[str], default: str) -> str:
    allowed = "/".join(options)
    while True:
        value = ask(f"{prompt} ({allowed})", default).strip()
        if value in options:
            return value
        print(f"Invalid choice. Allowed values: {allowed}")


def api_get(endpoint: str, params: Dict[str, object]) -> Dict[str, object]:
    query = urlencode(params)
    url = f"{API_BASE}/{endpoint}?{query}"
    req = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "MTG-Metagame-Input-Generator/1.0 (+https://github.com)",
        },
    )
    with urlopen(req, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def menu_select_from_list(title: str, items: List[str], default_index: int = 1) -> str:
    print(f"\n{title}")
    for idx, item in enumerate(items, start=1):
        print(f"{idx}. {item}")

    while True:
        choice = ask("Choose number", str(default_index))
        try:
            pos = int(choice)
            if 1 <= pos <= len(items):
                return items[pos - 1]
        except ValueError:
            pass
        print(f"Invalid choice. Enter a number from 1 to {len(items)}.")


def discover_formats(window_days: int = 14) -> List[str]:
    end_date = date.today()
    start_date = end_date - timedelta(days=max(1, window_days - 1))
    available: List[str] = []

    for candidate in FORMAT_CANDIDATES:
        try:
            payload = api_get(
                "metagame",
                {
                    "format": candidate,
                    "min_date": start_date.isoformat(),
                    "max_date": end_date.isoformat(),
                    "limit": API_LIMIT,
                },
            )
        except HTTPError:
            continue
        except URLError:
            continue
        except Exception:
            continue

        data = payload.get("data") or []
        if isinstance(data, list) and data:
            available.append(candidate)

    return available


def fetch_decks_for_format(format_name: str, window_days: int = 14) -> List[str]:
    end_date = date.today()
    start_date = end_date - timedelta(days=max(1, window_days - 1))
    payload = api_get(
        "metagame",
        {
            "format": format_name,
            "min_date": start_date.isoformat(),
            "max_date": end_date.isoformat(),
            "limit": API_LIMIT,
        },
    )

    rows = payload.get("data") or []
    if not isinstance(rows, list):
        return []

    decks: List[str] = []
    seen = set()
    for row in rows:
        name = str(row.get("archetype") or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        decks.append(name)
    return decks


def ask_int(prompt: str, default: int) -> int:
    while True:
        raw = ask(prompt, str(default))
        try:
            return int(raw)
        except ValueError:
            print("Please enter an integer.")


def ask_float(prompt: str, default: float) -> float:
    while True:
        raw = ask(prompt, str(default))
        try:
            return float(raw)
        except ValueError:
            print("Please enter a number.")


def resolve_base_dir() -> Path:
    state = load_state()
    default = state.get("base_dir", str(REPO_ROOT / "workspaces"))
    chosen = Path(ask("Workspace root directory", default)).expanduser().resolve()
    chosen.mkdir(parents=True, exist_ok=True)
    state["base_dir"] = str(chosen)
    save_state(state)
    return chosen


def preset_files(base_dir: Path) -> Path:
    path = base_dir / "presets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def profile_dir(base_dir: Path, preset_name: str) -> Path:
    return base_dir / "profiles" / preset_name


def list_presets(base_dir: Path) -> List[Path]:
    return sorted(preset_files(base_dir).glob("*.json"))


def latest_range_dir(base_path: Path) -> Optional[Path]:
    if not base_path.exists():
        return None
    candidates = [p for p in base_path.iterdir() if p.is_dir() and "_to_" in p.name]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def load_result_rows(run_dir: Path) -> List[Dict[str, str]]:
    csv_path = run_dir / "metagame_input.csv"
    if not csv_path.exists():
        return []

    rows: List[Dict[str, str]] = []
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            deck_name = str(row.get("Deck") or "").strip()
            raw_name = str(
                row.get("Source Deck Names")
                or row.get("Raw Deck")
                or ""
            ).strip() or deck_name
            rows.append(
                {
                    "raw_deck": raw_name,
                    "deck": deck_name,
                    "archetype": str(row.get("Archetype") or "").strip(),
                    "meta": str(row.get("Meta") or "").strip(),
                    "my_wr": str(row.get("My Deck Winrate") or "").strip(),
                }
            )
    return rows


def _fmt(text: str, width: int, align: str = "left") -> str:
    s = str(text)
    if len(s) > width:
        s = s[: width - 1] + "…"
    return s.rjust(width) if align == "right" else s.ljust(width)


def split_source_names(raw_value: str) -> List[str]:
    parts = [part.strip() for part in str(raw_value or "").split("|")]
    out: List[str] = []
    seen = set()
    for part in parts:
        key = part.lower()
        if not part or key in seen:
            continue
        seen.add(key)
        out.append(part)
    return out


def _print_deck_table(rows: List[Dict[str, str]]) -> None:
    COLS = [
        ("#",         "#",          4,  "right"),
        ("deck",      "Deck",       28, "left"),
        ("meta",      "Meta%",       7, "right"),
        ("archetype", "Archetype",  16, "left"),
        ("my_wr",     "My WR",       8, "right"),
        ("raw_deck",  "Raw Name",   28, "left"),
    ]
    SEP = "  "
    print(SEP.join(_fmt(h, w, a) for _, h, w, a in COLS))
    print(SEP.join("-" * w for _, _, w, _ in COLS))
    for idx, item in enumerate(rows, start=1):
        meta_str = item.get("meta", "")
        try:
            meta_str = f"{float(meta_str):.2f}"
        except (ValueError, TypeError):
            pass
        wr_str = item.get("my_wr", "")
        try:
            wr_val = float(wr_str)
            wr_str = f"{wr_val * 100:.1f}%" if wr_val > 0 else "-"
        except (ValueError, TypeError):
            wr_str = "-"
        cells = [
            str(idx),
            item.get("deck", ""),
            meta_str,
            item.get("archetype", ""),
            wr_str,
            item.get("raw_deck", ""),
        ]
        print(SEP.join(_fmt(cell, w, a) for cell, (_, _, w, a) in zip(cells, COLS)))


def run_postprocess_editor(profile_dir_path: Path, run_dir: Path) -> None:
    rows = load_result_rows(run_dir)
    if not rows:
        print("No metagame_input.csv found for editor session.")
        return

    model = ChangeModel.from_profile_dir(profile_dir_path)
    print(f"\nPost-process editor — {run_dir}")
    _print_deck_table(rows)

    while True:
        pending = " [unsaved changes]" if model.has_changes() else ""
        print(f"\nMenu{pending}")
        print("1. Edit deck mapping")
        print("2. Save queued changes")
        print("3. Show table")
        print("0. Exit editor")
        choice = ask_choice("Choose", ["0", "1", "2", "3"], "3")

        if choice == "0":
            if model.has_changes():
                save = ask_choice("Unsaved changes detected. Save now?", ["y", "n"], "y")
                if save == "y":
                    summary = model.apply(create_backup=True)
                    print(
                        f"Saved: aliases={summary.aliases_upserted}, "
                        f"archetypes={summary.archetypes_upserted}, "
                        f"mappings={summary.mappings_upserted}"
                    )
            return

        if choice == "3":
            _print_deck_table(rows)
            continue

        if choice == "2":
            summary = model.apply(create_backup=True)
            print(
                f"Saved: aliases={summary.aliases_upserted}, "
                f"archetypes={summary.archetypes_upserted}, "
                f"mappings={summary.mappings_upserted}"
            )
            continue

        # choice == "1": edit
        selected = ask_int("Row number to edit", 1)
        if selected < 1 or selected > len(rows):
            print("Invalid row number.")
            continue

        item = rows[selected - 1]
        raw_name = item["raw_deck"]
        current_deck = item["deck"]
        current_archetype = item["archetype"]

        print(f"\nEditing row {selected}:")
        print(f"  Raw name:  {raw_name}")
        print(f"  Deck:      {current_deck}")
        print(f"  Archetype: {current_archetype}")

        new_canonical = ask("Canonical deck name", current_deck)
        new_archetype = ask("Archetype", current_archetype)

        source_names = split_source_names(raw_name)
        for source_name in source_names:
            model.queue_mapping(source_name, new_canonical, new_archetype)

        add_alias = ask_choice("Also add exact alias rule raw->canonical?", ["y", "n"], "y")
        if add_alias == "y":
            for source_name in source_names:
                model.queue_alias(source_name, new_canonical, match_type="exact", priority=1)

        add_rule = ask_choice("Also add exact archetype rule canonical->archetype?", ["y", "n"], "y")
        if add_rule == "y":
            model.queue_archetype_rule(new_canonical, new_archetype, match_type="exact", priority=1)

        item["deck"] = new_canonical
        item["archetype"] = new_archetype
        print("Change queued.")
        _print_deck_table(rows)


def open_editor_from_existing_results(base_dir: Path) -> None:
    preset_path = choose_preset_with_results(base_dir)
    if preset_path is None:
        print("No presets with existing result folders found.")
        return
    preset = json.loads(preset_path.read_text(encoding="utf-8"))
    pdir = create_profile_structure(base_dir, preset["name"])

    latest_output = latest_range_dir(pdir / "outputs")
    latest_history = latest_range_dir(pdir / "history")
    candidates = [p for p in [latest_output, latest_history] if p is not None]
    if not candidates:
        print("No existing result folders found for this preset.")
        return

    run_dir = max(candidates, key=lambda p: p.stat().st_mtime)
    print(f"Editor source: {run_dir}")
    run_postprocess_editor(pdir, run_dir)


def latest_run_dir_for_profile(profile_dir_path: Path) -> Optional[Path]:
    latest_output = latest_range_dir(profile_dir_path / "outputs")
    latest_history = latest_range_dir(profile_dir_path / "history")
    candidates = [p for p in [latest_output, latest_history] if p is not None]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def create_profile_structure(base_dir: Path, preset_name: str) -> Path:
    pdir = profile_dir(base_dir, preset_name)
    (pdir / "configs").mkdir(parents=True, exist_ok=True)
    (pdir / "outputs").mkdir(parents=True, exist_ok=True)
    (pdir / "history").mkdir(parents=True, exist_ok=True)

    for name in TEMPLATE_FILES:
        src = REPO_ROOT / "docs" / name
        dst = pdir / "configs" / name
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)

    return pdir


def create_preset(base_dir: Path) -> Path:
    print("\nCreate preset")
    print("Tip: press Enter to accept default value in [brackets].")

    print("Fetching available formats from API...")
    formats = discover_formats(window_days=14)
    if formats:
        format_name = menu_select_from_list("Available formats (last 14 days)", formats, 1)
    else:
        print("Could not fetch format list from API. Falling back to manual input.")
        format_name = ask("Format (e.g. Modern, Pioneer, Standard)", "Modern")

    print(f"Fetching decks for format '{format_name}' (last 14 days)...")
    try:
        decks = fetch_decks_for_format(format_name, window_days=14)
    except Exception:
        decks = []

    if decks:
        my_deck = menu_select_from_list("Available decks", decks, 1)
    else:
        print("Could not fetch deck list from API. Falling back to manual input.")
        my_deck = ask("Your deck (exact deck name)", "Domain Zoo")

    default_name = f"{slugify(format_name)}_{slugify(my_deck)}"
    preset_name = ask("Preset name", default_name)
    preset_path = preset_files(base_dir) / f"{preset_name}.json"

    data = {
        "name": preset_name,
        "format": format_name,
        "my_deck": my_deck,
        "metagame_window_days": ask_int("Metagame window days (integer, usually 7 or 14)", 14),
        "my_window_days": ask_int("My deck primary window days (integer)", 90),
        "my_fallback_window_days": ask_int("My deck fallback window days (integer)", 180),
        "rogue_threshold": ask_float("Rogue threshold % (number, e.g. 0.5)", 0.5),
        "history_points_default": ask_int("Default history points", 4),
    }

    create_profile_structure(base_dir, preset_name)
    preset_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Preset created: {preset_path}")
    return preset_path


def choose_preset(base_dir: Path) -> Path:
    presets = list_presets(base_dir)
    if not presets:
        print("No presets found.")
        return create_preset(base_dir)

    print("\nAvailable presets:")
    for idx, p in enumerate(presets, start=1):
        print(f"{idx}. {p.stem}")
    print("N. Create new preset")

    while True:
        choice = ask("Choose preset number or N", "1")
        if choice.lower() == "n":
            return create_preset(base_dir)
        try:
            selected = presets[int(choice) - 1]
            return selected
        except Exception:
            print("Invalid choice.")


def choose_preset_with_results(base_dir: Path) -> Optional[Path]:
    presets = list_presets(base_dir)
    ready: List[Path] = []

    for preset_path in presets:
        try:
            preset = json.loads(preset_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        pdir = create_profile_structure(base_dir, str(preset.get("name") or preset_path.stem))
        if latest_run_dir_for_profile(pdir) is not None:
            ready.append(preset_path)

    if not ready:
        return None

    print("\nPresets with existing results:")
    for idx, p in enumerate(ready, start=1):
        print(f"{idx}. {p.stem}")

    while True:
        choice = ask("Choose preset number", "1")
        try:
            selected = ready[int(choice) - 1]
            return selected
        except Exception:
            print("Invalid choice.")


def run_preset(base_dir: Path, preset_path: Path) -> None:
    preset = json.loads(preset_path.read_text(encoding="utf-8"))
    preset_name = preset["name"]
    pdir = create_profile_structure(base_dir, preset_name)

    print("\nRun mode")
    print("1. Weekly snapshot")
    print("2. History rebuild")
    mode = ask_choice("Choose mode", ["1", "2"], "1")

    if mode == "2":
        history_points = ask_int(
            "History points (how many past periods to generate)",
            int(preset.get("history_points_default", 4)),
        )
    else:
        history_points = 1

    anchor_sunday = ask("Anchor Sunday YYYY-MM-DD (blank = latest Sunday)", "")

    cmd = [
        sys.executable,
        str(REPO_ROOT / "src" / "metagame_input_generator.py"),
        "--format",
        preset["format"],
        "--history-points",
        str(history_points),
        "--metagame-window-days",
        str(preset["metagame_window_days"]),
        "--my-deck",
        preset["my_deck"],
        "--my-window-days",
        str(preset["my_window_days"]),
        "--my-fallback-window-days",
        str(preset["my_fallback_window_days"]),
        "--rogue-threshold",
        str(preset["rogue_threshold"]),
        "--metagame-limit",
        str(API_LIMIT),
        "--matchup-limit",
        str(API_LIMIT),
        "--rules-file",
        str(pdir / "configs" / "archetype_rules.csv"),
        "--aliases-file",
        str(pdir / "configs" / "deck_aliases.csv"),
        "--user-mapping-file",
        str(pdir / "configs" / "user_deck_mapping.csv"),
        "--output-csv",
        str(pdir / "outputs" / "metagame_input.csv"),
        "--output-xlsx",
        str(pdir / "outputs" / "metagame_input.xlsx"),
        "--output-csv-rogue-grouped",
        str(pdir / "outputs" / "metagame_input_rogue_grouped.csv"),
        "--output-xlsx-rogue-grouped",
        str(pdir / "outputs" / "metagame_input_rogue_grouped.xlsx"),
        "--unknown-output",
        str(pdir / "outputs" / "unknown_archetypes.csv"),
        "--alias-suggestions-output",
        str(pdir / "outputs" / "alias_suggestions.csv"),
        "--history-output-dir",
        str(pdir / "history"),
    ]

    if anchor_sunday:
        cmd.extend(["--anchor-sunday", anchor_sunday])

    print("\nRunning command:")
    print(" ".join(cmd))
    result = subprocess.run(cmd, check=False)

    if result.returncode != 0:
        print("\n[ERROR] API temporary error or run failure. Please try again later.")
        fallback = ask_choice("Open editor on latest existing results instead?", ["y", "n"], "y")
        if fallback == "y":
            run_dir = latest_run_dir_for_profile(pdir)
            if run_dir is None:
                print("No existing result folders found for this preset.")
                fallback_preset = choose_preset_with_results(base_dir)
                if fallback_preset is None:
                    print("No presets with existing result folders found.")
                    return
                fallback_data = json.loads(fallback_preset.read_text(encoding="utf-8"))
                fallback_pdir = create_profile_structure(base_dir, fallback_data["name"])
                run_dir = latest_run_dir_for_profile(fallback_pdir)
                if run_dir is None:
                    print("Could not resolve fallback run directory.")
                    return
                print(f"Editor source: {run_dir}")
                run_postprocess_editor(fallback_pdir, run_dir)
                return
            print(f"Editor source: {run_dir}")
            run_postprocess_editor(pdir, run_dir)
        return

    print("\nDone.")
    print(f"Preset: {preset_name}")
    print(f"Profile folder: {pdir}")

    open_editor = ask_choice("Open post-process editor for latest results?", ["y", "n"], "y")
    if open_editor == "y":
        run_dir = latest_run_dir_for_profile(pdir)
        if run_dir is None:
            print("No run directory found.")
            return
        print(f"Editor source: {run_dir}")
        run_postprocess_editor(pdir, run_dir)


def main() -> None:
    print("MTG Preset Runner")
    base_dir = resolve_base_dir()
    print(f"Workspace root: {base_dir}")
    print("\nLegend:")
    print("- Enter numbers for menu options (e.g. 1, 2, 3, 0).")
    print("- Press Enter to use default values shown in [brackets].")
    print("- For date fields use YYYY-MM-DD (example: 2026-03-15).")
    print("- Use dot for decimals (example: 0.5).")
    print("- Format and deck lists are fetched live from API during preset creation.")

    while True:
        print("\nMenu")
        print("1. Use preset")
        print("2. Create preset")
        print("3. List presets")
        print("4. Edit latest results (no new API run)")
        print("0. Exit")
        choice = ask_choice("Choose", ["0", "1", "2", "3", "4"], "1")

        if choice == "0":
            return
        if choice == "2":
            create_preset(base_dir)
            continue
        if choice == "3":
            presets = list_presets(base_dir)
            if not presets:
                print("No presets found.")
            else:
                for p in presets:
                    print(f"- {p.stem}")
            continue

        if choice == "4":
            open_editor_from_existing_results(base_dir)
            continue

        preset_path = choose_preset(base_dir)
        run_preset(base_dir, preset_path)


if __name__ == "__main__":
    main()
