"""Explicit daily/export storage for the read-only OneNote source."""

import json
import re
from datetime import datetime
from pathlib import Path

import yaml

from .._version import __version__
from ..rem.files import atomic_write
from .adapter import AdapterResult, append_daily_jsonl, validate_record
from .onenote_scope import source_config


def prepare_import(source_dir: Path, mode: str, export_id: str | None) -> Path:
    """Validate the destination before any Graph access or filesystem writes."""
    if mode not in {"daily", "export"}:
        raise ValueError("Import mode must be daily or export.")
    if mode == "daily":
        if export_id is not None:
            raise ValueError("--export-id is only valid with --mode export.")
        today = datetime.now().date()
        return source_dir / "daily" / f"{today.year:04d}" / f"{today.month:02d}" / f"{today.day:02d}.jsonl"
    if not export_id or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", export_id):
        raise ValueError("Export requires --export-id with 1-80 letters, digits, underscores or hyphens.")
    directory = source_dir / "exports" / export_id
    if directory.exists():
        raise ValueError("Export ID already exists. Choose another --export-id; existing exports are preserved.")
    return directory / "records.jsonl"


def write_import(result: AdapterResult, source_dir: Path, target: Path, scope: dict,
                 mode: str, export_id: str | None, command: str) -> dict:
    """Save the shared record contract, selected scope and validation provenance."""
    for record in result.records:
        record["extensions"]["ingestion"] = {"mode": mode, "path": target.relative_to(source_dir).as_posix()}
        problems = validate_record(record)
        if problems:
            raise ValueError("Invalid shared record before storage: " + "; ".join(problems))
    account = result.records[0]["account"]
    config = source_dir / "source.yaml"
    doc = source_config(source_dir, account, scope, command)
    now = doc["validated_with"]["timestamp"]
    if mode == "daily":
        # Pre-contract imports used page IDs as the deduplication key. Never
        # silently mix those rows with the global IDs expected by consumers.
        if target.exists():
            for lineno, line in enumerate(target.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    existing = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError(f"Existing JSONL is corrupted at line {lineno}; preserved without appending.") from error
                if not isinstance(existing, dict):
                    raise ValueError(f"Existing JSONL is corrupted at line {lineno}; preserved without appending.")
                if validate_record(existing):
                    raise ValueError("Existing JSONL contains an incompatible or corrupted record; "
                                     "preserved without appending. Use a new --output directory for "
                                     "the shared record format; automatic migration is not implemented.")
        # Keep classification and actual file consistent if collection crosses midnight.
        date = datetime.strptime(target.relative_to(source_dir).as_posix(), "daily/%Y/%m/%d.jsonl").date()
        target, written = append_daily_jsonl(result.records, source_dir, date=date)
        stats = {"written": written, "skipped": len(result.records) - written}
    else:
        # mkdir is exclusive so a concurrent export cannot overwrite an existing ID.
        target.parent.mkdir(parents=True, exist_ok=False)
        atomic_write(target, "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in result.records))
        manifest = {"source": "onenote", "account": account, "scope": scope, "export_id": export_id,
                    "created": now, "records": len(result.records), "errors": result.errors,
                    "media_policy": "metadata_only", "connectonion_version": __version__}
        atomic_write(target.parent / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        stats = {"written": len(result.records), "skipped": 0}
    atomic_write(config, yaml.safe_dump(doc, allow_unicode=True, sort_keys=False))
    return {"records": len(result.records), **stats, "errors": result.errors, "jsonl": str(target),
            "source_yaml": str(config), "mode": mode, "registration": "external_import_only",
            "note": ("Same-day IDs already stored are skipped, including changed pages; incremental refresh is not implemented."
                     if stats["skipped"] else "")}
