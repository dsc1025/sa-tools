"""Locate versioned Stone Age resource files in a selected client data folder."""
from __future__ import annotations

from pathlib import Path


def resource_file(data: Path, prefix: str) -> Path:
    matches = sorted(data.glob(f"{prefix}*.bin"), key=lambda path: path.name.lower())
    if not matches:
        raise FileNotFoundError(f"No {prefix}*.bin found in {data}")
    if len(matches) > 1:
        names = ", ".join(path.name for path in matches)
        raise ValueError(f"More than one {prefix} file found; select a data folder with one active resource set: {names}")
    return matches[0]


def resources(data: Path) -> dict[str, Path]:
    data = data.resolve()
    if not data.is_dir():
        raise NotADirectoryError(f"Not a data directory: {data}")
    return {
        "spradrn": resource_file(data, "spradrn_"),
        "spr": resource_file(data, "spr_"),
        "adrn": resource_file(data, "adrn_"),
        "real": resource_file(data, "real_"),
    }
