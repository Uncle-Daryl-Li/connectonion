"""Selected-scope configuration for external read-only OneNote imports."""
from datetime import datetime, timezone
from pathlib import Path
import yaml
from .._version import __version__


def source_config(source_dir: Path, account: str, scope: dict, command: str) -> dict:
    """Build source.yaml content, preserving existing account/scope ownership."""
    config = source_dir / "source.yaml"
    if config.exists():
        saved = yaml.safe_load(config.read_text(encoding="utf-8"))
        if not isinstance(saved, dict) or saved.get("account") != account or saved.get("scope") != scope:
            raise ValueError("This source directory belongs to another account or scope. Use a separate --output directory.")
    now = datetime.now(timezone.utc).isoformat()
    doc = {"driver": "onenote", "account": account, "scope": scope, "retention": "90d",
           "media_policy": "metadata_only", "read_only": True,
           "validated_with": {"connectonion_version": __version__, "command": command,
                              "path": str(config.resolve()), "timestamp": now},
           "registration": "external_import_only"}
    return doc
