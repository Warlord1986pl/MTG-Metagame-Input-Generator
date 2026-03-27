import csv
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List


ALIASES_COLUMNS = ["pattern", "canonical_name", "match_type", "priority"]
ARCHETYPE_COLUMNS = ["pattern", "archetype", "match_type", "priority"]
MAPPING_COLUMNS = ["raw_name", "canonical_name", "archetype"]
ARCHETYPE_CATALOG_COLUMNS = ["archetype"]
DEFAULT_ARCHETYPE_CATALOG = [
    "Aggro",
    "Midrange",
    "Control",
    "Combo",
    "Ramp",
    "Tempo",
    "Prison",
    "Graveyard",
    "Blink",
    "Eldrazi",
    "Energy",
    "Rogue",
    "Unknown",
]


def _norm(text: str) -> str:
    return " ".join(str(text or "").strip().lower().split())


@dataclass
class ApplySummary:
    aliases_upserted: int = 0
    archetypes_upserted: int = 0
    mappings_upserted: int = 0
    backups_created: List[Path] = field(default_factory=list)


@dataclass
class RemoveArchetypeSummary:
    catalog_removed: bool = False
    rules_updated: int = 0
    mappings_updated: int = 0
    backups_created: List[Path] = field(default_factory=list)


@dataclass
class ConfigPaths:
    aliases_file: Path
    archetype_rules_file: Path
    user_mapping_file: Path
    archetype_catalog_file: Path

    @classmethod
    def from_config_dir(cls, config_dir: Path) -> "ConfigPaths":
        return cls(
            aliases_file=config_dir / "deck_aliases.csv",
            archetype_rules_file=config_dir / "archetype_rules.csv",
            user_mapping_file=config_dir / "user_deck_mapping.csv",
            archetype_catalog_file=config_dir / "archetype_catalog.csv",
        )


def load_archetype_catalog(config_dir: Path) -> List[str]:
    paths = ConfigPaths.from_config_dir(config_dir)
    catalog_path = paths.archetype_catalog_file
    if catalog_path.exists():
        rows = _read_rows(catalog_path, ARCHETYPE_CATALOG_COLUMNS)
        values = [str(row.get("archetype") or "").strip() for row in rows]
        return [value for value in values if value]

    discovered: List[str] = []
    seen = set()

    for name in DEFAULT_ARCHETYPE_CATALOG:
        norm = _norm(name)
        if norm and norm not in seen:
            seen.add(norm)
            discovered.append(name)

    for row in _read_rows(paths.archetype_rules_file, ARCHETYPE_COLUMNS):
        name = str(row.get("archetype") or "").strip()
        norm = _norm(name)
        if norm and norm not in seen:
            seen.add(norm)
            discovered.append(name)

    for row in _read_rows(paths.user_mapping_file, MAPPING_COLUMNS):
        name = str(row.get("archetype") or "").strip()
        norm = _norm(name)
        if norm and norm not in seen:
            seen.add(norm)
            discovered.append(name)

    _write_rows(
        catalog_path,
        ARCHETYPE_CATALOG_COLUMNS,
        [{"archetype": name} for name in discovered],
    )
    return discovered


def upsert_archetype_catalog(config_dir: Path, archetype: str) -> List[str]:
    name = str(archetype or "").strip()
    catalog = load_archetype_catalog(config_dir)
    if not name:
        return catalog

    target_norm = _norm(name)
    if any(_norm(existing) == target_norm for existing in catalog):
        return catalog

    catalog.append(name)
    catalog.sort(key=_norm)
    _write_rows(
        ConfigPaths.from_config_dir(config_dir).archetype_catalog_file,
        ARCHETYPE_CATALOG_COLUMNS,
        [{"archetype": value} for value in catalog],
    )
    return catalog


def remove_archetype(
    config_dir: Path,
    archetype: str,
    replacement: str = "Unknown",
    create_backup: bool = True,
) -> RemoveArchetypeSummary:
    target = str(archetype or "").strip()
    replacement_value = str(replacement or "Unknown").strip() or "Unknown"
    summary = RemoveArchetypeSummary()

    if not target:
        return summary

    target_norm = _norm(target)
    if target_norm == _norm("Unknown"):
        return summary

    paths = ConfigPaths.from_config_dir(config_dir)

    catalog_rows = _read_rows(paths.archetype_catalog_file, ARCHETYPE_CATALOG_COLUMNS)
    filtered_catalog = [r for r in catalog_rows if _norm(r.get("archetype", "")) != target_norm]
    if len(filtered_catalog) != len(catalog_rows):
        backup = _backup_if_needed(paths.archetype_catalog_file, create_backup)
        if backup is not None:
            summary.backups_created.append(backup)
        _write_rows(paths.archetype_catalog_file, ARCHETYPE_CATALOG_COLUMNS, filtered_catalog)
        summary.catalog_removed = True

    rules_rows = _read_rows(paths.archetype_rules_file, ARCHETYPE_COLUMNS)
    rules_updated = 0
    for row in rules_rows:
        if _norm(row.get("archetype", "")) == target_norm:
            row["archetype"] = replacement_value
            rules_updated += 1
    if rules_updated > 0:
        backup = _backup_if_needed(paths.archetype_rules_file, create_backup)
        if backup is not None:
            summary.backups_created.append(backup)
        _write_rows(paths.archetype_rules_file, ARCHETYPE_COLUMNS, rules_rows)
    summary.rules_updated = rules_updated

    mapping_rows = _read_rows(paths.user_mapping_file, MAPPING_COLUMNS)
    mappings_updated = 0
    for row in mapping_rows:
        if _norm(row.get("archetype", "")) == target_norm:
            row["archetype"] = replacement_value
            mappings_updated += 1
    if mappings_updated > 0:
        backup = _backup_if_needed(paths.user_mapping_file, create_backup)
        if backup is not None:
            summary.backups_created.append(backup)
        _write_rows(paths.user_mapping_file, MAPPING_COLUMNS, mapping_rows)
    summary.mappings_updated = mappings_updated

    upsert_archetype_catalog(config_dir, replacement_value)
    return summary


@dataclass
class ChangeModel:
    paths: ConfigPaths
    alias_changes: List[Dict[str, str]] = field(default_factory=list)
    archetype_changes: List[Dict[str, str]] = field(default_factory=list)
    mapping_changes: List[Dict[str, str]] = field(default_factory=list)

    @classmethod
    def from_profile_dir(cls, profile_dir: Path) -> "ChangeModel":
        return cls(paths=ConfigPaths.from_config_dir(profile_dir / "configs"))

    def queue_alias(
        self,
        pattern: str,
        canonical_name: str,
        match_type: str = "exact",
        priority: int = 1,
    ) -> None:
        self.alias_changes.append(
            {
                "pattern": pattern.strip(),
                "canonical_name": canonical_name.strip(),
                "match_type": match_type.strip().lower() or "exact",
                "priority": str(int(priority)),
            }
        )

    def queue_archetype_rule(
        self,
        pattern: str,
        archetype: str,
        match_type: str = "exact",
        priority: int = 1,
    ) -> None:
        self.archetype_changes.append(
            {
                "pattern": pattern.strip(),
                "archetype": archetype.strip(),
                "match_type": match_type.strip().lower() or "exact",
                "priority": str(int(priority)),
            }
        )

    def queue_mapping(self, raw_name: str, canonical_name: str, archetype: str) -> None:
        self.mapping_changes.append(
            {
                "raw_name": raw_name.strip(),
                "canonical_name": canonical_name.strip(),
                "archetype": archetype.strip(),
            }
        )

    def has_changes(self) -> bool:
        return bool(self.alias_changes or self.archetype_changes or self.mapping_changes)

    def apply(self, create_backup: bool = True) -> ApplySummary:
        summary = ApplySummary()

        if self.alias_changes:
            backup = _backup_if_needed(self.paths.aliases_file, create_backup)
            if backup is not None:
                summary.backups_created.append(backup)
            summary.aliases_upserted = _apply_alias_changes(self.paths.aliases_file, self.alias_changes)

        if self.archetype_changes:
            backup = _backup_if_needed(self.paths.archetype_rules_file, create_backup)
            if backup is not None:
                summary.backups_created.append(backup)
            summary.archetypes_upserted = _apply_archetype_changes(self.paths.archetype_rules_file, self.archetype_changes)

        if self.mapping_changes:
            backup = _backup_if_needed(self.paths.user_mapping_file, create_backup)
            if backup is not None:
                summary.backups_created.append(backup)
            summary.mappings_upserted = _apply_mapping_changes(self.paths.user_mapping_file, self.mapping_changes)

        self.alias_changes.clear()
        self.archetype_changes.clear()
        self.mapping_changes.clear()
        return summary


def _read_rows(path: Path, columns: List[str]) -> List[Dict[str, str]]:
    if not path.exists():
        return []

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = []
        for row in reader:
            cleaned = {col: str(row.get(col, "") or "").strip() for col in columns}
            if any(cleaned.values()):
                rows.append(cleaned)
        return rows


def _write_rows(path: Path, columns: List[str], rows: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _backup_if_needed(path: Path, create_backup: bool) -> Path | None:
    if not create_backup or not path.exists():
        return None

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = path.with_name(f"{path.name}.bak.{stamp}")
    shutil.copy2(path, backup_path)
    return backup_path


def _apply_alias_changes(path: Path, changes: List[Dict[str, str]]) -> int:
    rows = _read_rows(path, ALIASES_COLUMNS)
    index = {(_norm(r["pattern"]), r["match_type"]): i for i, r in enumerate(rows)}

    upserts = 0
    for change in changes:
        key = (_norm(change["pattern"]), change["match_type"])
        if not key[0] or not change["canonical_name"]:
            continue

        payload = {
            "pattern": change["pattern"],
            "canonical_name": change["canonical_name"],
            "match_type": change["match_type"],
            "priority": str(int(change.get("priority", "1") or "1")),
        }

        if key in index:
            rows[index[key]] = payload
        else:
            index[key] = len(rows)
            rows.append(payload)
        upserts += 1

    rows.sort(key=lambda r: (int(r["priority"] or "100"), _norm(r["pattern"])))
    _write_rows(path, ALIASES_COLUMNS, rows)
    return upserts


def _apply_archetype_changes(path: Path, changes: List[Dict[str, str]]) -> int:
    rows = _read_rows(path, ARCHETYPE_COLUMNS)
    index = {(_norm(r["pattern"]), r["match_type"]): i for i, r in enumerate(rows)}

    upserts = 0
    for change in changes:
        key = (_norm(change["pattern"]), change["match_type"])
        if not key[0] or not change["archetype"]:
            continue

        payload = {
            "pattern": change["pattern"],
            "archetype": change["archetype"],
            "match_type": change["match_type"],
            "priority": str(int(change.get("priority", "1") or "1")),
        }

        if key in index:
            rows[index[key]] = payload
        else:
            index[key] = len(rows)
            rows.append(payload)
        upserts += 1

    rows.sort(key=lambda r: (int(r["priority"] or "100"), _norm(r["pattern"])))
    _write_rows(path, ARCHETYPE_COLUMNS, rows)
    return upserts


def _apply_mapping_changes(path: Path, changes: List[Dict[str, str]]) -> int:
    rows = _read_rows(path, MAPPING_COLUMNS)
    index = {_norm(r["raw_name"]): i for i, r in enumerate(rows)}

    upserts = 0
    for change in changes:
        key = _norm(change["raw_name"])
        if not key or not change["canonical_name"]:
            continue

        payload = {
            "raw_name": change["raw_name"],
            "canonical_name": change["canonical_name"],
            "archetype": change["archetype"],
        }

        if key in index:
            rows[index[key]] = payload
        else:
            index[key] = len(rows)
            rows.append(payload)
        upserts += 1

    rows.sort(key=lambda r: _norm(r["raw_name"]))
    _write_rows(path, MAPPING_COLUMNS, rows)
    return upserts
